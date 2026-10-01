"""Isolated SDK/Moto producer and strong locator cleanup acceptance."""
import os,sys,base64,json,importlib.util,uuid
from pathlib import Path
from decimal import Decimal
from types import SimpleNamespace
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':
    pytest.skip('Run isolated with AMT_AUTHORITY_INTEGRATION=1',allow_module_level=True)
import boto3
from moto import mock_aws
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
for folder in ('campaign_cluster_aggregator','campaign_observation_publisher','campaign_deletion_bridge'):
    sys.path.insert(0,str(ROOT/'src'/folder))
from shared_campaign_locators import *
from shared_campaign_locators.core import WRITERS
from progress import get,key,wire

def module(name,folder):
    spec=importlib.util.spec_from_file_location(name,ROOT/'src'/folder/'service.py');result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
publisher=module('test_locator_publisher','campaign_observation_publisher')
cluster=module('test_locator_cluster','campaign_cluster_aggregator')
delete=module('test_locator_delete','campaign_deletion_bridge')
NOW=1780000100
PERIOD=NOW//(14*86400)
TOKEN=base64.urlsafe_b64encode(b'x'*32).decode().rstrip('=')
PART=f'CONTRIB#{PERIOD}#{TOKEN}'
EVENT='7fbce2ac-bd2e-4d2e-9ec6-1f895a482abc'
EPOCH='15c81ba4-2fa6-43c3-8895-889f08c931bf'
OP='47debb73-444b-4bb1-9889-fb56885b7922'
PINS={'locator_manifest_sha256':'a'*64,'locator_inventory_revision':1}
INV={'PK':'INVENTORY#dev','SK':'CAMPAIGN_LOCATORS','recordType':'CAMPAIGN_LOCATOR_INVENTORY','schemaVersion':1,
     'revision':1,'environment':'dev','coverage':'VERIFIED_COMPLETE','manifestSha256':'a'*64,'approvedAtEpoch':NOW-100,
     'locatorSchemaVersion':1,'minimumPeriodId':PERIOD-1,'priorPeriodsErased':True,'writers':WRITERS}
COMMAND={'PK':'ACCOUNT#a','SK':'ACCOUNT_DELETION','schemaVersion':1,'recordVersion':1,'environment':'dev',
         'eventType':'account.deletion.requested','accountId':'a','status':'REQUESTED','occurredAtEpoch':NOW,
         'deleteByEpoch':NOW+86400,'operationId':OP}


def observation(event=EVENT):
    return {'schemaVersion':1,'recordVersion':1,'environment':'dev','statisticsEventId':event,'accountId':'a',
        'campaignConsentGranted':True,'consentEpochId':EPOCH,'noticeVersion':'research-consent-2026-09-21-v2','observedAtEpoch':NOW-1,'expiresAt':NOW+1000,
        'appFeatures':{'schemaVersion':1,'extractorVersion':'android-1.0.0','languageId':'en','taxonomyBucket':'advance_fee',
        'vector':[1.0,0.0],'lexicalFingerprint':['0123456789abcdef'],'signalIds':['payment_request'],
        'indicatorIds':['payment.crypto'],'confidence':0.9}}

@pytest.fixture
def world(monkeypatch):
    from tests.campaign_period_fixtures import enable,row
    enable(monkeypatch)
    with mock_aws():
        d=boto3.client('dynamodb',region_name='us-east-1')
        for name in ('pipeline','ledger','users','outbox'):
            args={'TableName':name,'KeySchema':[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],
                  'AttributeDefinitions':[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}], 'BillingMode':'PAY_PER_REQUEST'}
            if name=='pipeline':
                args['GlobalSecondaryIndexes']=[]
                for n,index in ((1,'ContributorPeriodIndex'),(2,'CandidateBucketIndex')):
                    args['AttributeDefinitions'] += [{'AttributeName':f'GSI{n}PK','AttributeType':'S'},{'AttributeName':f'GSI{n}SK','AttributeType':'S'}]
                    args['GlobalSecondaryIndexes'].append({'IndexName':index,'KeySchema':[{'AttributeName':f'GSI{n}PK','KeyType':'HASH'},{'AttributeName':f'GSI{n}SK','KeyType':'RANGE'}],'Projection':{'ProjectionType':'ALL'}})
            d.create_table(**args)
        put=lambda row,table='pipeline':d.put_item(TableName=table,Item=wire(row))
        put(INV);put({'PK':'USER#a','SK':'CAMPAIGN_PARTICIPATION','state':'enrolled','consentEpochId':EPOCH,'environment':'dev','noticeVersion':'research-consent-2026-09-21-v2','policyVersion':'independent-research-v1'},'users')
        for period in (PERIOD,PERIOD-1):put(row(period))
        yield d,put


