"""Real DynamoDB CAS with injected deterministic KMS response/failure states."""
import os
from copy import deepcopy
from datetime import datetime, timezone
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('Isolated SDK only',allow_module_level=True)
from shared_campaign_work import retirement as K, configuration as C, records as R
from tests.shared_campaign_work.test_transactions_dynamodb import world, PERIOD, NOW

@pytest.fixture
def sealed(world,monkeypatch):
    monkeypatch.setenv('CAMPAIGN_PERIOD_RETIREMENT_ENABLED','true')
    writer,d,put,get,registry,marker,control=world
    timestamp=registry['retireAfterEpoch']+100
    sealed=registry|{'admissionState':'SEALED','admissionChangedAtEpoch':timestamp-1,
        'sealSchemaVersion':1,'sealedAtEpoch':timestamp-1,'sealManifestSha256':'b'*64,
        'sealInventoryRevision':1,'sealGeneration':C.pins()['generation'],
        'sealPipelineTableId':C.pins()['resources']['pipeline']['tableId'],
        'sealOutboxTableId':C.pins()['resources']['outbox']['tableId'],'sealNextOrdinal':1}
    put(sealed)
    class Kms:
        def __init__(self):
            self.calls=[];self.lost=set();self.fail=set()
            self.metadata={'Arn':sealed['keyArn'],'KeyId':sealed['keyArn'].rsplit('/',1)[1],
                'KeyManager':'CUSTOMER','Origin':'AWS_KMS','KeySpec':'HMAC_256',
                'KeyUsage':'GENERATE_VERIFY_MAC','MultiRegion':False,'KeyState':'Enabled','Enabled':True}
            self.tags={'Project':'trustcheckradar','Environment':'dev','Purpose':'campaign-contributor-token','PeriodId':str(PERIOD)}
        def describe_key(self,**kw):
            assert kw=={'KeyId':sealed['keyArn']}
            return {'KeyMetadata':deepcopy(self.metadata)}
        def list_resource_tags(self,**kw):
            assert kw=={'KeyId':sealed['keyArn']}
            return {'Tags':[{'TagKey':k,'TagValue':v} for k,v in self.tags.items()]}
        def disable_key(self,**kw):
            assert get({'PK':sealed['PK'],'SK':'HMAC_KEY'})['retirementState']=='INTENT'
            self.calls.append('disable')
            if 'disable' in self.fail:raise RuntimeError('before mutation')
            self.metadata.update(KeyState='Disabled',Enabled=False)
            if 'disable' in self.lost:raise RuntimeError('lost acknowledgment')
        def schedule_key_deletion(self,**kw):
            assert kw=={'KeyId':sealed['keyArn'],'PendingWindowInDays':7}
            assert get({'PK':sealed['PK'],'SK':'HMAC_KEY'})['retirementState']=='DISABLED'
            self.calls.append('schedule')
            if 'schedule' in self.fail:raise RuntimeError('before mutation')
            self.metadata.update(KeyState='PendingDeletion',Enabled=False,
                DeletionDate=datetime.fromtimestamp(timestamp+7*86400,timezone.utc))
            if 'schedule' in self.lost:raise RuntimeError('lost acknowledgment')
    kms=Kms()
    return writer.client,kms,put,get,sealed,marker,control,timestamp


def retire(w,**kw):
    client,kms,put,get,registry,marker,control,now=w
    return K.retire(client,kms,PERIOD,now=lambda:now,**kw)


def test_intent_precedes_kms_and_replay_never_refreshes_deadline(sealed):
    client,kms,put,get,registry,marker,control,now=sealed
    assert retire(sealed)=={'state':'SCHEDULED','keyDestroyedObserved':False,'changed':True}
    stored=get({'PK':registry['PK'],'SK':'HMAC_KEY'})
    assert K.validate_retired(stored,C.pins(),marker,now)==stored
    assert stored['retireAfterEpoch']==registry['retireAfterEpoch']
    assert kms.calls==['disable','schedule']
    assert retire(sealed)=={'state':'SCHEDULED','keyDestroyedObserved':False,'changed':False}
    assert get({'PK':registry['PK'],'SK':'HMAC_KEY'})==stored and kms.calls==['disable','schedule']


@pytest.mark.parametrize('stage',['disable','schedule'])
def test_lost_kms_response_reconciles_without_repeating_mutation(sealed,stage):
    sealed[1].lost.add(stage)
    assert retire(sealed)['state']=='SCHEDULED'
    assert sealed[1].calls==['disable','schedule']


