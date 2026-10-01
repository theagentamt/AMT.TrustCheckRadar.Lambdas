"""Exact same-table locators. Logical expiry is never independent locator TTL."""
from decimal import Decimal
import re
from uuid import UUID

WRITERS = ['publisher','cluster','deletion_bridge','lifecycle']
INVENTORY_FIELDS = {'PK','SK','recordType','schemaVersion','revision','environment','coverage',
    'manifestSha256','approvedAtEpoch','locatorSchemaVersion','minimumPeriodId','priorPeriodsErased','writers'}
LOCATOR_FIELDS = {'PK','SK','recordType','schemaVersion','environment','periodId','targetKind',
    'targetPK','targetSK','targetExpiresAtEpoch','GSI3PK','GSI3SK'}


class LocatorUnavailable(RuntimeError):
    def __init__(self):
        super().__init__('Campaign locator coverage is unavailable')


def require(condition):
    if not condition:
        raise LocatorUnavailable()


def integer(value, minimum=0):
    require(type(value) in (int,Decimal) and value >= minimum and value == int(value))
    return int(value)


def _one(value):
    if type(value) is str: return {'S':value}
    if type(value) is bool: return {'BOOL':value}
    if type(value) in (int,Decimal): return {'N':str(value)}
    if type(value) is list: return {'L':[_one(v) for v in value]}
    if type(value) is dict: return {'M':serialize(value)}
    raise LocatorUnavailable()


def serialize(value):
    return {k:_one(v) for k,v in value.items()}


def deserialize(value):
    def one(item):
        require(type(item) is dict and len(item)==1)
        kind,raw=next(iter(item.items()))
        if kind in ('S','BOOL'): return raw
        if kind=='N': return Decimal(raw)
        if kind=='L': return [one(v) for v in raw]
        if kind=='M': return deserialize(raw)
        raise LocatorUnavailable()
    return {k:one(v) for k,v in value.items()}


def _key(pk,sk):
    return serialize({'PK':pk,'SK':sk})


def _exact(value):
    fields=[(k,v) for k,v in value.items() if k not in ('PK','SK')]
    return {'ConditionExpression':' AND '.join(f'#f{i} = :f{i}' for i in range(len(fields))),
            'ExpressionAttributeNames':{f'#f{i}':k for i,(k,_) in enumerate(fields)},
            'ExpressionAttributeValues':serialize({f':f{i}':v for i,(_,v) in enumerate(fields)})}


def load_inventory(client, table, environment, manifest_sha256, revision, now_epoch):
    require(environment in ('dev','uat','prod') and type(manifest_sha256) is str
            and re.fullmatch('[0-9a-f]{64}',manifest_sha256) is not None)
    integer(revision,1)
    raw=client.get_item(TableName=table,Key=_key('INVENTORY#'+environment,'CAMPAIGN_LOCATORS'),ConsistentRead=True).get('Item')
    require(raw is not None)
    item=deserialize(raw)
    require(set(item)==INVENTORY_FIELDS and item['PK']=='INVENTORY#'+environment
            and item['SK']=='CAMPAIGN_LOCATORS' and item['recordType']=='CAMPAIGN_LOCATOR_INVENTORY'
            and integer(item['schemaVersion'])==1 and integer(item['locatorSchemaVersion'])==1
            and integer(item['revision'],1)==revision and item['environment']==environment
            and item['coverage']=='VERIFIED_COMPLETE' and item['manifestSha256']==manifest_sha256
            and integer(item['approvedAtEpoch'],1)<now_epoch and item['priorPeriodsErased'] is True
            and item['writers']==WRITERS)
    integer(item['minimumPeriodId'])
    return item


def inventory_condition(table, inventory):
    return {'ConditionCheck':{'TableName':table,'Key':_key(inventory['PK'],inventory['SK']),**_exact(inventory)}}


def validate_locator(value, environment, partition=None):
    require(type(value) is dict and set(value)==LOCATOR_FIELDS and environment in ('dev','uat','prod'))
    require(value['recordType']=='CAMPAIGN_CONTRIBUTOR_LOCATOR' and integer(value['schemaVersion'])==1
            and value['environment']==environment)
    period=integer(value['periodId'])
    pk=value['PK']
    require(type(pk) is str and re.fullmatch(r'CONTRIB#(0|[1-9][0-9]*)#[A-Za-z0-9_-]{43}',pk) is not None
            and pk.split('#')[1]==str(period) and (partition is None or pk==partition))
    token=pk.split('#')[2]
    kind=value['targetKind']
    require(kind in ('FEATURE','CONTRIBUTION'))
    prefix='EVENT#' if kind=='FEATURE' else 'CANDIDATE#'
    target=value['targetPK']
    require(type(target) is str and target.startswith(prefix))
    identifier=target[len(prefix):]
    try:
        parsed=UUID(identifier)
        require(parsed.version==4 and str(parsed)==identifier)
    except (ValueError,TypeError,AttributeError):
        raise LocatorUnavailable() from None
    require(value['SK']=='LOCATOR#'+target and value['targetSK']==('FEATURE' if kind=='FEATURE' else 'CONTRIB#'+token))
    expiry=integer(value['targetExpiresAtEpoch'],1)
    require(value['GSI3PK']=='EXPIRY#'+environment and integer(value['GSI3SK'],1)==expiry)
    return dict(value)


