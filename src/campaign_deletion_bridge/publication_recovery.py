"""Account-owned recovery through publication; never creates/replaces an aggregate."""
from shared_campaign_locators.core import paired_delete_actions,locator_for_target,get_owned_locator
from shared_campaign_locators.publication import phase,verify_aggregate
from progress import get,key,wire,integer,require


def exact(table,row,kind='ConditionCheck'):
    fields=[(k,v) for k,v in row.items() if k not in ('PK','SK')]
    return {kind:{'TableName':table,'Key':key(row['PK'],row['SK']),
        'ConditionExpression':' AND '.join(f'#f{i} = :f{i}' for i in range(len(fields))),
        'ExpressionAttributeNames':{f'#f{i}':k for i,(k,_) in enumerate(fields)},
        'ExpressionAttributeValues':wire({f':f{i}':v for i,(_,v) in enumerate(fields)})}}

def absent(table,pk,sk):
    return {'ConditionCheck':{'TableName':table,'Key':key(pk,sk),'ConditionExpression':'attribute_not_exists(PK)'}}

def evidence(ddb,table,intelligence,summary,environment,now):
    require(type(intelligence) is str and bool(intelligence))
    state=phase(summary,now)
    require(environment in ('dev','uat','prod'))
    inventory=get(ddb,table,'INVENTORY#'+environment,'CAMPAIGN_LOCATORS')
    require(type(inventory) is dict and inventory.get('revision')==summary['lifecycleInventoryRevision'])
    from uuid import UUID
    try:
        ident=UUID(summary.get('candidateId'))
        require(ident.version==4 and str(ident)==summary['candidateId'])
    except (ValueError,TypeError,AttributeError):require(False)
    require(summary.get('PK')=='CANDIDATE#'+summary['candidateId'] and summary.get('SK')=='SUMMARY')
    integer(summary.get('periodId'));integer(summary.get('expiresAt'),1)
    aggregate=get(ddb,intelligence,'CAMPAIGN#'+summary['candidateId'],'AGGREGATE')
    if state in ('FROZEN','REPAIRING'):require(aggregate is None)
    else:verify_aggregate(summary,aggregate)
    proof=absent(intelligence,'CAMPAIGN#'+summary['candidateId'],'AGGREGATE') if aggregate is None else exact(intelligence,aggregate)
    return state,proof

def delete_contribution_guard(ddb,table,intelligence,summary,environment,now):
    state,proof=evidence(ddb,table,intelligence,summary,environment,now)
    if state in ('PUBLISHED','SUPPRESSED'):return [exact(table,summary),proof]
    # Preserve original publication operation/start and thus aggregate deadline.
    action=exact(table,summary,'Update')
    action['Update'].update(UpdateExpression='SET #version = :next, lifecycleState = :repairing')
    action['Update']['ExpressionAttributeNames']['#version']='version'
    action['Update']['ExpressionAttributeValues'].update(wire({':next':integer(summary['version'],1)+1,':repairing':'REPAIRING'}))
    return [action,proof]

def published_cleanup(ddb,table,intelligence,summary,environment,now):
    state,proof=evidence(ddb,table,intelligence,summary,environment,now)
    require(state in ('PUBLISHED','SUPPRESSED'))
    page=ddb.query(TableName=table,KeyConditionExpression='PK = :pk AND begins_with(SK, :prefix)',
        ExpressionAttributeValues=wire({':pk':summary['PK'],':prefix':'CONTRIB#'}),ConsistentRead=True,Limit=10)
    from progress import plain
    rows=[plain(v) for v in page.get('Items',[])];require(len(rows)<=10)
    for row in rows:
        require(row.get('PK')==summary['PK'] and row.get('periodId')==summary['periodId'])
        locator=get_owned_locator(ddb,table,locator_for_target(row,environment))
        ddb.transact_write_items(TransactItems=[exact(table,summary),proof,*paired_delete_actions(table,locator)])
    if rows or page.get('LastEvaluatedKey'):return False
    # Repair metadata cannot silently survive SUMMARY retirement.
    require(get(ddb,table,summary['PK'],'DELETION_RECOMPUTE') is None)
    ddb.transact_write_items(TransactItems=[exact(table,summary,'Delete'),proof,absent(table,summary['PK'],'DELETION_RECOMPUTE')])
    return True
