"""One fair, durably selected retained-period attempt; never a completion proof."""
import base64
import re
import time
from copy import deepcopy
from decimal import Decimal
from uuid import UUID

from cleanup_errors import CoverageUnavailable
from coverage_assessment import _key_record
from progress import get, key, wire, require, integer, CommandGuardedClient
from service import parse_deletion_record, PERIOD_SECONDS, TOKEN_DOMAIN
from shared_campaign_locators import load_inventory, InventoryGuardedClient
from tombstone import ensure, validate
from locator_progress import sweep

SWEEP_FIELDS={'schemaVersion','operationId','inventoryRevision','minimumPeriodId','maximumPeriodId','nextPeriodId'}


def validate_cursor(tomb):
    fields={'retainedPeriodSweep','retainedPeriodSweepRevision'}
    if not fields.intersection(tomb):return None
    require(fields.issubset(tomb))
    integer(tomb['retainedPeriodSweepRevision'],1)
    state=tomb['retainedPeriodSweep']
    require(type(state) is dict and set(state)==SWEEP_FIELDS and integer(state['schemaVersion'],1)==1)
    integer(state['inventoryRevision'],1)
    first,last,next_period=(integer(state[name]) for name in ('minimumPeriodId','maximumPeriodId','nextPeriodId'))
    require(first<=next_period<=last and last-first<32 and last==integer(tomb['periodId']))
    try:
        op=UUID(state['operationId']);require(op.version==4 and str(op)==state['operationId'])
    except (ValueError,TypeError,AttributeError):
        raise CoverageUnavailable() from None
    return state


class RegistryGuardedClient:
    def __init__(self,client,table,record):
        self.client=client
        names={f'#k{i}':name for i,name in enumerate(record) if name not in ('PK','SK')}
        values={f':k{i}':record[name] for i,name in enumerate(record) if name not in ('PK','SK')}
        self.guard={'ConditionCheck':{'TableName':table,'Key':key(record['PK'],record['SK']),
            'ConditionExpression':' AND '.join(f'{name} = :{name[1:]}' for name in names),
            'ExpressionAttributeNames':names,'ExpressionAttributeValues':wire(values)}}

    def __getattr__(self,name):return getattr(self.client,name)

    def transact_write_items(self,**kwargs):
        return self.client.transact_write_items(**(kwargs|{'TransactItems':[self.guard,*kwargs['TransactItems']]}))


def advance(ddb,table,tomb,state):
    # Compare the whole observed known tombstone, preserving locator progress.
    names={f'#t{i}':field for i,field in enumerate(tomb) if field not in ('PK','SK')}
    values={f':t{i}':value for i,(field,value) in enumerate(tomb.items()) if field not in ('PK','SK')}
    expression=' AND '.join(f'{name} = :{name[1:]}' for name in names)
    if 'retainedPeriodSweep' not in tomb:
        expression+=' AND attribute_not_exists(retainedPeriodSweep) AND attribute_not_exists(retainedPeriodSweepRevision)'
    values.update({':state':state,':revision':integer(tomb.get('retainedPeriodSweepRevision',0))+1})
    ddb.transact_write_items(TransactItems=[{'Update':{'TableName':table,'Key':key(tomb['PK'],'TOMBSTONE'),
        'UpdateExpression':'SET retainedPeriodSweep = :state, retainedPeriodSweepRevision = :revision',
        'ConditionExpression':expression,'ExpressionAttributeNames':names,'ExpressionAttributeValues':wire(values)}}])


