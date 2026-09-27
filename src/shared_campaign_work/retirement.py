"""Proof-bound one-way KMS retirement; never enables, creates, or cancels keys."""
from datetime import datetime
from botocore.exceptions import ClientError
import re
import os
from . import configuration as C, records as R
from shared_campaign_locators import period as P

SEAL_FIELDS = {'sealSchemaVersion','sealedAtEpoch','sealManifestSha256','sealInventoryRevision',
               'sealGeneration','sealPipelineTableId','sealOutboxTableId','sealNextOrdinal'}
RETIREMENT_FIELDS = {'retirementSchemaVersion','retirementState','retirementRequestedAtEpoch',
                     'retirementObservedAtEpoch'}
SCHEDULED_FIELDS = RETIREMENT_FIELDS | {'scheduledDeletionAtEpoch'}
BASE_FIELDS = P.BASE_FIELDS | P.ADMISSION_FIELDS | P.WORK_FIELDS | SEAL_FIELDS


def validate_sealed(record, config, marker, now):
    """Strict sealed registry, including optional irreversible retirement stages."""
    R.need(type(record) is dict)
    state=record.get('retirementState')
    extra=set() if state is None else SCHEDULED_FIELDS if state=='SCHEDULED' else RETIREMENT_FIELDS
    R.need(state in (None,'INTENT','DISABLED','SCHEDULED') and set(record)==BASE_FIELDS|extra)
    period=R.integer(record['periodId']);now=R.integer(now,1)
    R.need(record['PK']==f'PERIOD#{period}' and record['SK']=='HMAC_KEY'
           and record['status']==('RETIRED' if state=='SCHEDULED' else 'ENABLED')
           and record['admissionState']=='SEALED'
           and record['admissionGeneration']==config['generation']
           and record['workManifestSha256']==config['manifest']
           and record['sealManifestSha256']==config['manifest']
           and record['sealGeneration']==config['generation']
           and record['sealPipelineTableId']==config['resources']['pipeline']['tableId']
           and record['sealOutboxTableId']==config['resources']['outbox']['tableId']
           and record['admissionManifestSha256']==marker['locatorManifestSha256'])
    R.integer(record['admissionSchemaVersion'],2,2)
    for field in ('workSchemaVersion','sealSchemaVersion'):R.integer(record[field],1,1)
    R.need(R.integer(record['admissionInventoryRevision'],1)==marker['locatorInventoryRevision'])
    for field in ('workInventoryRevision','sealInventoryRevision'):
        R.need(R.integer(record[field],1)==config['revision'])
    R.integer(record['admissionRevision'],1)
    sealed=R.integer(record['sealedAtEpoch'],1,now)
    R.integer(record['admissionChangedAtEpoch'],1,sealed)
    R.integer(record['sealNextOrdinal'],1)
    R.need(R.integer(record['retireAfterEpoch'],1)==(period+1)*P.PERIOD_SECONDS+P.RECOVERY_SECONDS)
    R.need(sealed>=record['retireAfterEpoch'] and period>=marker['minimumPeriodId'])
    R.need(type(record['keyArn']) is str and re.fullmatch(
        rf"arn:aws:kms:{re.escape(config['region'])}:{config['account']}:key/[0-9a-f]{{8}}(?:-[0-9a-f]{{4}}){{3}}-[0-9a-f]{{12}}",record['keyArn']))
    if state is not None:
        R.integer(record['retirementSchemaVersion'],1,1)
        requested=R.integer(record['retirementRequestedAtEpoch'],sealed,now)
        observed=R.integer(record['retirementObservedAtEpoch'],requested,now)
        if state=='INTENT':R.need(observed==requested)
        if state=='SCHEDULED':
            R.integer(record['scheduledDeletionAtEpoch'],requested+7*86400,observed+7*86400+60)
    return record


