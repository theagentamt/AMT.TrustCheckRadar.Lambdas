"""Isolated actual SDK conditions + Moto; no AWS credentials or live provider calls."""
import os
import sys
import base64
import importlib.util
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run isolated with AMT_AUTHORITY_INTEGRATION=1',allow_module_level=True)
import boto3
from moto import mock_aws
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/campaign_deletion_bridge'))
import progress
import locator_progress
from shared_campaign_locators import locator_for_target, locator_pointer
from uuid import UUID
spec=importlib.util.spec_from_file_location('deletion_service',ROOT/'src/campaign_deletion_bridge/service.py')
service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service)
NOW=10*14*86400+8*86400
TOKEN=base64.urlsafe_b64encode(b'x'*32).decode().rstrip('=')
PART=f'CONTRIB#10#{TOKEN}'
def event_pk(n): return 'EVENT#'+str(UUID(int=n+1,version=4))
OP='47debb73-444b-4bb1-9889-fb56885b7922'
COMMAND={'PK':'ACCOUNT#a','SK':'ACCOUNT_DELETION','schemaVersion':1,'recordVersion':1,'environment':'dev',
         'eventType':'account.deletion.requested','accountId':'a','status':'REQUESTED','occurredAtEpoch':NOW,
         'deleteByEpoch':NOW+86400,'operationId':OP}

@pytest.fixture
def world():
    with mock_aws():
        d=boto3.client('dynamodb',region_name='us-east-1')
        for name in ('pipeline','ledger'):
            config=dict(TableName=name,KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],
                        AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}],BillingMode='PAY_PER_REQUEST')
            if name=='pipeline':
                config['AttributeDefinitions'] += [{'AttributeName':'GSI1PK','AttributeType':'S'},{'AttributeName':'GSI1SK','AttributeType':'S'}]
                config['GlobalSecondaryIndexes']=[{'IndexName':'ContributorPeriodIndex','KeySchema':[{'AttributeName':'GSI1PK','KeyType':'HASH'},{'AttributeName':'GSI1SK','KeyType':'RANGE'}],'Projection':{'ProjectionType':'ALL'}}]
            d.create_table(**config)
        put=lambda row,table='pipeline':d.put_item(TableName=table,Item=progress.wire(row))
        put(COMMAND,'ledger')
        put({'PK':'INVENTORY#dev','SK':'CAMPAIGN_LOCATORS','recordType':'CAMPAIGN_LOCATOR_INVENTORY','schemaVersion':1,
             'revision':1,'environment':'dev','coverage':'VERIFIED_COMPLETE','manifestSha256':'a'*64,'approvedAtEpoch':NOW-100,
             'locatorSchemaVersion':1,'minimumPeriodId':9,'priorPeriodsErased':True,'writers':['publisher','cluster','deletion_bridge','lifecycle']})
        put({'PK':'PERIOD#10','SK':'HMAC_KEY','keyArn':'synthetic','status':'ENABLED'})
        yield d,put


def run(d,**kwargs):
    return service.delete_account_contributions(COMMAND,table_name='pipeline',deletion_ledger_table_name='ledger',
        retention_days=21,dynamodb=d,kms=SimpleNamespace(generate_mac=lambda **_:{'Mac':b'x'*32}),now_epoch=NOW,locator_manifest_sha256="a"*64,locator_inventory_revision=1,**kwargs)


def feature(put,n):
    row={'PK':event_pk(n),'SK':'FEATURE','expiresAt':NOW+1000,'periodId':10,'GSI1PK':PART,'GSI1SK':event_pk(n)+'#FEATURE'}
    loc=locator_for_target(row,'dev');put(row);put(loc)
    for sk in ('DEDUPE','CLUSTERED'):
        put({'PK':event_pk(n),'SK':sk,'expiresAt':NOW+1000,**locator_pointer(loc)})


def candidate(put,count=61):
    put({'PK':'CANDIDATE#11111111-1111-4111-8111-111111111111','SK':'SUMMARY','version':1,'expiresAt':NOW+1000,'GSI3PK':'EXPIRY#dev',
         'GSI3SK':NOW+1000,'centroid':[Decimal('.9')],'contributorCount':999,'submissionCount':999})
    put({'PK':'CANDIDATE#11111111-1111-4111-8111-111111111111','SK':'CONTRIB#'+TOKEN,'GSI1PK':PART,'GSI1SK':'CANDIDATE#11111111-1111-4111-8111-111111111111',
         'periodId':10,'submissionCount':1,'vectorApplied':True,'vector':[Decimal('.9')],'expiresAt':NOW+1000})
    put(locator_for_target({'PK':'CANDIDATE#11111111-1111-4111-8111-111111111111','SK':'CONTRIB#'+TOKEN,'GSI1PK':PART,'periodId':10,'expiresAt':NOW+1000},'dev'))
    for i in range(count):
        put({'PK':'CANDIDATE#11111111-1111-4111-8111-111111111111','SK':f'CONTRIB#other{i:03}','submissionCount':2,'vectorApplied':True,
             'vector':[Decimal('.5')],'expiresAt':NOW+1000})


