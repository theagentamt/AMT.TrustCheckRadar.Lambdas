"""Whole-period admission fence; no initializer, reopening or retirement writer."""
import os
import re
from uuid import UUID
from .core import (require, integer, serialize, deserialize, _key, _exact,
                   load_inventory, inventory_condition)

PERIOD_SECONDS = 14 * 86400
RECOVERY_SECONDS = 7 * 86400
WORK_FIELDS = {'workSchemaVersion','workManifestSha256','workInventoryRevision'}
BASE_FIELDS = {'PK','SK','keyArn','status','periodId','retireAfterEpoch'}
ADMISSION_FIELDS = {'admissionSchemaVersion','admissionGeneration','admissionState',
    'admissionRevision','admissionManifestSha256','admissionInventoryRevision','admissionChangedAtEpoch'}


def configuration():
    require(os.environ.get('CAMPAIGN_PERIOD_ADMISSION_ENABLED','false') == 'true')
    generation=os.environ.get('CAMPAIGN_PERIOD_ADMISSION_GENERATION','')
    try:
        parsed=UUID(generation)
        require(parsed.version==4 and str(parsed)==generation)
    except (ValueError,TypeError,AttributeError):
        require(False)
    identity()
    return generation


def identity():
    account=os.environ.get('CAMPAIGN_PERIOD_ADMISSION_ACCOUNT_ID','')
    region=os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION','')
    require(re.fullmatch('[0-9]{12}',account) and re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-[0-9]',region))
    return account,region


def runtime(context):
    configuration()
    account,region=identity()
    arn=getattr(context,'invoked_function_arn','').split(':')
    require(len(arn) in (7,8) and arn[:3]==['arn','aws','lambda'] and arn[3]==region
            and arn[4]==account and arn[5]=='function' and bool(arn[6]))


def validate(record, period, manifest, revision, now, *, states=('OPEN','CLOSING'), generation=None):
    generation=configuration() if generation is None else generation
    # An explicitly supplied generation is internal evidence, never a gate bypass.
    require(generation==configuration())
    integer(period);integer(now,1);integer(revision,1)
    require(type(manifest) is str and re.fullmatch('[0-9a-f]{64}',manifest))
    require(type(record) is dict)
    from shared_campaign_work import configuration as work_configuration
    modern=work_configuration.enabled()
    require(set(record)==BASE_FIELDS|ADMISSION_FIELDS|(WORK_FIELDS if modern else set()))
    if modern:
        work=work_configuration.pins()
        require(integer(record['workSchemaVersion'],1)==1 and record['workManifestSha256']==work['manifest']
                and integer(record['workInventoryRevision'],1)==work['revision'])
    require(record['PK']==f'PERIOD#{period}' and record['SK']=='HMAC_KEY'
            and record['status']=='ENABLED' and integer(record['periodId'])==period
            and integer(record['retireAfterEpoch'])==(period+1)*PERIOD_SECONDS+RECOVERY_SECONDS)
    account,region=identity()
    require(type(record['keyArn']) is str and re.fullmatch(
        rf'arn:aws:kms:{re.escape(region)}:{account}:key/[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}',record['keyArn']))
    require(integer(record['admissionSchemaVersion'])==(2 if modern else 1) and record['admissionGeneration']==generation
            and record['admissionState'] in states and integer(record['admissionRevision'],1)<=9007199254740991
            and record['admissionManifestSha256']==manifest
            and integer(record['admissionInventoryRevision'],1)==revision
            and integer(record['admissionChangedAtEpoch'],1)<=now)
    return record


def read(client, table, period, manifest, revision, now, *, states=('OPEN','CLOSING')):
    configuration()  # Refuse before storage access when disabled.
    raw=client.get_item(TableName=table,Key=_key(f'PERIOD#{integer(period)}','HMAC_KEY'),ConsistentRead=True).get('Item')
    return validate(deserialize(raw) if raw else None,period,manifest,revision,now,states=states)


def transient_deadline(period, now, *original_bounds):
    """Approved maxima: no transient survives its period recovery horizon."""
    period=integer(period);integer(now,1)
    require(bool(original_bounds))
    bounds=[integer(value,1) for value in original_bounds]
    deadline=min(*bounds,(period+1)*PERIOD_SECONDS+RECOVERY_SECONDS)
    require(now<deadline)
    return deadline


def condition(table, record):
    return {'ConditionCheck':{'TableName':table,'Key':_key(record['PK'],record['SK']),**_exact(record)}}


class GuardedClient:
    """Exactly one immutable observed period guard per transaction."""
    def __init__(self,client,table,record,*,now=None,remaining_ms=lambda:30000):
        import time
        self.client,self.table,self.record=client,table,record
        self.now=now or (lambda:int(time.time()));self.remaining=remaining_ms
    def __getattr__(self,name):return getattr(self.client,name)
    def transact_write_items(self,**kwargs):
        actions=kwargs['TransactItems'];guard=condition(self.table,self.record)
        matched=False
        for action in actions:
            item=next(iter(action.values()))
            target=item.get('Key',item.get('Item',{}))
            if item.get('TableName')==self.table and all(target.get(k)==guard['ConditionCheck']['Key'][k] for k in ('PK','SK')):
                require(not matched and action==guard)
                matched=True
        from shared_campaign_work import configuration as work_config
        client=self.client
        if work_config.enabled():
            from shared_campaign_work.transactions import TrackedClient
            client=TrackedClient(client,registries=[self.record],now=self.now,remaining_ms=self.remaining)
        if matched:return client.transact_write_items(**kwargs)
        require(len(actions)<100)
        return client.transact_write_items(**(kwargs|{'TransactItems':[*actions,guard]}))


def close(client, table, environment, period, manifest, revision, now, remaining_ms=lambda:30000):
    """One irreversible CAS. Time permits closing, never proves erasure."""
    configuration()
    def budget():require(remaining_ms()>=6000)
    budget();inventory=load_inventory(client,table,environment,manifest,revision,now)
    require(integer(period)>=inventory['minimumPeriodId'] and now>=(period+1)*PERIOD_SECONDS)
    budget();record=read(client,table,period,manifest,revision,now)
    if record['admissionState']=='CLOSING':
        # Check-only replay proves pins still match; no timestamp/expiry refresh.
        budget();client.transact_write_items(TransactItems=[inventory_condition(table,inventory),condition(table,record)])
        return {'state':'CLOSING','changed':False,'periodComplete':False,'retirementEligible':False}
    require(record['admissionRevision']<9007199254740991)
    action={'Update':{'TableName':table,'Key':_key(record['PK'],record['SK']),**_exact(record),
        'UpdateExpression':'SET admissionState = :closing, admissionRevision = :next, admissionChangedAtEpoch = :now'}}
    action['Update']['ExpressionAttributeValues'].update(serialize({':closing':'CLOSING',':next':record['admissionRevision']+1,':now':now}))
    budget();client.transact_write_items(TransactItems=[inventory_condition(table,inventory),action])
    return {'state':'CLOSING','changed':True,'periodComplete':False,'retirementEligible':False}
