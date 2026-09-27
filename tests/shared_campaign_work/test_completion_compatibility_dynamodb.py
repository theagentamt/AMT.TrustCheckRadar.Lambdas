"""Actual completion consumes qualified seals without requiring a retired MAC key."""
import os
import pytest
from tests.campaign_deletion_bridge.test_completion_dynamodb import qualified,world,CMD,NOW,candidate,get
from tests.campaign_deletion_bridge.test_coverage_dynamodb import partition
from shared_campaign_work import configuration as C,records as R
from shared_campaign_work.proofs import advance_prefix
from shared_campaign_locators import period as P
from retained_periods import delete_retained_contributions

@pytest.fixture
def modern(qualified,monkeypatch):
    w=qualified;timestamp=(1501)*P.PERIOD_SECONDS+P.RECOVERY_SECONDS+100
    w.d.create_table(TableName='outbox',BillingMode='PAY_PER_REQUEST',KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':n,'AttributeType':'S'} for n in ('PK','SK')])
    ids={'pipeline':'11111111-1111-4111-8111-111111111111','outbox':'22222222-2222-4222-8222-222222222222'}
    env={'APP_ENVIRONMENT':'dev','CAMPAIGN_PERIOD_WORK_ENABLED':'true','CAMPAIGN_PERIOD_WORK_MANIFEST_SHA256':'d'*64,'CAMPAIGN_PERIOD_WORK_INVENTORY_REVISION':'1',
         **{f'CAMPAIGN_PERIOD_WORK_{n.upper()}_TABLE_NAME':n for n in ids},**{f'CAMPAIGN_PERIOD_WORK_{n.upper()}_TABLE_ID':v for n,v in ids.items()}}
    for k,v in env.items():monkeypatch.setenv(k,v)
    config=C.pins();marker={'PK':'INVENTORY#dev','SK':'CAMPAIGN_PERIOD_WORK','recordType':'CAMPAIGN_PERIOD_WORK_INVENTORY','schemaVersion':1,
       'environment':'dev','coverage':'VERIFIED_COMPLETE','revision':1,'manifestSha256':'d'*64,'approvedAtEpoch':NOW-20,
       'admissionGeneration':config['generation'],'locatorManifestSha256':'a'*64,'locatorInventoryRevision':1,'minimumPeriodId':1498,
       'resources':config['resources'],'writers':C.WRITERS,'baseline':'EXACT_ALL_TARGETS_INDEXED','restoreInvalidation':'REQUIRES_NEW_GENERATION'};w.put(marker)
    for period in range(1498,1501):
        w.d.delete_item(TableName='pipeline',Key=C.wire({'PK':partition(period),'SK':'TOMBSTONE'}))
        record=get(w,'pipeline',f'PERIOD#{period}','HMAC_KEY')
        record|={'admissionSchemaVersion':2,'admissionState':'SEALED','admissionChangedAtEpoch':timestamp-1,
            'workSchemaVersion':1,'workManifestSha256':'d'*64,'workInventoryRevision':1,
            'sealSchemaVersion':1,'sealedAtEpoch':timestamp-1,'sealManifestSha256':'d'*64,'sealInventoryRevision':1,
            'sealGeneration':config['generation'],'sealPipelineTableId':ids['pipeline'],'sealOutboxTableId':ids['outbox'],'sealNextOrdinal':1,
            'retirementSchemaVersion':1,'retirementState':'SCHEDULED','retirementRequestedAtEpoch':timestamp-1,
            'retirementObservedAtEpoch':timestamp-1,'scheduledDeletionAtEpoch':timestamp-1+7*86400,'status':'RETIRED'};w.put(record)
        w.put(R.control_key(period)|{'recordType':'CAMPAIGN_PERIOD_WORK_CONTROL','schemaVersion':1,'environment':'dev','generation':config['generation'],
            'periodId':period,'manifestSha256':'d'*64,'inventoryRevision':1,'revision':1,'nextOrdinal':1,'pendingCount':0,
            'passRevision':0,'passCursor':0,'passHighWater':0,'lastProgressAtEpoch':0,'lastFullPassAtEpoch':0})
    class Client:
        def __getattr__(self,name):return getattr(w.d,name)
        def describe_table(self,**kw):
            value=w.d.describe_table(**kw);name=kw['TableName'];value['Table']['TableId']=ids[name]
            value['Table']['TableArn']='arn:aws:dynamodb:us-east-1:107827791950:table/'+name;return value
    class NoKms:
        def generate_mac(self,**kw):raise AssertionError('Retired key must not be used')
    return w,Client(),NoKms(),timestamp


def test_actual_account_receipt_after_request_period_retirement_without_mac(modern):
    w,client,kms,now=modern
    cleanup=delete_retained_contributions(CMD,environment='dev',aws_account_id='107827791950',aws_region='us-east-1',
       table_name='pipeline',deletion_ledger_table_name='ledger',retention_days=21,dynamodb=client,kms=kms,
       locator_manifest_sha256='a'*64,locator_inventory_revision=1,now_epoch=now)
    assert cleanup['reason']=='SEALED_PERIOD_RANGE_OBSERVED' and cleanup['selectedLocatorPassEnded'] and not cleanup['complete']
    result=candidate(w,dynamodb=client,kms=kms,now=lambda:now).complete(CMD)
    assert result['campaignComplete']
    receipt=get(w,'ledger',CMD['PK'],'ACCOUNT_DELETION#CAMPAIGN')
    assert receipt['operationId']==CMD['operationId']
    assert candidate(w,dynamodb=client,kms=kms,now=lambda:now).complete(CMD)['alreadyComplete']
    assert get(w,'ledger',CMD['PK'],'ACCOUNT_DELETION#CAMPAIGN')==receipt


def test_contiguous_prefix_avoids_ever_growing_period_budget(modern):
    w,client,kms,now=modern
    for period in (1498,1499):assert advance_prefix(client,now=lambda:now)['retiredThroughPeriodId']==period
    assert candidate(w,dynamodb=client,kms=kms,now=lambda:now,max_periods=1).complete(CMD)['campaignComplete']


def test_restored_work_blocks_seal_consumer_without_receipt(modern):
    w,client,kms,now=modern
    w.put({'PK':'PERIOD_WORK#1498','SK':'UNKNOWN','restored':True})
    with pytest.raises(Exception):candidate(w,dynamodb=client,kms=kms,now=lambda:now).complete(CMD)
    assert get(w,'ledger',CMD['PK'],'ACCOUNT_DELETION#CAMPAIGN') is None


def test_assessor_stops_before_final_proof_reread_when_budget_expires(modern,monkeypatch):
    from coverage_assessment import assess_account_coverage
    w,client,kms,now=modern;expired=[];calls=[]
    real_query=client.query;real_get=client.get_item
    def query(**kw):
        result=real_query(**kw)
        if any(value.get('S')=='PERIOD_WORK#1500' for value in kw.get('ExpressionAttributeValues',{}).values()):expired.append(True)
        return result
    def get_item(**kw):
        assert not expired, 'No proof reread may occur after budget exhaustion'
        calls.append(kw);return real_get(**kw)
    monkeypatch.setattr(client,'query',query);monkeypatch.setattr(client,'get_item',get_item)
    result=assess_account_coverage(CMD,environment='dev',aws_account_id='107827791950',aws_region='us-east-1',
        table_name='pipeline',deletion_ledger_table_name='ledger',dynamodb=client,kms=kms,
        locator_manifest_sha256='a'*64,locator_inventory_revision=1,now_epoch=now,remaining_ms=lambda:0 if expired else 30000)
    assert expired and not result['rangeExamined'] and 'TIME_BUDGET_EXCEEDED' in result['reasons']
    assert not result['receiptEligible']


def test_global_work_minimum_does_not_expand_historical_account_locator_scope(modern,monkeypatch):
    from coverage_assessment import assess_account_coverage
    w,client,kms,now=modern
    marker=get(w,'pipeline','INVENTORY#dev','CAMPAIGN_PERIOD_WORK')
    w.put(marker|{'minimumPeriodId':1497})
    real=client.get_item
    def checked(**kw):
        assert kw['Key'].get('PK')!={'S':'PERIOD#1497'}, 'Global lifecycle scope must not expand account scope'
        return real(**kw)
    monkeypatch.setattr(client,'get_item',checked)
    result=delete_retained_contributions(CMD,environment='dev',aws_account_id='107827791950',aws_region='us-east-1',
        table_name='pipeline',deletion_ledger_table_name='ledger',retention_days=21,dynamodb=client,kms=kms,
        locator_manifest_sha256='a'*64,locator_inventory_revision=1,now_epoch=now)
    assert result['selectedLocatorPassEnded']
    assessment=assess_account_coverage(CMD,environment='dev',aws_account_id='107827791950',aws_region='us-east-1',
        table_name='pipeline',deletion_ledger_table_name='ledger',dynamodb=client,kms=kms,
        locator_manifest_sha256='a'*64,locator_inventory_revision=1,now_epoch=now)
    assert assessment['rangeExamined'] and assessment['periodsExpected']==3
    assert candidate(w,dynamodb=client,kms=kms,now=lambda:now).complete(CMD)['campaignComplete']