def test_multiple_invocations_resume_every_index_row_without_receipt(world):
    d,put=world
    for i in range(9):feature(put,i)
    saw_end=False
    for _ in range(40):
        result=run(d,max_steps=2)
        assert result['complete'] is False and result['coverage']=='LOCATOR_TRAVERSAL_ONLY'
        if result['locatorPassEnded']:
            saw_end=True;break
    assert saw_end
    for i in range(9):
        for sk in ('FEATURE','DEDUPE','CLUSTERED'):
            assert progress.get(d,'pipeline',event_pk(i),sk) is None
    assert progress.get(d,'ledger','ACCOUNT#a','ACCOUNT_DELETION#CAMPAIGN') is None
    tomb=progress.get(d,'pipeline',PART,'TOMBSTONE')
    assert tomb['deletionDeadlineEpoch']==NOW+21*86400 and 'expiresAt' not in tomb
    assert 'accountId' not in tomb and 'a' != tomb.get('PK')


def test_recompute_resumes_across_pages_without_partial_centroid(world):
    d,put=world;candidate(put)
    run(d,max_steps=4) # initialize, discover, delete, initialize recompute
    assert progress.get(d,'pipeline','CANDIDATE#11111111-1111-4111-8111-111111111111','SUMMARY')['contributorCount']==999
    assert progress.get(d,'pipeline','CANDIDATE#11111111-1111-4111-8111-111111111111','DELETION_RECOMPUTE')['expiresAt']==NOW+1000
    run(d,max_steps=1)
    assert progress.get(d,'pipeline','CANDIDATE#11111111-1111-4111-8111-111111111111','SUMMARY')['contributorCount']==999
    run(d,max_steps=1)
    assert progress.get(d,'pipeline','CANDIDATE#11111111-1111-4111-8111-111111111111','SUMMARY')['contributorCount']==999
    run(d,max_steps=1)
    summary=progress.get(d,'pipeline','CANDIDATE#11111111-1111-4111-8111-111111111111','SUMMARY')
    assert summary['contributorCount']==61 and summary['submissionCount']==122 and summary['centroid']==[Decimal('.5')]
    assert progress.get(d,'pipeline','CANDIDATE#11111111-1111-4111-8111-111111111111','DELETION_RECOMPUTE') is None


def test_lost_delete_acknowledgment_keeps_pending_recompute(world):
    d,put=world;candidate(put,1);run(d,max_steps=2)
    class Lost:
        def __getattr__(self,name):return getattr(d,name)
        def transact_write_items(self,**kwargs):
            d.transact_write_items(**kwargs)
            if any('Delete' in item for item in kwargs['TransactItems']):
                raise RuntimeError('synthetic lost acknowledgment')
    with pytest.raises(RuntimeError):run(Lost(),max_steps=1)
    assert progress.get(d,'pipeline',PART,'TOMBSTONE')['locatorCleanupState']['phase']=='RECOMPUTE'
    assert progress.get(d,'pipeline','CANDIDATE#11111111-1111-4111-8111-111111111111','CONTRIB#'+TOKEN) is None
    run(d,max_steps=5)
    assert progress.get(d,'pipeline','CANDIDATE#11111111-1111-4111-8111-111111111111','SUMMARY')['contributorCount']==1


def test_writer_version_change_restarts_recompute(world):
    d,put=world;candidate(put);run(d,max_steps=5)
    saved=progress.get(d,'pipeline','CANDIDATE#11111111-1111-4111-8111-111111111111','DELETION_RECOMPUTE');assert saved['contributors']==25
    d.update_item(TableName='pipeline',Key=progress.key('CANDIDATE#11111111-1111-4111-8111-111111111111','SUMMARY'),UpdateExpression='SET #v = :v',
                  ExpressionAttributeNames={'#v':'version'},ExpressionAttributeValues=progress.wire({':v':3}))
    run(d,max_steps=1)
    saved=progress.get(d,'pipeline','CANDIDATE#11111111-1111-4111-8111-111111111111','DELETION_RECOMPUTE')
    assert saved['summaryVersion']==3 and saved['contributors']==0 and saved['cursor'] is None


def test_delayed_gsi_rows_after_empty_pass_do_not_create_completion(world):
    d,put=world
    assert run(d,max_steps=3)['locatorPassEnded']
    feature(put,5)
    assert run(d,max_steps=5)['complete'] is False
    assert progress.get(d,'pipeline',event_pk(5),'FEATURE') is None
    assert progress.get(d,'ledger','ACCOUNT#a','ACCOUNT_DELETION#CAMPAIGN') is None


def test_expired_or_missing_key_and_deadline_cannot_be_skipped(world):
    d,put=world
    d.delete_item(TableName='pipeline',Key=progress.key('PERIOD#10','HMAC_KEY'))
    with pytest.raises(progress.CoverageUnavailable):run(d)
    put({'PK':'PERIOD#10','SK':'HMAC_KEY','keyArn':'synthetic','status':'ENABLED'})
    put({'PK':PART,'SK':'TOMBSTONE','expiresAt':NOW})
    with pytest.raises(progress.CoverageUnavailable):run(d)
    assert progress.get(d,'pipeline',PART,'TOMBSTONE')['expiresAt']==NOW