def validate_retired(record,config,marker,now):
    value=validate_sealed(record,config,marker,now)
    R.need(value.get('retirementState')=='SCHEDULED' and value['status']=='RETIRED')
    return value


def retire(client,kms,period,*,now,remaining_ms=lambda:30000):
    """Reconcile one sealed period. Scheduling is not evidence of key destruction."""
    R.need(os.environ.get('CAMPAIGN_PERIOD_RETIREMENT_ENABLED','false')=='true')
    config=C.pins();period=R.integer(period);table=config['resources']['pipeline']['tableName']
    def call(method,**kwargs):
        R.need(remaining_ms()>=6000)
        return method(**kwargs)
    def clock():return R.integer(now(),1)
    def read(key):
        raw=call(client.get_item,TableName=table,Key=C.wire(key),ConsistentRead=True).get('Item')
        return C.plain(raw) if raw else None
    def evidence():
        timestamp=clock();C.binding(client,config,remaining_ms)
        marker=C.validate_marker(read({'PK':'INVENTORY#'+config['environment'],'SK':'CAMPAIGN_PERIOD_WORK'}),config,timestamp)
        registry=validate_sealed(read({'PK':f'PERIOD#{period}','SK':'HMAC_KEY'}),config,marker,timestamp)
        R.need(registry['periodId']==period and timestamp>=registry['retireAfterEpoch'])
        control=R.validate_control(read(R.control_key(period)),config['environment'],config['generation'],period,
            config['manifest'],config['revision'],timestamp)
        R.need(control['pendingCount']==0 and control['nextOrdinal']==registry['sealNextOrdinal'])
        # No eventually consistent index, counter alone, TTL, or truncated page
        # can stand in for the strong empty-work observation.
        page=call(client.query,TableName=table,ConsistentRead=True,Limit=1,
            KeyConditionExpression='PK = :pk',
            ExpressionAttributeValues=C.wire({':pk':R.WORK_PREFIX+str(period)}))
        R.need(page.get('Items')==[] and not page.get('LastEvaluatedKey'))
        return registry,control,marker
    def transaction(registry,control,marker,changed=None):
        action=C.condition(table,registry)
        if changed is not None:
            delta={k:v for k,v in changed.items() if registry.get(k)!=v}
            update={'TableName':table,'Key':C.wire({'PK':registry['PK'],'SK':registry['SK']}),**C.exact(registry),
                'UpdateExpression':'SET '+', '.join(f'#r{i} = :r{i}' for i in range(len(delta)))}
            update['ExpressionAttributeNames'].update({f'#r{i}':k for i,k in enumerate(delta)})
            update['ExpressionAttributeValues'].update(C.wire({f':r{i}':v for i,v in enumerate(delta.values())}))
            if 'retirementState' not in registry:
                update['ConditionExpression']+=' AND attribute_not_exists(retirementState)'
            action={'Update':update}
        return call(client.transact_write_items,TransactItems=[
            action,C.condition(table,control),C.condition(table,marker)])
    def kms_state(registry):
        arn=registry['keyArn']
        metadata=call(kms.describe_key,KeyId=arn).get('KeyMetadata')
        R.need(type(metadata) is dict and metadata.get('Arn')==arn
            and metadata.get('KeyId')==arn.rsplit('/',1)[1]
            and metadata.get('KeyManager')=='CUSTOMER' and metadata.get('Origin')=='AWS_KMS'
            and metadata.get('KeySpec')=='HMAC_256' and metadata.get('KeyUsage')=='GENERATE_VERIFY_MAC'
            and metadata.get('MultiRegion') is False
            and metadata.get('KeyState') in ('Enabled','Disabled','PendingDeletion')
            and metadata.get('Enabled') is (metadata.get('KeyState')=='Enabled'))
        tags=call(kms.list_resource_tags,KeyId=arn)
        expected={'Project':'trustcheckradar','Environment':config['environment'],
                  'Purpose':'campaign-contributor-token','PeriodId':str(period)}
        values=tags.get('Tags')
        R.need(type(values) is list and len(values)==len(expected)
            and not tags.get('Truncated') and not tags.get('NextMarker')
            and all(type(v) is dict and set(v)=={'TagKey','TagValue'} for v in values)
            and {v['TagKey']:v['TagValue'] for v in values}==expected)
        return metadata
    def replace(registry,control,marker,changes):
        changed=registry|changes
        validate_sealed(changed,config,marker,clock())
        try:transaction(registry,control,marker,changed)
        except Exception:
            # Reconcile a committed transaction with a lost acknowledgement;
            # every binding and proof is read again before proceeding to KMS.
            observed,_,_=evidence()
            R.need(observed==changed)
        return changed

    registry,control,marker=evidence()
    state=registry.get('retirementState')
    try:metadata=kms_state(registry)
    except ClientError as exc:
        if (exc.response.get('Error',{}).get('Code')!='NotFoundException'
            or state!='SCHEDULED' or clock()<registry['scheduledDeletionAtEpoch']):
            raise R.WorkUnavailable() from None
        # Absence is meaningful only after previously observed scheduled proof;
        # it never substitutes for data erasure or admits a missing unknown key.
        transaction(registry,control,marker)
        return {'state':'SCHEDULED','keyDestroyedObserved':True,'changed':False}
    if state=='SCHEDULED':
        R.need(metadata['KeyState']=='PendingDeletion')
        date=metadata.get('DeletionDate')
        R.need(isinstance(date,datetime) and date.tzinfo is not None
            and int(date.timestamp())==registry['scheduledDeletionAtEpoch'])
        transaction(registry,control,marker)
        return {'state':'SCHEDULED','keyDestroyedObserved':False,'changed':False}
    if state is None:
        # A preexisting pending deletion with no durable intent cannot be adopted.
        R.need(metadata['KeyState'] in ('Enabled','Disabled'))
        at=clock()
        registry=replace(registry,control,marker,{'retirementSchemaVersion':1,
            'retirementState':'INTENT','retirementRequestedAtEpoch':at,'retirementObservedAtEpoch':at})
    registry,control,marker=evidence()
    metadata=kms_state(registry)
    transaction(registry,control,marker)
    if metadata['KeyState']=='Enabled':
        R.need(registry['retirementState']=='INTENT')
        try:call(kms.disable_key,KeyId=registry['keyArn'])
        except Exception:pass  # Only the subsequent qualified read can acknowledge it.
        metadata=kms_state(registry)
        R.need(metadata['KeyState']=='Disabled')
    if metadata['KeyState']=='Disabled':
        if registry['retirementState']=='INTENT':
            registry=replace(registry,control,marker,{'retirementState':'DISABLED','retirementObservedAtEpoch':clock()})
        registry,control,marker=evidence()
        R.need(registry['retirementState']=='DISABLED')
        metadata=kms_state(registry);R.need(metadata['KeyState']=='Disabled')
        transaction(registry,control,marker)
        try:call(kms.schedule_key_deletion,KeyId=registry['keyArn'],PendingWindowInDays=7)
        except Exception:pass
        metadata=kms_state(registry)
    R.need(metadata['KeyState']=='PendingDeletion' and registry['retirementState']=='DISABLED')
    date=metadata.get('DeletionDate')
    R.need(isinstance(date,datetime) and date.tzinfo is not None)
    deletion=int(date.timestamp())
    R.need(registry['retirementRequestedAtEpoch']+7*86400<=deletion<=clock()+7*86400+60)
    registry,control,marker=evidence()
    R.need(registry['retirementState']=='DISABLED')
    replace(registry,control,marker,{'retirementState':'SCHEDULED','status':'RETIRED',
        'retirementObservedAtEpoch':clock(),'scheduledDeletionAtEpoch':deletion})
    return {'state':'SCHEDULED','keyDestroyedObserved':False,'changed':True}
