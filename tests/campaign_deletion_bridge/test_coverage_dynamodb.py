"""Read-only coverage using actual SDK/Moto tables and a fixed synthetic KMS."""
import os
import sys
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest

if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run isolated with actual SDK/Moto', allow_module_level=True)
import boto3
from moto import mock_aws
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'src/campaign_deletion_bridge'))
from coverage_assessment import assess_account_coverage
from progress import wire, key, plain
from service import PERIOD_SECONDS, RECOVERY_SECONDS
from shared_campaign_locators.core import WRITERS
import base64

NOW = PERIOD_SECONDS * 1500 + 100
ACCOUNT = 'synthetic-owner-sensitive'
OP = '69a43d58-54d1-4edc-9941-8a37a5ab8d79'
ARN = 'arn:aws:kms:us-east-1:107827791950:key/12345678-1234-4234-8234-123456789abc'
TOKEN = base64.urlsafe_b64encode(b'x'*32).decode().rstrip('=')
CMD = {'PK':'ACCOUNT#'+ACCOUNT,'SK':'ACCOUNT_DELETION','schemaVersion':1,'recordVersion':1,
       'environment':'dev','eventType':'account.deletion.requested','accountId':ACCOUNT,
       'status':'REQUESTED','occurredAtEpoch':NOW,'deleteByEpoch':NOW+86400,'operationId':OP}
INV = {'PK':'INVENTORY#dev','SK':'CAMPAIGN_LOCATORS','recordType':'CAMPAIGN_LOCATOR_INVENTORY',
       'schemaVersion':1,'revision':1,'environment':'dev','coverage':'VERIFIED_COMPLETE',
       'manifestSha256':'a'*64,'approvedAtEpoch':NOW-100,'locatorSchemaVersion':1,
       'minimumPeriodId':1498,'priorPeriodsErased':True,'writers':WRITERS}


def partition(period): return f'CONTRIB#{period}#{TOKEN}'
def tomb(period):
    return {'PK':partition(period),'SK':'TOMBSTONE','recordType':'CAMPAIGN_DELETION_TOMBSTONE',
            'schemaVersion':2,'environment':'dev','periodId':period,'createdAtEpoch':NOW,
            'deletionDeadlineEpoch':NOW+21*86400,'GSI3PK':'EXPIRY#dev','GSI3SK':NOW+21*86400,
            'locatorCleanupRevision':1,'locatorCleanupState':{'operationId':OP,'phase':'SEEK','cursor':None}}


@pytest.fixture
def world():
    with mock_aws():
        d = boto3.client('dynamodb',region_name='us-east-1')
        for table in ('pipeline','ledger'):
            d.create_table(TableName=table,BillingMode='PAY_PER_REQUEST',
                KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],
                AttributeDefinitions=[{'AttributeName':k,'AttributeType':'S'} for k in ('PK','SK')])
        def put(row,table='pipeline'):d.put_item(TableName=table,Item=wire(row))
        put(INV);put(CMD,'ledger')
        for period in range(1498,1501):
            put({'PK':f'PERIOD#{period}','SK':'HMAC_KEY','keyArn':ARN,'status':'ENABLED','periodId':period,
                 'retireAfterEpoch':(period+1)*PERIOD_SECONDS+RECOVERY_SECONDS})
            put(tomb(period))
        calls=[]
        def mac(**kwargs):
            calls.append(kwargs)
            return {'Mac':b'x'*32,'KeyId':ARN,'MacAlgorithm':'HMAC_SHA_256'}
        def snapshot():return {table:sorted([plain(r) for r in d.scan(TableName=table)['Items']],key=lambda r:(r['PK'],r['SK'])) for table in ('pipeline','ledger')}
        yield SimpleNamespace(d=d,put=put,kms=SimpleNamespace(generate_mac=mac),calls=calls,snapshot=snapshot)


class ReadOnly:
    def __init__(self, client):self.client=client;self.calls=[]
    def get_item(self,**kwargs):
        assert kwargs['ConsistentRead'] is True
        self.calls.append(('get',kwargs));return self.client.get_item(**kwargs)
    def query(self,**kwargs):
        assert kwargs['ConsistentRead'] is True and 'IndexName' not in kwargs
        self.calls.append(('query',kwargs));return self.client.query(**kwargs)
    def __getattr__(self,name):raise AssertionError('non-read operation requested: '+name)


def assess(w,**overrides):
    args=dict(command=deepcopy(CMD),environment='dev',aws_account_id='107827791950',aws_region='us-east-1',
              table_name='pipeline',deletion_ledger_table_name='ledger',dynamodb=ReadOnly(w.d),kms=w.kms,
              locator_manifest_sha256='a'*64,locator_inventory_revision=1,now_epoch=NOW+1)
    result=assess_account_coverage(**(args|overrides))
    assert result['complete'] is False and result['receiptEligible'] is False
    serialized=json.dumps(result)
    assert all(value not in serialized for value in (ACCOUNT,OP,TOKEN,ARN))
    return result