def test_time_budget_returns_pending_without_work(world):
    d,put=world;feature(put,1)
    result=run(d,remaining_ms=lambda:1000)
    assert result['complete'] is False and result['deleted']==0
    assert progress.get(d,'pipeline',event_pk(1),'FEATURE') is not None


def test_wrong_owned_base_row_fails_closed(world):
    d,put=world;feature(put,1);run(d,max_steps=2)
    d.update_item(TableName='pipeline',Key=progress.key(event_pk(1),'FEATURE'),UpdateExpression='SET GSI1PK = :pk',
                  ExpressionAttributeValues=progress.wire({':pk':'CONTRIB#10#another'}))
    with pytest.raises(d.exceptions.TransactionCanceledException):run(d,max_steps=1)
    assert progress.get(d,'pipeline',event_pk(1),'FEATURE') is not None


def test_durable_command_is_required_and_exact_terminal_replay_has_no_work(world):
    d,put=world;feature(put,1)
    d.delete_item(TableName='ledger',Key=progress.key(COMMAND['PK'],COMMAND['SK']))
    with pytest.raises(progress.CoverageUnavailable):run(d)
    put(COMMAND|{'eventType':'account.deletion.completed','status':'COMPLETE','completedAtEpoch':NOW+1,
                 'retainUntilEpoch':NOW+1+120*86400},'ledger')
    assert run(d)['alreadyCompleted']
    assert progress.get(d,'pipeline',event_pk(1),'FEATURE') is not None
    put(COMMAND|{'status':'COMPLETE','completedAtEpoch':NOW+1},'ledger')
    with pytest.raises(progress.CoverageUnavailable):run(d)


def test_tombstone_condition_blocks_queued_writes_atomically(world):
    d,put=world
    put({'PK':PART,'SK':'TOMBSTONE','expiresAt':NOW+1000})
    check={'ConditionCheck':{'TableName':'pipeline','Key':progress.key(PART,'TOMBSTONE'),
                            'ConditionExpression':'attribute_not_exists(PK) AND attribute_not_exists(SK)'}}
    with pytest.raises(d.exceptions.TransactionCanceledException):
        d.transact_write_items(TransactItems=[check,{'Put':{'TableName':'pipeline','Item':progress.wire({'PK':'EVENT#new','SK':'FEATURE'})}}])
    assert progress.get(d,'pipeline','EVENT#new','FEATURE') is None


def test_new_operation_adopts_pending_recompute_without_reset(world):
    d,put=world;candidate(put,31);run(d,max_steps=4)
    original=progress.get(d,'pipeline',PART,'TOMBSTONE')['locatorCleanupState']
    assert original['phase']=='RECOMPUTE'
    other=COMMAND|{'operationId':'abcd1234-444b-4bb1-9889-fb56885b7922'}
    put(other,'ledger')
    client=progress.CommandGuardedClient(d,'ledger',other)
    locator_progress.sweep(client,'pipeline','dev',PART,other['operationId'],NOW,max_steps=1)
    adopted=progress.get(d,'pipeline',PART,'TOMBSTONE')['locatorCleanupState']
    assert adopted==original|{'operationId':other['operationId']}
    locator_progress.sweep(client,'pipeline','dev',PART,other['operationId'],NOW,max_steps=4)
    assert progress.get(d,'pipeline','CANDIDATE#11111111-1111-4111-8111-111111111111','SUMMARY')['contributorCount']==31


def test_overlapping_live_commands_preserve_shared_repair_and_deadline(world):
    d,put=world;candidate(put,31);run(d,max_steps=4)
    withdrawal=COMMAND|{'SK':'CAMPAIGN_WITHDRAWAL#'+OP,'eventType':'campaign.consent.withdrawn','status':'PENDING',
                        'operationId':'abcd1234-444b-4bb1-9889-fb56885b7922','consentEpochId':OP}
    put(withdrawal,'ledger')
    client=progress.CommandGuardedClient(d,'ledger',withdrawal)
    locator_progress.sweep(client,'pipeline','dev',PART,withdrawal['operationId'],NOW,max_steps=2)
    run(d,max_steps=2) # re-adopt, continue second aggregate page
    assert progress.get(d,'pipeline','CANDIDATE#11111111-1111-4111-8111-111111111111','SUMMARY')['contributorCount']==31
    assert progress.get(d,'pipeline',PART,'TOMBSTONE')['deletionDeadlineEpoch']==NOW+21*86400
    assert progress.get(d,'ledger','ACCOUNT#a','ACCOUNT_DELETION#CAMPAIGN') is None


def test_command_change_during_work_prevents_atomic_delete(world):
    d,put=world;feature(put,1);run(d,max_steps=2)
    client=progress.CommandGuardedClient(d,'ledger',COMMAND)
    put(COMMAND|{'status':'COMPLETE'},'ledger')
    with pytest.raises(d.exceptions.TransactionCanceledException):
        locator_progress.sweep(client,'pipeline','dev',PART,OP,NOW,max_steps=1)
    assert progress.get(d,'pipeline',event_pk(1),'FEATURE') is not None
