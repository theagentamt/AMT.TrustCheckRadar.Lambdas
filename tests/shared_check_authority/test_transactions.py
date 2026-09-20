"""Isolated DynamoDB emulator regressions; never use real AWS or app mocks."""
import os
import sys
from pathlib import Path
import pytest

if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run in isolated Moto environment; see authority handoff', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from copy import deepcopy
from dataclasses import replace
import boto3
from moto import mock_aws
from botocore.config import Config
from shared_check_authority.core import Authority, AuthorityError, Settings, OWNER_POLICY, TRIAL_SECONDS, VIRUS_TOTAL_ENABLED, TrustedWorkerContext

ACCOUNT = 'synthetic-account'
WORKER = TrustedWorkerContext(ACCOUNT)
PAYLOAD = {'entryPoint': 'standalone_url', 'language': 'en', 'target': {'url': 'https://example.com/?a=1&b=2', 'scope': 'full_url', 'withheldComponents': []}}

@pytest.fixture
def world():
    with mock_aws():
        ddb = boto3.resource('dynamodb', region_name='us-east-1', aws_access_key_id='synthetic', aws_secret_access_key='synthetic', config=Config(retries={'total_max_attempts':1}))
        for name in ('users', 'devices', 'deletion', 'authority'):
            ddb.create_table(TableName=name, BillingMode='PAY_PER_REQUEST', KeySchema=[{'AttributeName': 'PK', 'KeyType': 'HASH'}, {'AttributeName': 'SK', 'KeyType': 'RANGE'}], AttributeDefinitions=[{'AttributeName': k, 'AttributeType': 'S'} for k in ('PK', 'SK')])
        clock = [1800000000]
        settings = Settings('users','devices','deletion','authority','https://issuer.example','client','checks',OWNER_POLICY,'k1',{'k1': b'synthetic-not-secret-test-key-0000'},60,120,300,480,600,60,100,3,True)
        a = Authority(settings, ddb, now=lambda: clock[0])
        pk = a._partition(ACCOUNT, 'k1')
        def put(table, sk, **attrs):
            prefix = pk if table == 'authority' else ('ACCOUNT#' if table == 'deletion' else 'USER#') + ACCOUNT
            ddb.Table(table).put_item(Item={'PK':prefix,'SK':sk,**attrs})
        put('users','PROFILE',sub=ACCOUNT,status='ACTIVE',ageVerified=True)
        put('devices','ACTIVE_BINDING',recordType='ACTIVE_BINDING_POINTER',stateVersion=1,bindingFingerprint='device-one')
        put('devices','DEVICE#device-one',accountId=ACCOUNT,status='ACTIVE',bindingFingerprint='device-one')
        put('authority','ACCESS',recordType='V1_ACCESS_AUTHORITY',schemaVersion=1,state='ACTIVE',policyVersion=OWNER_POLICY,basis='paid',revision=1,validFromEpoch=clock[0]-100,validUntilEpoch=clock[0]+1000,periodId='p1')
        put('authority','PERIOD#p1',recordType='V1_ALLOWANCE_PERIOD',policyVersion=OWNER_POLICY,grantRevision=1,limit=200,usedChecks=0,reservedChecks=0,startEpoch=clock[0]-100,endEpoch=clock[0]+1000)
        event={'headers':{'x-device-binding-fingerprint':'device-one'},'requestContext':{'authorizer':{'jwt':{'claims':{'sub':ACCOUNT,'iss':settings.cognito_issuer,'client_id':'client','token_use':'access','scope':'checks','exp':str(clock[0]+90)}}}}}
        def row(sk): return a._get('authority',{'PK':pk,'SK':sk})
        def change(table, sk, **attrs):
            prefix = pk if table == 'authority' else ('ACCOUNT#' if table == 'deletion' else 'USER#') + ACCOUNT
            old = a._get(table,{'PK':prefix,'SK':sk}) or {'PK':prefix,'SK':sk}
            ddb.Table(table).put_item(Item=old|attrs)
        yield a,event,put,row,change,clock

def admit(w, prep='prepare-1'):
    a,e,*_=w
    cid=a.prepare(e,PAYLOAD,prep)
    return cid,a.admit(e,PAYLOAD,cid)

def failure(code, fn):
    with pytest.raises(AuthorityError) as exc: fn()
    assert exc.value.code == code

