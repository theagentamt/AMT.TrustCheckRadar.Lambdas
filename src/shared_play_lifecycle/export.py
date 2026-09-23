"""Account-owned billing metadata only; no KMS or raw credential exposure."""
from shared_check_authority.core import AuthorityError,integral
from .tokens import validate,key,public_projection
from .bindings import validate_pair,reverse_key


def page(authority,table,account,partition,position):
    if partition not in {authority._partition(account,kid) for kid in authority.s.hmac_keys}:raise AuthorityError('PLAY_TOKEN_INVENTORY_UNAVAILABLE')
    args={'KeyConditionExpression':'PK = :pk','ExpressionAttributeValues':{':pk':partition},'ConsistentRead':True,'Limit':25}
    if position is not None:
        if not isinstance(position,str) or not (position=='PLAY_BINDING' or position.startswith('PLAY_TOKEN#')):raise AuthorityError('PLAY_TOKEN_INVENTORY_UNAVAILABLE')
        args['ExclusiveStartKey']={'PK':partition,'SK':position}
    result=authority.ddb.Table(table).query(**args);items=[]
    for row in result.get('Items',[]):
        if row.get('PK')!=partition:raise AuthorityError('PLAY_TOKEN_INVENTORY_UNAVAILABLE')
        if row.get('SK')=='PLAY_BINDING':
            reverse=authority._get(table,reverse_key(row.get('bindingHash')))
            validate_pair(reverse,row,account=account,partition=partition)
            items.append({'kind':'google_play_preparation','platform':'google_play','preparedAtEpoch':int(row['createdAtEpoch'])})
        else:
            validate(row,key(partition,row.get('tokenDigest')),authority.now(),allow_expired=True)
            if authority.now()<row['expiresAt']:items.append(public_projection(row,authority.now()))
    cursor=result.get('LastEvaluatedKey')
    if cursor is not None and (set(cursor)!={'PK','SK'} or cursor['PK']!=partition or not isinstance(cursor['SK'],str)):raise AuthorityError('PLAY_TOKEN_INVENTORY_UNAVAILABLE')
    return items,cursor['SK'] if cursor else None