@pytest.mark.parametrize('stage',['disable','schedule'])
def test_failed_kms_mutation_leaves_durable_resumable_intent(sealed,stage):
    client,kms,put,get,registry,*_=sealed;kms.fail.add(stage)
    with pytest.raises(R.WorkUnavailable):retire(sealed)
    assert get({'PK':registry['PK'],'SK':'HMAC_KEY'})['retirementState']==('INTENT' if stage=='disable' else 'DISABLED')
    kms.fail.clear();assert retire(sealed)['state']=='SCHEDULED'


@pytest.mark.parametrize('change',[
    {'admissionState':'DRAINING'},{'sealNextOrdinal':2},{'sealGeneration':'11111111-1111-4111-8111-111111111111'},
    {'sealPipelineTableId':'33333333-3333-4333-8333-333333333333'},{'sealManifestSha256':'c'*64},
    {'unexpected':'field'},{'retireAfterEpoch':1},{'sealSchemaVersion':True},
])
def test_unqualified_registry_has_no_kms_mutation(sealed,change):
    client,kms,put,get,registry,*_=sealed;put(registry|change)
    with pytest.raises(R.WorkUnavailable):retire(sealed)
    assert kms.calls==[]


@pytest.mark.parametrize('field,value',[
    ('Arn','arn:aws:kms:us-west-2:107827791950:key/11111111-1111-4111-8111-111111111111'),
    ('KeySpec','SYMMETRIC_DEFAULT'),('KeyUsage','ENCRYPT_DECRYPT'),('MultiRegion',True),
    ('KeyManager','AWS'),('Origin','EXTERNAL'),('Enabled',False),('KeyState','PendingDeletion')])
def test_foreign_or_wrong_key_cannot_acquire_intent(sealed,field,value):
    client,kms,put,get,registry,*_=sealed;kms.metadata[field]=value
    with pytest.raises(R.WorkUnavailable):retire(sealed)
    assert kms.calls==[] and get({'PK':registry['PK'],'SK':'HMAC_KEY'})==registry


def test_foreign_period_tag_refused(sealed):
    sealed[1].tags['PeriodId']=str(PERIOD-1)
    with pytest.raises(R.WorkUnavailable):retire(sealed)
    assert sealed[1].calls==[]


def test_pending_work_even_with_zero_counter_refuses(sealed):
    client,kms,put,*_=sealed
    put({'PK':R.WORK_PREFIX+str(PERIOD),'SK':'WORK#00000000000000000001','unknown':True})
    with pytest.raises(R.WorkUnavailable):retire(sealed)
    assert kms.calls==[]


def test_truncated_empty_work_page_refuses(sealed):
    client,kms,*_=sealed;real=client.query
    client.query=lambda **kw:{'Items':[],'LastEvaluatedKey':C.wire({'PK':'next','SK':'next'})}
    try:
        with pytest.raises(R.WorkUnavailable):retire(sealed)
    finally:client.query=real
    assert kms.calls==[]


def test_lost_intent_transaction_response_is_strongly_reconciled(sealed):
    client,kms,put,get,registry,*_=sealed;real=client.transact_write_items;first=[True]
    def lost(**kw):
        result=real(**kw)
        if first[0]:first[0]=False;raise RuntimeError('lost acknowledgment')
        return result
    client.transact_write_items=lost
    try:assert retire(sealed)['state']=='SCHEDULED'
    finally:client.transact_write_items=real
    assert kms.calls==['disable','schedule']


def test_proof_race_aborts_before_kms(sealed):
    client,kms,put,get,registry,marker,control,now=sealed
    real=client.transact_write_items
    def race(**kw):
        put(control|{'revision':control['revision']+1,'nextOrdinal':2,'pendingCount':1})
        return real(**kw)
    client.transact_write_items=race
    try:
        with pytest.raises(Exception):retire(sealed)
    finally:client.transact_write_items=real
    assert kms.calls==[] and get({'PK':registry['PK'],'SK':'HMAC_KEY'})==registry


def test_budget_refusal_before_sdk(sealed):
    with pytest.raises(R.WorkUnavailable):retire(sealed,remaining_ms=lambda:5000)
    assert sealed[1].calls==[]