def locator(period):
    ident=str(uuid4());deadline=NOW-1
    return {'PK':partition(period),'SK':'LOCATOR#EVENT#'+ident,'recordType':'CAMPAIGN_CONTRIBUTOR_LOCATOR',
            'schemaVersion':1,'environment':'dev','periodId':period,'targetKind':'FEATURE',
            'targetPK':'EVENT#'+ident,'targetSK':'FEATURE','targetExpiresAtEpoch':deadline,
            'GSI3PK':'EXPIRY#dev','GSI3SK':deadline}


def test_all_inventory_periods_including_older_than_two_are_observed_without_mutation(world):
    before=world.snapshot();result=assess(world)
    assert result['periodsExpected']==result['periodsExamined']==result['observedEmptyPeriods']==3
    assert result['rangeExamined'] is True and len(world.calls)==3
    assert result['reasons']==['COMPLETION_PROOF_UNQUALIFIED']
    assert world.snapshot()==before


def test_old_period_locator_is_pending_even_when_target_logically_expired_or_missing(world):
    world.put(locator(1498));before=world.snapshot();r=assess(world)
    assert r['pendingPeriods']==1 and r['observedEmptyPeriods']==2
    assert 'LOCATORS_OR_REPAIR_PENDING' in r['reasons']
    assert world.snapshot()==before


@pytest.mark.parametrize('mode,reason',[('missing','PERIOD_KEY_MISSING'),('retired','PERIOD_RETIREMENT_UNPROVEN'),
    ('foreign','PERIOD_KEY_UNVERIFIED'),('mismatched_period','PERIOD_KEY_UNVERIFIED'),('malformed','PERIOD_KEY_UNVERIFIED')])
def test_missing_retired_or_unknown_old_key_is_not_erasure(world,mode,reason):
    k=key('PERIOD#1498','HMAC_KEY');old=plain(world.d.get_item(TableName='pipeline',Key=k)['Item'])
    if mode=='missing':world.d.delete_item(TableName='pipeline',Key=k)
    else:
        old.update({'status':'RETIRED'} if mode=='retired' else {'keyArn':ARN.replace('107827791950','111111111111')} if mode=='foreign' else {'periodId':1499} if mode=='mismatched_period' else {'unknown':'private'})
        world.put(old)
    r=assess(world);assert r['unverifiedPeriods']==1 and r['rangeExamined'] is False and reason in r['reasons']
    assert len(world.calls)==2


@pytest.mark.parametrize('change',[{'priorPeriodsErased':False},{'revision':2},{'coverage':'UNKNOWN'}, {'unknown':'private'}])
def test_unqualified_inventory_prevents_any_period_work(world,change):
    world.put(INV|change);r=assess(world)
    assert r['reasons']==['COMPLETION_PROOF_UNQUALIFIED','INVENTORY_UNVERIFIED']
    assert not world.calls


def test_period_budget_is_checked_before_any_mac_or_partial_success(world):
    world.put(INV|{'minimumPeriodId':1490});r=assess(world,max_periods=8)
    assert r['periodsExpected']==11 and r['periodsExamined']==0
    assert 'PERIOD_BUDGET_EXCEEDED' in r['reasons'] and not world.calls


def test_strong_paginated_partition_and_page_budget(world):
    for _ in range(5):world.put(locator(1498))
    before=world.snapshot();r=assess(world,page_size=2)
    assert r['pendingPeriods']==1 and r['pagesRead']>=5 and r['rangeExamined'] is True
    limited=assess(world,page_size=2,max_pages=1)
    assert limited['pagesRead']==1 and limited['rangeExamined'] is False
    assert 'PAGE_BUDGET_EXCEEDED' in limited['reasons']
    assert world.snapshot()==before


@pytest.mark.parametrize('mode,reason',[('absent','PARTITION_NOT_FENCED'),('legacy_ttl','REPAIR_STATE_UNVERIFIED'),
    ('unknown_state','REPAIR_STATE_UNVERIFIED'),('pending','LOCATORS_OR_REPAIR_PENDING'),('other_operation','LOCATORS_OR_REPAIR_PENDING')])
def test_fence_and_pending_state_are_not_assumed_complete(world,mode,reason):
    row=tomb(1498)
    if mode=='absent':world.d.delete_item(TableName='pipeline',Key=key(row['PK'],row['SK']))
    else:
        if mode=='legacy_ttl':row['expiresAt']=NOW+100
        if mode=='unknown_state':row['locatorCleanupState']['phase']='COMPLETE'
        if mode=='other_operation':row['locatorCleanupState']['operationId']=str(uuid4())
        if mode=='pending':row['locatorCleanupState']['cursor']='LOCATOR#EVENT#'+str(uuid4())
        world.put(row)
    r=assess(world);assert reason in r['reasons'] and r['observedEmptyPeriods']==2


def test_unknown_partition_row_cannot_be_filtered_away(world):
    world.put({'PK':partition(1498),'SK':'LEGACY#private','value':'secret'})
    r=assess(world);assert r['unverifiedPeriods']==1 and 'PARTITION_UNVERIFIED' in r['reasons']
    assert 'private' not in json.dumps(r)