@pytest.mark.parametrize('outcome,charge',[('complete',1),('partial',0),('failed',0),('blocked',0),('invalid_input',0),('unsupported',0),('unavailable',0)])
def test_charge_and_duplicate_settlement(world,outcome,charge):
    a,e,_,row,_,_=world
    cid,op=admit(world)
    assert op['admitted'] is True and row('PERIOD#p1')['reservedChecks']==1
    duplicate=a.admit(e,PAYLOAD,cid)
    assert duplicate['admitted'] is False and 'executionToken' not in duplicate
    result=a.settle(WORKER,cid,op['executionToken'],outcome)
    assert result['chargedChecks']==charge
    assert a.settle(WORKER,cid,op['executionToken'],'complete')==result
    assert row('PERIOD#p1')['usedChecks']==charge and row('PERIOD#p1')['reservedChecks']==0
    assert row('INFLIGHT')['activeCount']==0
    assert a.reconcile(e,cid)==result

def test_lost_prepare_response_is_idempotent(world):
    a,e,_,row,_,_=world
    first=a.prepare(e,PAYLOAD,'lost-response')
    assert a.prepare(e,PAYLOAD,'lost-response')==first
    assert row('CHECK#'+first) is None and row('PERIOD#p1')['reservedChecks']==0

@pytest.mark.parametrize('mutation',[
    lambda p:p['target'].update(url='https://example.com/?b=2&a=1'),
    lambda p:p['target'].update(url='http://example.com/?a=1&b=2'),
    lambda p:p['target'].update(scope='origin_only'),
    lambda p:p.update(language='es'),
    lambda p:p['target'].update(withheldComponents=['fragment']),
])
def test_exact_intent_not_normalized(world,mutation):
    a,e,*_=world
    cid=a.prepare(e,PAYLOAD,'intent')
    altered=deepcopy(PAYLOAD);mutation(altered)
    failure('CHECK_ID_CONFLICT',lambda:a.admit(e,altered,cid))

def test_expired_jwt_does_not_strand_worker(world):
    a,e,_,row,_,clock=world
    cid,op=admit(world)
    clock[0]+=91
    failure('AUTHENTICATION_REQUIRED',lambda:a.reconcile(e,cid))
    assert a.settle(WORKER,cid,op['executionToken'],'complete')['chargedChecks']==1
    assert row('PERIOD#p1')['reservedChecks']==0

def test_worker_must_own_execution_and_account(world):
    a,_,_,row,_,_=world
    cid,op=admit(world)
    failure('EXECUTION_OWNER_MISMATCH',lambda:a.settle(WORKER,cid,'wrong','complete'))
    failure('ACCOUNT_UNAVAILABLE',lambda:a.settle(TrustedWorkerContext('other-account'),cid,op['executionToken'],'complete'))
    assert row('PERIOD#p1')['usedChecks']==0

def test_settlement_targets_original_period_after_renewal(world):
    a,_,put,row,change,clock=world
    cid,op=admit(world)
    change('authority','ACCESS',revision=2,periodId='p2')
    put('authority','PERIOD#p2',recordType='V1_ALLOWANCE_PERIOD',policyVersion=OWNER_POLICY,grantRevision=2,limit=200,usedChecks=0,reservedChecks=0,startEpoch=clock[0],endEpoch=clock[0]+1000)
    assert a.settle(WORKER,cid,op['executionToken'],'complete')['chargedChecks']==1
    assert row('PERIOD#p1')['usedChecks']==1 and row('PERIOD#p2')['usedChecks']==0

def test_complimentary_still_bounded_but_never_charged(world):
    a,_,_,row,change,_=world
    change('authority','ACCESS',basis='complimentary')
    cid,op=admit(world)
    assert a.settle(WORKER,cid,op['executionToken'],'complete')['chargedChecks']==0
    assert row('PERIOD#p1')['reservedChecks']==0

@pytest.mark.parametrize('basis,limit',[('paid',200),('trial',10)])
def test_owner_limits_and_explicit_trial(world,basis,limit):
    a,e,_,_,change,clock=world
    extra={'activationKind':'explicit','activatedAtEpoch':clock[0]} if basis=='trial' else {}
    change('authority','ACCESS',basis=basis,validFromEpoch=clock[0],validUntilEpoch=clock[0]+TRIAL_SECONDS,**extra)
    change('authority','PERIOD#p1',limit=limit,usedChecks=limit)
    failure('ALLOWANCE_EXHAUSTED',lambda:a.prepare(e,PAYLOAD,'cap'))
    change('authority','PERIOD#p1',usedChecks=0)
    if basis=='trial':
        change('authority','ACCESS',activationKind='automatic')
        failure('AUTHORITY_STATE_INVALID',lambda:a.prepare(e,PAYLOAD,'no-auto-trial'))