def publish(d,event=EVENT,**override):
    d.put_item(TableName='outbox',Item=wire(json.loads(json.dumps(observation(event) | {'PK':f'EVENT#{event}','SK':'OBSERVATION_READY','eventType':'campaign.observation.ready'}),parse_float=Decimal)))
    return publisher.publish_observation(observation(event),pipeline_table_name='pipeline',users_table_name='users',deletion_ledger_table_name='ledger',
        cluster_queue_url='synthetic',hmac_key_id='arn:aws:kms:us-east-1:107827791950:key/12345678-1234-4234-8234-123456789abc',transient_retention_days=21,dynamodb_client=d,
        kms_client=SimpleNamespace(generate_mac=lambda **_:{'Mac':b'x'*32}),sqs_client=SimpleNamespace(send_message=lambda **_:{}),now_epoch=NOW,**(PINS|override))


def aggregate(d,event=EVENT):
    body=json.dumps({'schemaVersion':1,'recordVersion':1,'environment':'dev','statisticsEventId':event,'eventType':'campaign.cluster.requested'})
    return cluster.process_message(body,environment='dev',schema_version=1,table_name='pipeline',retention_days=21,max_submissions=3,dynamodb=d,now_epoch=NOW,users_table_name='users',deletion_ledger_table_name='ledger',outbox_table_name='outbox',**PINS)


def erase(d,**extra):
    return delete.delete_account_contributions(COMMAND,table_name='pipeline',deletion_ledger_table_name='ledger',retention_days=21,dynamodb=d,
        kms=SimpleNamespace(generate_mac=lambda **_:{'Mac':b'x'*32}),now_epoch=NOW,**(PINS|extra))


def test_publisher_and_all_cluster_paths_keep_exact_locators(world):
    d,put=world
    assert publish(d)=='published';assert aggregate(d)=='candidate-created'
    for _ in range(3):
        event=str(uuid.uuid4());assert publish(d,event)=='published';aggregate(d,event)
    items,cursor=owned_page(d,'pipeline','dev',PART)
    assert cursor is None and len(items)==5
    assert sum(v['targetKind']=='CONTRIBUTION' for v in items)==1
    for loc in items:
        assert 'expiresAt' not in loc
        assert loc['GSI3SK']==loc['targetExpiresAtEpoch']
        if loc['targetKind']=='FEATURE':
            for sk in ('DEDUPE','CLUSTERED'):
                sibling=get(d,'pipeline',loc['targetPK'],sk)
                assert sibling['locatorPK']==PART and sibling['locatorSK']==loc['SK'] and sibling['expiresAt']<=loc['targetExpiresAtEpoch']


def test_default_missing_inventory_cannot_create_unlocated_target(world):
    d,put=world
    with pytest.raises(LocatorUnavailable):publish(d,locator_manifest_sha256=None)
    assert get(d,'pipeline','EVENT#'+EVENT,'FEATURE') is None
    d.delete_item(TableName='pipeline',Key=key('INVENTORY#dev','CAMPAIGN_LOCATORS'))
    with pytest.raises(LocatorUnavailable):publish(d)
    assert get(d,'pipeline','EVENT#'+EVENT,'FEATURE') is None


def test_inventory_change_during_write_is_atomic(world):
    d,put=world
    class Changed:
        def __getattr__(self,name):return getattr(d,name)
        def transact_write_items(self,**kwargs):
            put(INV|{'revision':2})
            return d.transact_write_items(**kwargs)
    with pytest.raises(d.exceptions.TransactionCanceledException):publish(Changed())
    assert get(d,'pipeline','EVENT#'+EVENT,'FEATURE') is None
    assert owned_page(d,'pipeline','dev',PART)[0]==[]


def test_tombstone_race_prevents_new_contribution_and_locator(world):
    d,put=world;publish(d)
    class Deleted:
        def __getattr__(self,name):return getattr(d,name)
        def transact_write_items(self,**kwargs):
            put({'PK':PART,'SK':'TOMBSTONE','expiresAt':NOW+100})
            return d.transact_write_items(**kwargs)
    with pytest.raises(d.exceptions.TransactionCanceledException):aggregate(Deleted())
    items,_=owned_page(d,'pipeline','dev',PART)
    assert len(items)==1 and items[0]['targetKind']=='FEATURE'


def test_finalizing_summary_blocks_repeat_and_new_selected_contributions(world):
    d,put=world;publish(d);aggregate(d)
    loc=next(v for v in owned_page(d,'pipeline','dev',PART)[0] if v['targetKind']=='CONTRIBUTION')
    summary=get(d,'pipeline',loc['targetPK'],'SUMMARY');put(summary|{'lifecycleState':'FINALIZING'})
    event=str(uuid.uuid4());publish(d,event)
    with pytest.raises(d.exceptions.TransactionCanceledException):aggregate(d,event)
    assert get(d,'pipeline',loc['targetPK'],loc['targetSK'])['submissionCount']==1