@pytest.mark.parametrize('changed',['command','inventory','key','tombstone'])
def test_changed_evidence_invalidates_range_observation(world,changed):
    class Race(ReadOnly):
        raced=False
        def query(self,**kwargs):
            result=super().query(**kwargs)
            if not self.raced:
                self.raced=True
                if changed=='command':world.put(CMD|{'status':'COMPLETE'},'ledger')
                elif changed=='inventory':world.put(INV|{'revision':2})
                elif changed=='tombstone':world.put(tomb(1498)|{'locatorCleanupRevision':2})
                else:
                    row=plain(world.d.get_item(TableName='pipeline',Key=key('PERIOD#1498','HMAC_KEY'))['Item']);world.put(row|{'status':'RETIRED'})
            return result
    r=assess(world,dynamodb=Race(world.d))
    assert r['rangeExamined'] is False and 'OBSERVATION_CHANGED' in r['reasons']


def test_time_budget_stops_without_next_period_access(world):
    r=assess(world,remaining_ms=lambda:2000)
    assert r['periodsExamined']==0 and not world.calls and 'TIME_BUDGET_EXCEEDED' in r['reasons']


@pytest.mark.parametrize('kind',['exception','wrong_key','wrong_algorithm','wrong_mac'])
def test_kms_unavailable_or_mismatched_response_has_fixed_diagnostic(world,kind):
    def mac(**kwargs):
        if kind=='exception':raise RuntimeError(ACCOUNT+' privatekey')
        return {'KeyId':ARN if kind!='wrong_key' else 'other','MacAlgorithm':'HMAC_SHA_256' if kind!='wrong_algorithm' else 'other','Mac':b'x'*(31 if kind=='wrong_mac' else 32)}
    r=assess(world,kms=SimpleNamespace(generate_mac=mac))
    assert r['unverifiedPeriods']==3 and 'PERIOD_KEY_UNAVAILABLE' in r['reasons']
    assert 'privatekey' not in json.dumps(r)


def test_stale_or_terminal_command_never_authorizes_observation(world):
    world.put(CMD|{'status':'COMPLETE'},'ledger');r=assess(world)
    assert 'COMMAND_CHANGED' in r['reasons'] and not world.calls


def test_command_before_inventory_approval_is_not_rebound(world):
    world.put(INV|{'approvedAtEpoch':NOW});r=assess(world)
    assert 'COMMAND_PREDATES_INVENTORY' in r['reasons'] and not world.calls


@pytest.mark.parametrize('phase',['DELETE','RECOMPUTE'])
def test_pending_locator_repair_blocks_empty_classification_even_after_locator_deleted(world,phase):
    row=tomb(1498)
    row['locatorCleanupState']={'operationId':OP,'phase':phase,'cursor':None,
        'locator':locator(1498),'nextCursor':None}
    world.put(row);r=assess(world)
    assert r['pendingPeriods']==1 and r['observedEmptyPeriods']==2
    assert 'LOCATORS_OR_REPAIR_PENDING' in r['reasons']


@pytest.mark.parametrize('cursor',['LOCATOR#EVENT#------------------------------------','LOCATOR#EVENT#00000000-0000-1000-8000-000000000000','private-raw-token'])
def test_malformed_repair_cursor_is_unverified_not_persisted_or_echoed(world,cursor):
    row=tomb(1498);row['locatorCleanupState']['cursor']=cursor;world.put(row)
    before=world.snapshot();r=assess(world)
    assert 'REPAIR_STATE_UNVERIFIED' in r['reasons'] and r['unverifiedPeriods']==1
    assert cursor not in json.dumps(r) and world.snapshot()==before


def test_denied_storage_returns_only_fixed_reason(world):
    class Denied:
        def get_item(self,**kwargs):raise RuntimeError(ACCOUNT+' secret')
    r=assess(world,dynamodb=Denied())
    assert r['reasons']==['COMPLETION_PROOF_UNQUALIFIED','EVIDENCE_UNAVAILABLE']
    assert not world.calls


def test_missing_or_unknown_config_never_calls_external_client(world):
    r=assess(world,max_periods=True)
    assert r['reasons']==['COMPLETION_PROOF_UNQUALIFIED','CONFIGURATION_UNVERIFIED']
    assert not world.calls


@pytest.mark.parametrize('allowed_final_reads',[0,1])
def test_expired_budget_before_or_during_final_rereads_stops_sdk_calls(world,allowed_final_reads):
    class Timed(ReadOnly):
        queries=0
        final_reads=0
        def query(self,**kwargs):
            result=super().query(**kwargs);self.queries+=1;return result
        def get_item(self,**kwargs):
            if self.queries==3:
                assert self.final_reads < allowed_final_reads
                self.final_reads+=1
            return super().get_item(**kwargs)
    client=Timed(world.d)
    remaining=lambda: 2000 if client.queries==3 and client.final_reads>=allowed_final_reads else 10000
    r=assess(world,dynamodb=client,remaining_ms=remaining)
    assert r['rangeExamined'] is False and 'TIME_BUDGET_EXCEEDED' in r['reasons']
    assert client.final_reads==allowed_final_reads