def test_key_absence_requires_scheduled_proof_and_reached_deadline(sealed):
    from botocore.exceptions import ClientError
    client,kms,put,get,registry,marker,control,now=sealed
    original=kms.describe_key
    def missing(**kw):raise ClientError({'Error':{'Code':'NotFoundException'}},'DescribeKey')
    kms.describe_key=missing
    with pytest.raises(R.WorkUnavailable):retire(sealed)
    assert kms.calls==[]
    kms.describe_key=original;retire(sealed)
    kms.describe_key=missing
    with pytest.raises(R.WorkUnavailable):retire(sealed)
    assert K.retire(client,kms,PERIOD,now=lambda:now+7*86400)=={
        'state':'SCHEDULED','keyDestroyedObserved':True,'changed':False}
    assert kms.calls==['disable','schedule']


def test_access_denied_is_never_key_absence(sealed):
    from botocore.exceptions import ClientError
    client,kms,put,get,registry,marker,control,now=sealed
    retire(sealed)
    def denied(**kw):raise ClientError({'Error':{'Code':'AccessDeniedException'}},'DescribeKey')
    kms.describe_key=denied
    with pytest.raises(R.WorkUnavailable):
        K.retire(client,kms,PERIOD,now=lambda:now+7*86400)


def test_restored_resource_identity_refuses_before_kms(sealed):
    client,kms,*_=sealed;original=client.describe_table
    def restored(**kwargs):
        result=original(**kwargs)
        result['Table']['TableId']='33333333-3333-4333-8333-333333333333'
        return result
    client.describe_table=restored
    try:
        with pytest.raises(R.WorkUnavailable):retire(sealed)
    finally:client.describe_table=original
    assert kms.calls==[]


def test_disabled_registry_with_enabled_key_never_reenables_or_reschedules(sealed):
    client,kms,put,get,registry,*_=sealed
    kms.fail.add('schedule')
    with pytest.raises(R.WorkUnavailable):retire(sealed)
    kms.fail.clear();kms.calls.clear();kms.metadata.update(KeyState='Enabled',Enabled=True)
    with pytest.raises(R.WorkUnavailable):retire(sealed)
    assert kms.calls==[]


@pytest.mark.parametrize('mutation', ['tags_truncated','tags_duplicate','deletion_date_missing'])
def test_ambiguous_kms_metadata_is_not_accepted(sealed,mutation):
    client,kms,put,get,registry,*_=sealed
    if mutation=='tags_truncated':
        real=kms.list_resource_tags
        kms.list_resource_tags=lambda **kw:real(**kw)|{'Truncated':True}
    elif mutation=='tags_duplicate':
        real=kms.list_resource_tags
        kms.list_resource_tags=lambda **kw:{'Tags':real(**kw)['Tags'][:-1]+[real(**kw)['Tags'][0]]}
    else:
        real=kms.describe_key
        def without_date(**kw):
            value=real(**kw);value['KeyMetadata'].pop('DeletionDate',None);return value
        kms.describe_key=without_date
    with pytest.raises(R.WorkUnavailable):retire(sealed)
    if mutation.startswith('tags'):assert kms.calls==[]
    else:assert get({'PK':registry['PK'],'SK':'HMAC_KEY'})['retirementState']=='DISABLED'


@pytest.mark.parametrize('stage', ['DISABLED','SCHEDULED'])
def test_lost_stage_commit_reconciles_without_duplicate_external_calls(sealed,stage):
    client,kms,put,get,registry,*_=sealed;real=client.transact_write_items;lost=[False]
    def invoke(**kwargs):
        result=real(**kwargs)
        changed=any('Update' in action for action in kwargs['TransactItems'])
        if changed and not lost[0] and get({'PK':registry['PK'],'SK':'HMAC_KEY'}).get('retirementState')==stage:
            lost[0]=True;raise RuntimeError('lost stage acknowledgment')
        return result
    client.transact_write_items=invoke
    try:assert retire(sealed)['state']=='SCHEDULED'
    finally:client.transact_write_items=real
    assert lost[0] and kms.calls==['disable','schedule']


def test_disabled_retirement_refuses_before_any_sdk_call(monkeypatch):
    monkeypatch.setenv('CAMPAIGN_PERIOD_RETIREMENT_ENABLED','false')
    class NoCalls:
        def __getattr__(self,name):raise AssertionError('SDK must not be reached')
    with pytest.raises(R.WorkUnavailable):K.retire(NoCalls(),NoCalls(),PERIOD,now=lambda:NOW)