def test_target_ttl_cannot_remove_locator_and_paired_cleanup_removes_siblings(world):
    d,put=world;publish(d);loc=owned_page(d,'pipeline','dev',PART)[0][0]
    d.delete_item(TableName='pipeline',Key=key(loc['targetPK'],loc['targetSK']))
    assert get_owned_locator(d,'pipeline',loc)==loc
    sibling=get(d,'pipeline',loc['targetPK'],'DEDUPE')
    assert get_locator_by_pointer(d,'pipeline','dev',sibling)==loc
    d.transact_write_items(TransactItems=paired_delete_actions('pipeline',loc))
    assert owned_page(d,'pipeline','dev',PART)[0]==[]
    assert get(d,'pipeline',loc['targetPK'],'DEDUPE') is None


def test_mismatched_sibling_blocks_all_paired_deletion(world):
    d,put=world;publish(d);loc=owned_page(d,'pipeline','dev',PART)[0][0]
    sibling=get(d,'pipeline',loc['targetPK'],'DEDUPE');put(sibling|{'locatorPK':'CONTRIB#0#'+'z'*43})
    with pytest.raises(d.exceptions.TransactionCanceledException):d.transact_write_items(TransactItems=paired_delete_actions('pipeline',loc))
    assert get(d,'pipeline',loc['targetPK'],'FEATURE') is not None
    assert get_owned_locator(d,'pipeline',loc)==loc


def test_strong_cleanup_never_queries_eventual_contributor_index(world):
    d,put=world
    for _ in range(4):publish(d,str(uuid.uuid4()))
    publish(d);aggregate(d);put(COMMAND,'ledger')
    class StrongOnly:
        def __getattr__(self,name):return getattr(d,name)
        def query(self,**kwargs):
            assert 'IndexName' not in kwargs
            assert kwargs.get('ConsistentRead') is True
            return d.query(**kwargs)
    ended=False
    for _ in range(30):
        result=erase(StrongOnly(),max_steps=2)
        assert result['complete'] is False
        if result['locatorPassEnded']:ended=True;break
    assert ended and owned_page(d,'pipeline','dev',PART)[0]==[]
    assert get(d,'ledger','ACCOUNT#a','ACCOUNT_DELETION#CAMPAIGN') is None


def test_uncovered_old_request_and_missing_legacy_locator_are_blocked(world):
    d,put=world;publish(d);put(COMMAND,'ledger')
    put(INV|{'approvedAtEpoch':NOW})
    with pytest.raises(LocatorUnavailable):erase(d)
    put(INV)
    loc=owned_page(d,'pipeline','dev',PART)[0][0]
    d.delete_item(TableName='pipeline',Key=key(loc['PK'],loc['SK']))
    d.delete_item(TableName='ledger',Key=key('ACCOUNT#a','ACCOUNT_DELETION'))
    with pytest.raises(LocatorUnavailable):aggregate(d)


@pytest.mark.parametrize('changes',[{'expiresAt':NOW+1},{'schemaVersion':True},{'targetSK':'other'},
    {'targetExpiresAtEpoch':NOW+1},{'environment':'prod'},{'PK':'CONTRIB#1#bad'}])
def test_locator_schema_rejects_ttl_or_unowned_alias(world,changes):
    d,put=world;publish(d);loc=owned_page(d,'pipeline','dev',PART)[0][0]
    with pytest.raises(LocatorUnavailable):validate_locator(loc|changes,'dev',PART)


def test_versioned_repair_fence_has_fixed_deadline_and_survives_overdue_retry(world):
    d,put=world;publish(d);put(COMMAND,'ledger')
    erase(d,max_steps=1)
    tomb=get(d,'pipeline',PART,'TOMBSTONE')
    assert tomb['schemaVersion']==2 and 'expiresAt' not in tomb
    assert tomb['deletionDeadlineEpoch']==NOW+21*86400
    later=NOW+22*86400
    result=delete.delete_account_contributions(COMMAND,table_name='pipeline',deletion_ledger_table_name='ledger',
        retention_days=21,dynamodb=d,kms=SimpleNamespace(generate_mac=lambda **_:{'Mac':b'x'*32}),now_epoch=later,**PINS)
    assert result['complete'] is False
    assert get(d,'pipeline',PART,'TOMBSTONE')['deletionDeadlineEpoch']==tomb['deletionDeadlineEpoch']
    assert owned_page(d,'pipeline','dev',PART)[0]==[]


