"""Synthetic fixtures only. Moto intercepts AWS; provider/HTTP calls are local fakes."""
import json
import sys
from pathlib import Path
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import boto3
from botocore.config import Config
from moto import mock_aws
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'src/url_assessment'), str(ROOT/'tests/shared_check_authority')]
from shared_url_qualification import Policy, Closed, load_policy, require_subject
from shared_url_qualification.store import Store
from shared_url_qualification.cleanup import sweep
from shared_lookup_freshness import observed
from assessment_service import Budget
from url_redirect_resolver.resolver import resolve, Deadline

NOW = 1800000000
URL = 'https://example.com/'
SUB = '31902877-bcce-4d05-a18b-8e3f58410101'


def make_table(ddb, name='qualification'):
    ddb.create_table(TableName=name, BillingMode='PAY_PER_REQUEST',
        KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],
        AttributeDefinitions=[{'AttributeName':k,'AttributeType':'S'} for k in ('PK','SK','GSI1PK','GSI1SK')],
        GlobalSecondaryIndexes=[{'IndexName':'GSI1','KeySchema':[{'AttributeName':'GSI1PK','KeyType':'HASH'},{'AttributeName':'GSI1SK','KeyType':'RANGE'}],
            'Projection':{'ProjectionType':'INCLUDE','NonKeyAttributes':['expiresAt']}}])


@pytest.fixture
def store():
    with mock_aws():
        ddb = boto3.resource('dynamodb', region_name='us-east-1', aws_access_key_id='synthetic', aws_secret_access_key='synthetic',
                             config=Config(retries={'total_max_attempts':1}))
        make_table(ddb)
        clock = [NOW]
        policy = Policy(str(NOW-1)+'_fixture', NOW-1, NOW+3599, {'safe':URL}, 'qualification', True, True, True, lambda:clock[0])
        sweep(ddb, 'qualification', NOW)
        yield Store(policy,ddb), clock


def no_match():
    return {'threatTypes':[], 'observedAt':observed(NOW), 'validUntil':None}


def match(end=NOW+60):
    return {'threatTypes':['MALWARE'], 'observedAt':observed(NOW), 'validUntil':observed(end)}


def test_all_controls_default_closed(monkeypatch):
    monkeypatch.setenv('STAGE','dev')
    manifest={'version':1,'runId':str(NOW)+'_fixture','startEpoch':NOW,'endEpoch':NOW+60,'syntheticOnly':True,'targets':[{'id':'safe','url':URL}]}
    monkeypatch.setenv('URL_QUALIFICATION_MANIFEST_JSON',json.dumps(manifest))
    monkeypatch.setenv('URL_QUALIFICATION_TABLE_NAME','qualification')
    with pytest.raises(Closed):load_policy(now=lambda:NOW)
    monkeypatch.setenv('URL_QUALIFICATION_ENABLED','true')
    policy=load_policy(now=lambda:NOW)
    assert not policy.admission and not policy.provider and not policy.retention
    with pytest.raises(Closed):policy.require_admission(URL)
    with pytest.raises(Closed):policy.require_provider(URL)
    monkeypatch.setenv('URL_RETENTION_APPROVED','true')
    manifest['endEpoch']=NOW+3601
    monkeypatch.setenv('URL_QUALIFICATION_MANIFEST_JSON',json.dumps(manifest))
    with pytest.raises(Closed):load_policy(now=lambda:NOW)
    manifest['endEpoch']=NOW+60;manifest['runId']=str(NOW+1)+'_reuse'
    monkeypatch.setenv('URL_QUALIFICATION_MANIFEST_JSON',json.dumps(manifest))
    with pytest.raises(Closed):load_policy(now=lambda:NOW)


def test_subject_scope_is_independent_and_ignores_body(monkeypatch):
    import time
    for key,value in {'STAGE':'dev','URL_SUBJECT_ALLOWLIST_JSON':json.dumps([SUB]),'DEV_SUBJECT_ALLOWLIST_JSON':'[]',
        'COGNITO_ISSUER':'https://cognito-idp.us-east-1.amazonaws.com/synthetic','COGNITO_APP_CLIENT_ID':'syntheticclient',
        'COGNITO_REQUIRED_SCOPE':'aws.cognito.signin.user.admin'}.items():monkeypatch.setenv(key,value)
    event={'requestContext':{'authorizer':{'jwt':{'claims':{'sub':SUB,'iss':__import__('os').environ['COGNITO_ISSUER'],
        'client_id':'syntheticclient','scope':'aws.cognito.signin.user.admin','token_use':'access','exp':str(int(time.time())+60)}}}}}
    assert require_subject(event)==SUB
    with pytest.raises(Closed):require_subject({'body':json.dumps({'sub':SUB})})
    from shared_check_authority.engineering import require_engineering_subject
    with pytest.raises(Exception):require_engineering_subject(event)