@pytest.mark.parametrize('target,attrs',[
    ('users',{'status':'DELETING'}),('deletion',{'state':'DELETING'}),
    ('pointer',{'stateVersion':2}),('grant',{'revision':2}),('grant',{'basis':'trial'}),
])
def test_atomic_revocation_between_read_and_admission(world,monkeypatch,target,attrs):
    a,e,_,row,change,_=world
    cid=a.prepare(e,PAYLOAD,'race')
    original=a.client.transact_write_items
    fired=[False]
    def racing(**kwargs):
        if any('Put' in op and op['Put']['Item'].get('recordType')=='V1_CHECK_RECEIPT' for op in kwargs['TransactItems']):
            table,sk={'users':('users','PROFILE'),'deletion':('deletion','ACCOUNT_DELETION'),'pointer':('devices','ACTIVE_BINDING'),'grant':('authority','ACCESS')}[target]
            change(table,sk,**attrs);fired[0]=True
        return original(**kwargs)
    monkeypatch.setattr(a.client,'transact_write_items',racing)
    failure('TRANSACTION_UNCERTAIN',lambda:a.admit(e,PAYLOAD,cid))
    assert fired[0] and row('CHECK#'+cid) is None and row('PERIOD#p1')['reservedChecks']==0

def test_account_deletion_blocks_worker_atomic_write(world):
    a,_,put,row,_,_=world
    cid,op=admit(world)
    put('deletion','ACCOUNT_DELETION',state='DELETING')
    failure('ACCOUNT_UNAVAILABLE',lambda:a.settle(WORKER,cid,op['executionToken'],'complete'))
    assert row('PERIOD#p1')['usedChecks']==0

def test_expired_purged_receipt_never_readmits(world):
    a,e,_,row,_,clock=world
    cid,op=admit(world)
    a.ddb.Table('authority').delete_item(Key={'PK':a._partition(ACCOUNT,'k1'),'SK':'CHECK#'+cid})
    clock[0]+=61
    failure('OPERATION_EXPIRED',lambda:a.admit(e,PAYLOAD,cid))
    assert a.reconcile(e,cid)=={'state':'UNKNOWN','chargedChecks':None}

def test_missing_pointer_no_legacy_fallback(world):
    a,e,*_=world
    a.ddb.Table('devices').delete_item(Key={'PK':'USER#'+ACCOUNT,'SK':'ACTIVE_BINDING'})
    failure('ACTIVE_DEVICE_REQUIRED',lambda:a.prepare(e,PAYLOAD,'legacy'))

def test_legacy_balance_never_grants(world):
    a,e,put,*_=world
    put('authority','ACCESS',subscriptionTier='PRO',monthlyChecksRemaining=999,isAccessGranted=True)
    failure('EXTERNAL_ACCESS_UNAVAILABLE',lambda:a.prepare(e,PAYLOAD,'legacy'))

def test_attempt_cap_counts_malformed_without_customer_deduction(world):
    a,e,_,row,_,_=world
    a.s=replace(a.s,attempts_per_window=2)
    for _ in range(2): failure('INPUT_REJECTED',lambda:a.prepare(e,{},'bad'))
    failure('TRANSACTION_UNCERTAIN',lambda:a.prepare(e,PAYLOAD,'exhausted'))
    assert row('PERIOD#p1')['usedChecks']==0

def test_inflight_cap_atomic_rollback(world):
    a,e,_,row,_,_=world
    a.s=replace(a.s,max_inflight=1)
    admit(world)
    cid=a.prepare(e,PAYLOAD,'second')
    failure('TRANSACTION_UNCERTAIN',lambda:a.admit(e,PAYLOAD,cid))
    assert row('CHECK#'+cid) is None and row('PERIOD#p1')['reservedChecks']==1