def test_legacy_ttl_tombstone_requires_controlled_migration(world):
    d,put=world;publish(d);put(COMMAND,'ledger')
    put({'PK':PART,'SK':'TOMBSTONE','expiresAt':NOW+1000})
    with pytest.raises(RuntimeError):erase(d)
    assert get(d,'pipeline','EVENT#'+EVENT,'FEATURE') is not None
    assert get(d,'pipeline',PART,'TOMBSTONE')['expiresAt']==NOW+1000


def test_retirement_is_unavailable_without_period_and_repair_proof(world):
    import tombstone
    d,put=world;publish(d);put(COMMAND,'ledger');erase(d)
    with pytest.raises(RuntimeError):tombstone.retire(d,'pipeline',PART)
    assert get(d,'pipeline',PART,'TOMBSTONE') is not None


@pytest.mark.parametrize('change',[{'revision':2},{'manifestSha256':'b'*64},{'schemaVersion':True},
    {'writers':['publisher']},{'priorPeriodsErased':False},{'unexpected':'field'}])
def test_inventory_changes_or_unqualified_coverage_block_producers(world,change):
    d,put=world;put(INV|change)
    with pytest.raises(LocatorUnavailable):publish(d)
    assert get(d,'pipeline','EVENT#'+EVENT,'FEATURE') is None

@pytest.mark.parametrize('path',['new','repeat','capped'])
@pytest.mark.parametrize('race',['withdrawal','epoch','deletion','outbox_removed','outbox_expiry'])
def test_research_authority_races_atomically_block_every_cluster_write(world,path,race):
    d,put=world
    for _ in range({'new':0,'repeat':1,'capped':3}[path]):
        event=str(uuid.uuid4());publish(d,event);aggregate(d,event)
    event=str(uuid.uuid4());publish(d,event)
    before=d.scan(TableName='pipeline')['Items']
    class Race:
        def __getattr__(self,name):return getattr(d,name)
        def transact_write_items(self,**kwargs):
            if race=='deletion':put(COMMAND,'ledger')
            elif race=='outbox_removed':d.delete_item(TableName='outbox',Key=key('EVENT#'+event,'OBSERVATION_READY'))
            elif race=='outbox_expiry':d.update_item(TableName='outbox',Key=key('EVENT#'+event,'OBSERVATION_READY'),UpdateExpression='SET expiresAt=:expiry',ExpressionAttributeValues={':expiry':{'N':str(NOW)}})
            else:
                row=get(d,'users','USER#a','CAMPAIGN_PARTICIPATION')
                put(row | ({'state':'withdrawal_pending'} if race=='withdrawal' else {'consentEpochId':str(uuid.uuid4())}),'users')
            return d.transact_write_items(**kwargs)
    with pytest.raises(d.exceptions.TransactionCanceledException):aggregate(Race(),event)
    assert d.scan(TableName='pipeline')['Items']==before


def test_legacy_notice_never_publishes_and_old_feature_never_clusters(world):
    d,put=world
    old=observation() | {'noticeVersion':'2026-09-07'}
    assert publisher.publish_observation(old,pipeline_table_name='pipeline',users_table_name='users',deletion_ledger_table_name='ledger',
        cluster_queue_url='synthetic',transient_retention_days=21,dynamodb_client=d,kms_client=None,sqs_client=None,now_epoch=NOW,**PINS)=='participation-suppressed'
    publish(d)
    row=get(d,'pipeline','EVENT#'+EVENT,'FEATURE');row.pop('researchPolicyVersion');row.pop('researchNoticeVersion');put(row)
    before=d.scan(TableName='pipeline')['Items']
    assert aggregate(d)=='legacy-feature-suppressed'
    assert d.scan(TableName='pipeline')['Items']==before


def test_missing_outbox_never_clusters_despite_retained_feature(world):
    d,put=world;publish(d)
    d.delete_item(TableName='outbox',Key=key('EVENT#'+EVENT,'OBSERVATION_READY'))
    assert aggregate(d)=='participation-suppressed'
    assert get(d,'pipeline','EVENT#'+EVENT,'CLUSTERED') is None


def test_current_feature_does_not_merge_into_legacy_candidate(world):
    d,put=world;publish(d);aggregate(d)
    summaries=[row for row in d.scan(TableName='pipeline')['Items'] if row['SK']=={'S':'SUMMARY'}]
    old=deserialize(summaries[0]);old.pop('researchPolicyVersion');old.pop('researchNoticeVersion');put(old)
    event=str(uuid.uuid4());publish(d,event)
    assert aggregate(d,event)=='candidate-created'
    assert get(d,'pipeline',old['PK'],'SUMMARY')==old