def test_match_cache_preserves_original_clocks_and_no_match_is_not_stored(store):
    s,clock=store
    result=match()
    assert s.lookup(URL,Budget(10),lambda:result)==result
    clock[0]+=20
    assert s.lookup(URL,Budget(10),lambda:pytest.fail('cache refetch'))==result
    assert s.table.get_item(Key=s.key('BUDGET'))['Item']['providerAttempts']==1
    clock[0]=NOW+60
    assert s.lookup(URL,Budget(10),no_match)==no_match()
    assert s.table.get_item(Key=s.key('BUDGET'))['Item']['providerAttempts']==2


def test_hard_sixty_permits_no_refund_or_retry(store):
    s,_=store
    calls=[]
    for _ in range(60):s.lookup(URL,Budget(10),lambda:calls.append(1) or no_match())
    with pytest.raises(Closed):s.lookup(URL,Budget(10),lambda:pytest.fail('cap overrun'))
    assert len(calls)==60 and s.table.get_item(Key=s.key('BUDGET'))['Item']['providerAttempts']==60


def test_unknown_call_or_budget_response_retains_lock(store,monkeypatch):
    s,_=store
    def broken():raise TimeoutError('synthetic')
    with pytest.raises(TimeoutError):s.lookup(URL,Budget(10),broken)
    with pytest.raises(Closed):s.lookup(URL,Budget(10),lambda:pytest.fail('ambiguous refetch'))
    assert s.table.get_item(Key=s.key('BUDGET'))['Item']['providerAttempts']==1


def test_match_cache_persistence_failure_keeps_match_and_blocks_refetch(store,monkeypatch):
    s,clock=store
    original=s.client.transact_write_items
    def fail_cache(**kwargs):
        if any('Put' in action for action in kwargs['TransactItems']):raise TimeoutError('synthetic')
        return original(**kwargs)
    monkeypatch.setattr(s.client,'transact_write_items',fail_cache)
    result=match()
    assert s.lookup(URL,Budget(10),lambda:result)==result
    clock[0]+=61
    with pytest.raises(Closed):s.lookup(URL,Budget(10),lambda:pytest.fail('cache failure refetch'))


def test_lifetime_over_seven_days_not_persisted_but_match_preserved(store):
    s,_=store
    result=match(NOW+604801)
    assert s.lookup(URL,Budget(10),lambda:result)==result
    assert 'Item' not in s.table.get_item(Key=s.key('MATCH#safe'))
    with pytest.raises(Closed):s.lookup(URL,Budget(10),lambda:pytest.fail('oversized refetch'))


def test_single_flight_concurrent_caller(store):
    s,_=store
    entered,finish=Event(),Event()
    def slow():
        entered.set();assert finish.wait(3);return match()
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(s.lookup,URL,Budget(10),slow)
        assert entered.wait(3)
        try:
            with pytest.raises(Closed,match='LOOKUP_BUSY'):s.lookup(URL,Budget(10),lambda:pytest.fail('duplicate'))
        finally:finish.set()
        assert future.result()==match()
    assert s.lookup(URL,Budget(10),lambda:pytest.fail('postflight duplicate'))==match()


def test_freshness_window_target_and_manifest_rebind_fail_closed(store):
    s,clock=store
    with pytest.raises(Closed):s.lookup(URL+'private',Budget(10),lambda:pytest.fail('nonmanifest'))
    clock[0]=NOW+901
    with pytest.raises(Closed,match='CLEANUP_CLOSED'):s.lookup(URL,Budget(10),lambda:pytest.fail('cleanup stale'))
    clock[0]=NOW
    s.lookup(URL,Budget(10),lambda:match())
    changed=Store(replace(s.p,targets={'safe':URL+'other'}),s.ddb)
    with pytest.raises(Closed):changed.lookup(URL+'other',Budget(10),lambda:pytest.fail('manifest rebound'))
    clock[0]=s.p.end
    with pytest.raises(Closed):s.lookup(URL,Budget(10),lambda:pytest.fail('window ended'))


def test_redirect_scope_enforced_before_dns_and_http():
    class Transport:
        def __init__(self):self.hosts=[];self.fetched=[]
        def addresses(self,host,deadline):self.hosts.append(host);return ['93.184.215.14']
        def fetch(self,target,address,deadline):self.fetched.append(target.url);return 302,'https://unapproved.example/'
    transport=Transport()
    def approved(url):
        if url!=URL:raise Closed()
    value=resolve(URL,transport,Deadline(10),authorize=approved)
    assert value['reasonCodes']==['QUALIFICATION_CLOSED']
    assert transport.hosts==['example.com'] and transport.fetched==[URL]
    # Existing URL/SSRF validation still precedes the callback/network.
    transport=Transport()
    value=resolve('http://127.0.0.1/',transport,Deadline(10),authorize=approved)
    assert not transport.hosts and value['resolutionStatus']=='blocked'