def delete_retained_contributions(command,*,environment,aws_account_id,aws_region,table_name,
        deletion_ledger_table_name,retention_days,dynamodb,kms,locator_manifest_sha256,
        locator_inventory_revision,now_epoch=None,max_periods=8,max_steps=10,remaining_ms=None):
    """A cursor advance may precede a failed attempt; its locator state is never reset."""
    command=deepcopy(command)
    now=int(time.time()) if now_epoch is None else now_epoch
    require(type(now) is int and type(max_periods) is int and 1<=max_periods<=32
            and type(max_steps) is int and 1<=max_steps<=20
            and type(retention_days) is int and 1<=retention_days<=21
            and environment in ('dev','uat','prod')
            and type(aws_account_id) is str and re.fullmatch(r'[0-9]{12}',aws_account_id)
            and type(aws_region) is str and re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-[0-9]',aws_region))
    parsed=parse_deletion_record({'eventName':'INSERT','dynamodb':{'NewImage':wire(command)}},environment=environment,schema_version=1)
    require(parsed is not None and 0<command['occurredAtEpoch']<=now
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:@-]{0,199}',command['accountId']))
    result={'deleted':0,'recomputedCandidates':0,'complete':False,'receiptEligible':False,
            'coverage':'RETAINED_PERIOD_ATTEMPT_ONLY','periodsExpected':0,'periodsAttempted':0,
            'selectedLocatorPassEnded':False,'reason':'PENDING'}
    def budget():require(remaining_ms is None or remaining_ms()>=3000)
    budget()
    stored=get(dynamodb,deletion_ledger_table_name,command['PK'],command['SK'])
    if stored and stored.get('status')=='COMPLETE':
        completed=integer(stored.get('completedAtEpoch'),command['occurredAtEpoch'])
        expected=command|{'status':'COMPLETE','completedAtEpoch':completed}
        if command['eventType']=='account.deletion.requested':
            expected|={'eventType':'account.deletion.completed','retainUntilEpoch':completed+120*86400}
        require(stored==expected and completed<=now)
        return result|{'alreadyCompleted':True,'reason':'ALREADY_COMPLETED'}
    require(stored==command)
    budget()
    inventory=load_inventory(dynamodb,table_name,environment,locator_manifest_sha256,locator_inventory_revision,now)
    first,last=int(inventory['minimumPeriodId']),command['occurredAtEpoch']//PERIOD_SECONDS
    require(command['occurredAtEpoch']>inventory['approvedAtEpoch'] and first<=last and last-first+1<=max_periods)
    result['periodsExpected']=last-first+1
    guarded=InventoryGuardedClient(CommandGuardedClient(dynamodb,deletion_ledger_table_name,command),table_name,inventory)
    def derive(period):
        budget();record=get(dynamodb,table_name,f'PERIOD#{period}','HMAC_KEY')
        arn=_key_record(record,period,aws_account_id,aws_region)
        budget();response=kms.generate_mac(KeyId=arn,Message=TOKEN_DOMAIN+command['accountId'].encode(),MacAlgorithm='HMAC_SHA_256')
        require(response.get('KeyId')==arn and response.get('MacAlgorithm')=='HMAC_SHA_256'
                and type(response.get('Mac')) is bytes and len(response['Mac'])==32)
        partition=f"CONTRIB#{period}#{base64.urlsafe_b64encode(response['Mac']).decode().rstrip('=')}"
        return record,partition
    # Without this exact request-period anchor the cursor cannot be safely addressed.
    anchor_record,anchor=derive(last)
    anchor_client=RegistryGuardedClient(guarded,table_name,anchor_record)
    budget();tomb=ensure(anchor_client,table_name,environment,anchor,last,command['occurredAtEpoch'],retention_days)
    require(tomb['createdAtEpoch']<=command['occurredAtEpoch'])
    cursor=validate_cursor(tomb)
    if cursor:
        require(cursor['inventoryRevision']==inventory['revision'] and cursor['minimumPeriodId']==first and cursor['maximumPeriodId']==last)
        selected=int(cursor['nextPeriodId'])
    else:selected=first
    next_period=first if selected==last else selected+1
    state={'schemaVersion':1,'operationId':command['operationId'],'inventoryRevision':inventory['revision'],
           'minimumPeriodId':first,'maximumPeriodId':last,'nextPeriodId':next_period}
    budget();advance(anchor_client,table_name,tomb,state)
    result['periodsAttempted']=1
    try:
        selected_record,partition=(anchor_record,anchor) if selected==last else derive(selected)
        selected_client=RegistryGuardedClient(guarded,table_name,selected_record)
        budget();target=ensure(selected_client,table_name,environment,partition,selected,command['occurredAtEpoch'],retention_days)
        require(target['createdAtEpoch']<=command['occurredAtEpoch'])
        budget();outcome=sweep(selected_client,table_name,environment,partition,command['operationId'],now,
                               max_steps=max_steps,remaining_ms=remaining_ms)
        result.update(deleted=outcome['deleted'],recomputedCandidates=outcome['recomputedCandidates'],
                      selectedLocatorPassEnded=outcome['locatorPassEnded'],reason='COMPLETION_PROOF_UNQUALIFIED')
    except Exception:
        # Cursor already advanced: poison/missing evidence must not starve another period.
        # Never expose SDK errors or equate a failed selected period with empty coverage.
        result['reason']='SELECTED_PERIOD_UNVERIFIED'
    return result
