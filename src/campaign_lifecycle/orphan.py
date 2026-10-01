"""Bounded expired orphan cleanup; never period completion or key retirement."""
from decimal import Decimal
from uuid import UUID
from boto3.dynamodb.types import TypeSerializer,TypeDeserializer
from shared_campaign_contracts.metadata import validate_metadata
from shared_campaign_contracts.app_features import LANGUAGE_IDS
from shared_research_consent import CURRENT_NOTICE,CURRENT_POLICY

SERIALIZER=TypeSerializer();DESERIALIZER=TypeDeserializer()
def plain(row):return {k:DESERIALIZER.deserialize(v) for k,v in row.items()}
def wire(row):return {k:SERIALIZER.serialize(v) for k,v in row.items()}
from shared_campaign_contracts import repair
from shared_campaign_locators import core,period as fence

PAGE_SIZE=25
MAX_PAGES=4
MAX_PAIRS=10


def recover(worker,candidate_id,period):
    worker.require_enabled()
    try:
        parsed=UUID(candidate_id)
        core.require(parsed.version==4 and str(parsed)==candidate_id)
    except (ValueError,TypeError,AttributeError):core.require(False)
    period=core.integer(period);now=worker.now();core.integer(now,1)
    core.require(now>=(period+1)*fence.PERIOD_SECONDS+fence.RECOVERY_SECONDS)
    def budget():core.require(worker.remaining()>=6000)
    def get(pk,sk):
        budget();raw=worker.d.get_item(TableName=worker.pipeline,Key=core._key(pk,sk),ConsistentRead=True).get('Item')
        return plain(raw) if raw else None
    def absent(pk,sk):return {'ConditionCheck':{'TableName':worker.pipeline,'Key':core._key(pk,sk),'ConditionExpression':'attribute_not_exists(PK)'}}
    def exact(row,kind='ConditionCheck'):
        fields=[(k,v) for k,v in row.items() if k not in ('PK','SK')]
        return {kind:{'TableName':worker.pipeline,'Key':core._key(row['PK'],row['SK']),
            'ConditionExpression':' AND '.join(f'#f{i} = :f{i}' for i in range(len(fields))),
            'ExpressionAttributeNames':{f'#f{i}':k for i,(k,_) in enumerate(fields)},
            'ExpressionAttributeValues':wire({f':f{i}':v for i,(_,v) in enumerate(fields)})}}
    budget();inventory=core.load_inventory(worker.d,worker.pipeline,worker.env,worker.manifest,worker.revision,now)
    core.require(period>=inventory['minimumPeriodId'])
    budget();registry=fence.read(worker.d,worker.pipeline,period,worker.manifest,worker.revision,now,states=('CLOSING',))
    pk='CANDIDATE#'+candidate_id
    core.require(get(pk,'SUMMARY') is None)
    checkpoint=get(pk,'DELETION_RECOMPUTE')
    if checkpoint is not None:repair.validate_orphan(checkpoint,pk,period,worker.env,now,expired=True)
    # Read the complete bounded base partition before mutation. Unknown/live or
    # mixed-period evidence is preserved; a truncated traversal is never proof.
    rows=[];cursor=None;seen=set()
    for _ in range(MAX_PAGES):
        budget();args={'TableName':worker.pipeline,'KeyConditionExpression':'PK = :pk',
            'ExpressionAttributeValues':core.serialize({':pk':pk}),'ConsistentRead':True,'Limit':PAGE_SIZE}
        if cursor is not None:args['ExclusiveStartKey']=cursor
        page=worker.d.query(**args);part=[plain(v) for v in page.get('Items',[])]
        core.require(len(part)<=PAGE_SIZE and all(row.get('PK')==pk for row in part))
        rows.extend(part);cursor=page.get('LastEvaluatedKey')
        if not cursor:break
        last=plain(cursor)
        core.require(set(last)=={'PK','SK'} and last['PK']==pk and type(last['SK']) is str and len(last['SK'])<=2048
                     and last['SK'] not in seen and bool(part) and part[-1].get('SK')==last['SK'])
        seen.add(last['SK'])
    core.require(not cursor)
    locators=[]
    for row in rows:
        if row.get('SK')=='DELETION_RECOMPUTE':
            core.require(row==checkpoint);continue
        core.require(type(row.get('SK')) is str and row['SK'].startswith('CONTRIB#')
                     and core.integer(row.get('periodId'))==period and core.integer(row.get('expiresAt'),1)<=now)
        expected={'PK','SK','GSI1PK','GSI1SK','periodId','submissionCount','vectorApplied','vector','languageId',
            'researchNoticeVersion','researchPolicyVersion','metadataSchemaVersion','lexicalFingerprint','signalIds','indicatorIds',
            'GSI3PK','GSI3SK','expiresAt'}
        core.require(set(row)==expected and row['GSI1SK']==pk and row['GSI3PK']=='EXPIRY#'+worker.env
                     and row['GSI3SK']==row['expiresAt'] and row['researchNoticeVersion']==CURRENT_NOTICE
                     and row['researchPolicyVersion']==CURRENT_POLICY)
        validate_metadata(row)
        core.require(1<=core.integer(row['submissionCount'],1)<=3 and type(row['vectorApplied']) is bool
                     and type(row['vector']) is list and 1<=len(row['vector'])<=384
                     and all(type(v) in (int,Decimal) and Decimal(v).is_finite() and abs(v)<=1 for v in row['vector'])
                     and type(row['languageId']) is str and row['languageId'] in LANGUAGE_IDS)
        locator=core.locator_for_target(row,worker.env)
        budget();owned=core.get_owned_locator(worker.d,worker.pipeline,locator)
        core.require(owned==locator)
        locators.append((owned,row))
    # A deleted checkpoint is reconciled only from a later fresh invocation.
    checkpoint_guard=absent(pk,'DELETION_RECOMPUTE') if checkpoint is None else exact(checkpoint)
    guards=[fence.condition(worker.pipeline,registry),core.inventory_condition(worker.pipeline,inventory),absent(pk,'SUMMARY')]
    deleted=0
    for locator,row in locators[:MAX_PAIRS]:
        pair=core.paired_delete_actions(worker.pipeline,locator)
        target=exact(row,'Delete')['Delete']
        target['ConditionExpression']='attribute_not_exists(PK) OR ('+target['ConditionExpression']+')'
        pair[0]={'Delete':target}
        budget();fence.GuardedClient(worker.d,worker.pipeline,registry,now=worker.now,remaining_ms=worker.remaining).transact_write_items(TransactItems=[*guards,checkpoint_guard,*pair])
        deleted+=1
    if locators:
        return {'expiredPairs':deleted,'checkpointDeleted':False,'candidateEmptyObserved':False,'scopeComplete':False,'retirementEligible':False}
    # Full strong partition contained only the exact checkpoint, or nothing.
    # CLOSING forbids all contribution creators; every action still checks the
    # current generation/inventory and absent SUMMARY in the same transaction.
    actions=[*guards,checkpoint_guard if checkpoint is None else exact(checkpoint,'Delete')]
    budget();fence.GuardedClient(worker.d,worker.pipeline,registry,now=worker.now,remaining_ms=worker.remaining).transact_write_items(TransactItems=actions)
    return {'expiredPairs':0,'checkpointDeleted':checkpoint is not None,'candidateEmptyObserved':True,'scopeComplete':False,'retirementEligible':False}