def test_cleanup_bounded_preserves_active_lock_and_budget_until_deadline(store):
    s,clock=store
    owner=s.acquire('safe')
    s.table.put_item(Item=s.key('MATCH#old')|s.expiry(NOW-1)|match(NOW-1))
    assert sweep(s.ddb,'qualification',NOW)=={'deleted':1,'complete':True}
    assert s.table.get_item(Key=s.key('LOCK#safe'))['Item']['owner']==owner
    for index in range(205):s.table.put_item(Item=s.key('MATCH#'+str(index))|s.expiry(NOW-1))
    assert sweep(s.ddb,'qualification',NOW+1)=={'deleted':200,'complete':False}
    assert s.table.get_item(Key={'PK':'CONTROL','SK':'CLEANUP'})['Item']['lastSweepEpoch']==NOW
    assert sweep(s.ddb,'qualification',NOW+2)=={'deleted':5,'complete':True}


def test_store_rows_have_no_url_subject_check_proof_or_binding(store):
    s,_=store
    s.lookup(URL,Budget(10),match)
    raw=json.dumps(s.table.scan()['Items'],default=str)
    for value in (URL,SUB,'operationProof','clientCheckId','account','binding','checkId'):
        assert value not in raw


def test_committed_but_lost_provider_permit_never_calls_provider(store,monkeypatch):
    s,_=store
    original=s.client.transact_write_items
    def lost(**kwargs):
        original(**kwargs)
        raise TimeoutError('synthetic lost response')
    monkeypatch.setattr(s.client,'transact_write_items',lost)
    with pytest.raises(Closed):s.lookup(URL,Budget(10),lambda:pytest.fail('uncertain budget request'))
    assert s.table.get_item(Key=s.key('BUDGET'))['Item']['providerAttempts']==1
    with pytest.raises(Closed):s.lookup(URL,Budget(10),lambda:pytest.fail('lost budget retry'))


def test_cleanup_conditional_uncertainty_does_not_advance_heartbeat(store,monkeypatch):
    s,_=store
    s.table.put_item(Item=s.key('MATCH#expired')|s.expiry(NOW-1))
    original=s.ddb.Table
    table=original('qualification')
    def uncertain(**kwargs):
        raise s.client.exceptions.ConditionalCheckFailedException({'Error':{'Code':'ConditionalCheckFailedException'}},'DeleteItem')
    monkeypatch.setattr(table,'delete_item',uncertain)
    monkeypatch.setattr(s.ddb,'Table',lambda _:table)
    assert sweep(s.ddb,'qualification',NOW+1)=={'deleted':0,'complete':False}
    assert table.get_item(Key={'PK':'CONTROL','SK':'CLEANUP'})['Item']['lastSweepEpoch']==NOW


def test_explicit_off_or_partial_qualification_config_cannot_fall_back_to_legacy_provider(monkeypatch):
    from shared_url_qualification import configured
    from assessment_dependencies import Dependencies
    monkeypatch.setenv('STAGE','dev')
    monkeypatch.setenv('URL_PROVIDER_ENABLED','false')
    assert configured()
    with pytest.raises(Closed):Dependencies()
    monkeypatch.setenv('URL_QUALIFICATION_ENABLED','false')
    with pytest.raises(Closed):Dependencies()


def test_fractional_provider_expiry_is_not_physically_deleted_early(store):
    from decimal import Decimal
    s,clock=store
    result=match()
    result['validUntil']=observed(NOW+60).replace('Z','.900000001Z')
    assert s.lookup(URL,Budget(10),lambda:result)==result
    row=s.table.get_item(Key=s.key('MATCH#safe'))['Item']
    assert row['expiresAt']==NOW+61
    clock[0]=Decimal(NOW+60)+Decimal('0.5')
    assert sweep(s.ddb,'qualification',NOW+60)=={'deleted':0,'complete':True}
    assert s.lookup(URL,Budget(10),lambda:pytest.fail('early fractional refetch'))==result
    clock[0]=Decimal(NOW+60)+Decimal('0.900000001')
    assert s.lookup(URL,Budget(10),no_match)==no_match()
    assert s.table.get_item(Key=s.key('BUDGET'))['Item']['providerAttempts']==2
    assert sweep(s.ddb,'qualification',NOW+61)=={'deleted':1,'complete':True}


@pytest.mark.parametrize('subjects',[[],['*'],[SUB,SUB],[SUB,'0bc48d13-2715-46a4-8278-72d977dd9900'],[SUB.upper()]])
def test_only_one_canonical_url_subject_is_admissible(monkeypatch,subjects):
    import time
    for key,value in {'STAGE':'dev','URL_SUBJECT_ALLOWLIST_JSON':json.dumps(subjects),
        'COGNITO_ISSUER':'https://cognito-idp.us-east-1.amazonaws.com/synthetic','COGNITO_APP_CLIENT_ID':'syntheticclient',
        'COGNITO_REQUIRED_SCOPE':'aws.cognito.signin.user.admin'}.items():monkeypatch.setenv(key,value)
    event={'requestContext':{'authorizer':{'jwt':{'claims':{'sub':SUB,
        'iss':'https://cognito-idp.us-east-1.amazonaws.com/synthetic','client_id':'syntheticclient',
        'scope':'aws.cognito.signin.user.admin','token_use':'access','exp':str(int(time.time())+60)}}}}}
    with pytest.raises(Closed):require_subject(event)