def test_receipt_contains_no_raw_payload_or_account(world):
    a,_,_,row,_,_=world
    cid,_=admit(world)
    text=str(row('CHECK#'+cid))
    assert ACCOUNT not in text and PAYLOAD['target']['url'] not in text and 'synthetic-not-secret' not in repr(a.s)
    rotated=replace(a.s,active_key_id='k2',hmac_keys=a.s.hmac_keys|{'k2':b'other-synthetic-key-00000000000000'})
    b=Authority(rotated,a.ddb,now=a.now)
    assert len(b.deletion_partitions(ACCOUNT))==2
    assert a.deletion_partitions(ACCOUNT)[0] in b.deletion_partitions(ACCOUNT)

@pytest.mark.parametrize('patch',[{'enabled':False},{'policy_version':'legacy'},{'receipt_retention_seconds':479},{'counter_retention_seconds':100},{'hmac_keys':{}},{'attempts_per_window':True}])
def test_configuration_has_no_implicit_policy(world,patch):
    a,*_=world
    failure('POLICY_CONFIGURATION_UNAVAILABLE',lambda:Authority(replace(a.s,**patch),a.ddb,now=a.now))
    assert VIRUS_TOTAL_ENABLED is False


def test_client_json_is_not_worker_authority(world):
    a,e,*_=world
    cid,op=admit(world)
    failure('TRUSTED_WORKER_REQUIRED',lambda:a.settle({'account':ACCOUNT},cid,op['executionToken'],'complete'))
    assert ACCOUNT not in repr(WORKER)


def test_ambiguous_success_never_returns_execution_to_retry(world,monkeypatch):
    a,e,_,row,_,_=world
    cid=a.prepare(e,PAYLOAD,'uncertain')
    original=a.client.transact_write_items
    def lost_response(**kwargs):
        result=original(**kwargs)
        if any('Put' in op and op['Put']['Item'].get('recordType')=='V1_CHECK_RECEIPT' for op in kwargs['TransactItems']):
            raise TimeoutError('synthetic lost response')
        return result
    monkeypatch.setattr(a.client,'transact_write_items',lost_response)
    result=a.admit(e,PAYLOAD,cid)
    assert result['admitted'] is False and 'executionToken' not in result
    assert row('PERIOD#p1')['reservedChecks']==1
    assert a.admit(e,PAYLOAD,cid)['admitted'] is False


def test_ambiguous_settlement_does_not_charge_twice(world,monkeypatch):
    a,_,_,row,_,_=world
    cid,op=admit(world)
    original=a.client.transact_write_items
    def lost_response(**kwargs):
        original(**kwargs)
        raise TimeoutError('synthetic lost response')
    monkeypatch.setattr(a.client,'transact_write_items',lost_response)
    result=a.settle(WORKER,cid,op['executionToken'],'complete')
    assert result['chargedChecks']==1 and row('PERIOD#p1')['usedChecks']==1
    assert a.settle(WORKER,cid,op['executionToken'],'complete')==result


def test_worker_deadline_requires_recovery(world):
    a,_,_,row,_,clock=world
    cid,op=admit(world)
    clock[0]+=a.s.worker_settlement_seconds
    failure('RECONCILIATION_REQUIRED',lambda:a.settle(WORKER,cid,op['executionToken'],'complete'))
    assert row('PERIOD#p1')['usedChecks']==0


def test_sdk_retries_must_be_disabled(world):
    a,*_=world
    resource=boto3.resource('dynamodb',region_name='us-east-1',aws_access_key_id='synthetic',aws_secret_access_key='synthetic',config=Config(retries={'total_max_attempts':2}))
    failure('SDK_RETRY_CONFIGURATION_UNAVAILABLE',lambda:Authority(a.s,resource,now=a.now))


def test_unicode_malformed_counts_as_rejected_input(world):
    a,e,*_=world
    payload=deepcopy(PAYLOAD);payload['target']['url']='https://example.com/\ud800'
    failure('INPUT_REJECTED',lambda:a.prepare(e,payload,'malformed'))


def test_seventh_day_trial_expired(world):
    a,e,_,_,change,clock=world
    change('authority','ACCESS',basis='trial',validFromEpoch=clock[0]-TRIAL_SECONDS,validUntilEpoch=clock[0],activationKind='explicit',activatedAtEpoch=clock[0]-TRIAL_SECONDS)
    failure('EXTERNAL_ACCESS_UNAVAILABLE',lambda:a.prepare(e,PAYLOAD,'expired-trial'))