def locator_for_target(row, environment):
    require(type(row) is dict and row.get('environment',environment)==environment)
    pk,sk=row.get('PK'),row.get('SK')
    if type(pk) is str and pk.startswith('EVENT#') and sk=='FEATURE':
        kind='FEATURE'
    elif type(pk) is str and pk.startswith('CANDIDATE#') and type(sk) is str and sk.startswith('CONTRIB#'):
        kind='CONTRIBUTION'
    else:
        raise LocatorUnavailable()
    value={'PK':row.get('GSI1PK'),'SK':'LOCATOR#'+pk,'recordType':'CAMPAIGN_CONTRIBUTOR_LOCATOR',
        'schemaVersion':1,'environment':environment,'periodId':row.get('periodId'),'targetKind':kind,
        'targetPK':pk,'targetSK':sk,'targetExpiresAtEpoch':row.get('expiresAt'),
        'GSI3PK':'EXPIRY#'+environment,'GSI3SK':row.get('expiresAt')}
    return validate_locator(value,environment)


def get_owned_locator(client, table, locator):
    expected=validate_locator(locator,locator['environment'])
    raw=client.get_item(TableName=table,Key=_key(locator['PK'],locator['SK']),ConsistentRead=True).get('Item')
    require(raw is not None)
    actual=validate_locator(deserialize(raw),locator['environment'],locator['PK'])
    require(actual==expected)
    return actual


def locator_put(table, locator):
    value=validate_locator(locator,locator['environment'])
    return {'Put':{'TableName':table,'Item':serialize(value),'ConditionExpression':'attribute_not_exists(PK) AND attribute_not_exists(SK)'}}


def locator_condition(table, locator):
    value=validate_locator(locator,locator['environment'])
    return {'ConditionCheck':{'TableName':table,'Key':_key(value['PK'],value['SK']),**_exact(value)}}


def locator_pointer(locator):
    value=validate_locator(locator,locator['environment'])
    return {'locatorPK':value['PK'],'locatorSK':value['SK'],'targetExpiresAtEpoch':integer(value['targetExpiresAtEpoch'],1)}


def paired_delete_actions(table, locator):
    value=validate_locator(locator,locator['environment'])
    actions=[{'Delete':{'TableName':table,'Key':_key(value['targetPK'],value['targetSK']),
        'ConditionExpression':'attribute_not_exists(PK) OR (GSI1PK = :owner AND expiresAt = :expiry AND periodId = :period)',
        'ExpressionAttributeValues':serialize({':owner':value['PK'],':expiry':value['targetExpiresAtEpoch'],':period':value['periodId']})}}]
    if value['targetKind']=='FEATURE':
        for sk in ('DEDUPE','CLUSTERED'):
            actions.append({'Delete':{'TableName':table,'Key':_key(value['targetPK'],sk),
                'ConditionExpression':'attribute_not_exists(PK) OR (locatorPK = :owner AND locatorSK = :locator AND targetExpiresAtEpoch = :expiry AND expiresAt <= :expiry)',
                'ExpressionAttributeValues':serialize({':owner':value['PK'],':locator':value['SK'],':expiry':value['targetExpiresAtEpoch']})}})
    actions.append({'Delete':{'TableName':table,'Key':_key(value['PK'],value['SK']),**_exact(value)}})
    return actions


def owned_page(client, table, environment, partition, cursor=None, limit=25):
    require(type(limit) is int and 1<=limit<=25 and type(partition) is str
            and re.fullmatch(r'CONTRIB#(0|[1-9][0-9]*)#[A-Za-z0-9_-]{43}',partition) is not None)
    args={'TableName':table,'KeyConditionExpression':'PK = :pk AND begins_with(SK, :prefix)',
          'ExpressionAttributeValues':serialize({':pk':partition,':prefix':'LOCATOR#'}),'ConsistentRead':True,'Limit':limit,'ScanIndexForward':True}
    if cursor is not None:
        require(type(cursor) is str and cursor.startswith('LOCATOR#') and len(cursor)<=2048)
        args['ExclusiveStartKey']=_key(partition,cursor)
    page=client.query(**args)
    items=[validate_locator(deserialize(v),environment,partition) for v in page.get('Items',[])]
    require(len(items)<=limit)
    last=deserialize(page['LastEvaluatedKey']) if page.get('LastEvaluatedKey') else None
    if last is not None:
        require(set(last)=={'PK','SK'} and last['PK']==partition and type(last['SK']) is str and last['SK'].startswith('LOCATOR#'))
    return items,last['SK'] if last else None


def get_locator_by_pointer(client, table, environment, row):
    """Resolve an expiration sibling after its primary FEATURE was removed by TTL."""
    pk,sk=row.get('locatorPK'),row.get('locatorSK')
    require(type(pk) is str and type(sk) is str)
    raw=client.get_item(TableName=table,Key=_key(pk,sk),ConsistentRead=True).get('Item')
    require(raw is not None)
    value=validate_locator(deserialize(raw),environment,pk)
    require(value['SK']==sk and value['targetPK']==row.get('PK')
            and value['targetKind']=='FEATURE' and row.get('SK') in ('DEDUPE','CLUSTERED')
            and integer(row.get('targetExpiresAtEpoch'),1)==value['targetExpiresAtEpoch']
            and integer(row.get('expiresAt'),1)<=value['targetExpiresAtEpoch'])
    return value


class InventoryGuardedClient:
    def __init__(self, client, table, inventory):
        self.client,self.table,self.inventory=client,table,inventory
    def __getattr__(self, name):
        return getattr(self.client,name)
    def transact_write_items(self, **kwargs):
        return self.client.transact_write_items(**(kwargs|{'TransactItems':[
            inventory_condition(self.table,self.inventory),*kwargs['TransactItems']]}))
    def update_item(self, **kwargs):
        return self.transact_write_items(TransactItems=[{'Update':kwargs}])
