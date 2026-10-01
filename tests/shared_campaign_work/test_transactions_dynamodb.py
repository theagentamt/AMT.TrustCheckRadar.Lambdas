"""Real SDK transactions; only immutable resource TableId metadata is modeled in Moto."""
import os
from copy import deepcopy
from types import SimpleNamespace
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('Isolated SDK only',allow_module_level=True)
import boto3
from moto import mock_aws
from shared_campaign_work import configuration as C,records as R
from shared_campaign_work.transactions import TrackedClient
from tests.campaign_period_fixtures import enable,row

GEN='7a1e079d-22f8-4a5e-b6c4-f432fed4e7b1'
PERIOD=8;NOW=10000000
EVENT='7fbce2ac-bd2e-4d2e-9ec6-1f895a482abc'
TARGET={'PK':'EVENT#'+EVENT,'SK':'DEDUPE','expiresAt':NOW+100,'GSI3SK':NOW+100,'GSI3PK':'EXPIRY#dev'}

@pytest.fixture
def world(monkeypatch):
    enable(monkeypatch)
    generation=os.environ['CAMPAIGN_PERIOD_ADMISSION_GENERATION']
    env={'APP_ENVIRONMENT':'dev','CAMPAIGN_PERIOD_WORK_ENABLED':'true','CAMPAIGN_PERIOD_WORK_MANIFEST_SHA256':'b'*64,
         'CAMPAIGN_PERIOD_WORK_INVENTORY_REVISION':'1','CAMPAIGN_PERIOD_WORK_PIPELINE_TABLE_NAME':'pipeline',
         'CAMPAIGN_PERIOD_WORK_OUTBOX_TABLE_NAME':'outbox',
         'CAMPAIGN_PERIOD_WORK_PIPELINE_TABLE_ID':'11111111-1111-4111-8111-111111111111',
         'CAMPAIGN_PERIOD_WORK_OUTBOX_TABLE_ID':'22222222-2222-4222-8222-222222222222'}
    for k,v in env.items():monkeypatch.setenv(k,v)
    with mock_aws():
        d=boto3.client('dynamodb',region_name='us-east-1');resources=C.pins()['resources']
        for name in ('pipeline','outbox'):
            d.create_table(TableName=name,KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],
                           AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}],BillingMode='PAY_PER_REQUEST')
        put=lambda record:d.put_item(TableName='pipeline',Item=C.wire(record))
        registry=row(PERIOD)|{'admissionSchemaVersion':2,'workSchemaVersion':1,'workManifestSha256':'b'*64,'workInventoryRevision':1}
        put(registry)
        marker={'PK':'INVENTORY#dev','SK':'CAMPAIGN_PERIOD_WORK','recordType':'CAMPAIGN_PERIOD_WORK_INVENTORY',
                'schemaVersion':1,'environment':'dev','coverage':'VERIFIED_COMPLETE','revision':1,'manifestSha256':'b'*64,
                'approvedAtEpoch':NOW-1,'admissionGeneration':generation,'locatorManifestSha256':'a'*64,'locatorInventoryRevision':1,
                'minimumPeriodId':PERIOD,'resources':resources,'writers':C.WRITERS,'baseline':'EXACT_ALL_TARGETS_INDEXED',
                'restoreInvalidation':'REQUIRES_NEW_GENERATION'}
        put(marker)
        control=R.control_key(PERIOD)|{'recordType':'CAMPAIGN_PERIOD_WORK_CONTROL','schemaVersion':1,'environment':'dev',
                'generation':generation,'periodId':PERIOD,'manifestSha256':'b'*64,'inventoryRevision':1,'revision':1,
                'nextOrdinal':1,'pendingCount':0,'passRevision':0,'passCursor':0,'passHighWater':0,
                'lastProgressAtEpoch':0,'lastFullPassAtEpoch':0};put(control)
        class Client:
            def __getattr__(self,name):return getattr(d,name)
            def describe_table(self,**kw):
                result=d.describe_table(**kw);family='pipeline' if kw['TableName']=='pipeline' else 'outbox'
                result['Table']['TableId']=resources[family]['tableId']
                result['Table']['TableArn']='arn:aws:dynamodb:us-east-1:107827791950:table/'+kw['TableName']
                return result
        client=Client();writer=TrackedClient(client,registries=[registry],now=lambda:NOW)
        def get(k):
            raw=d.get_item(TableName='pipeline',Key=C.wire(k),ConsistentRead=True).get('Item');return C.plain(raw) if raw else None
        yield writer,d,put,get,registry,marker,control


def create(writer,value=TARGET):
    return writer.transact_write_items(TransactItems=[{'Put':{'TableName':'pipeline','Item':C.wire(value),'ConditionExpression':'attribute_not_exists(PK)'}}])

def delete(writer):
    return writer.transact_write_items(TransactItems=[{'Delete':{'TableName':'pipeline','Key':C.wire({'PK':TARGET['PK'],'SK':TARGET['SK']})}}])


def test_allocate_and_delete_pair_exactly_once(world):
    writer,d,put,get,registry,marker,control=world;create(writer)
    indexed=get(R.lookup_key('pipeline',TARGET['PK'],TARGET['SK']));assert indexed['ordinal']==1
    assert get(R.control_key(PERIOD))['pendingCount']==1
    delete(writer);delete(writer)
    assert get(R.control_key(PERIOD))['pendingCount']==0 and get(R.control_key(PERIOD))['nextOrdinal']==2
    assert get(R.work_key(PERIOD,1)) is None and get(R.lookup_key('pipeline',TARGET['PK'],TARGET['SK'])) is None


def test_legacy_target_and_missing_control_never_auto_adopt(world):
    writer,d,put,get,registry,marker,control=world;put(TARGET)
    with pytest.raises(R.WorkUnavailable):delete(writer)
    assert get({'PK':TARGET['PK'],'SK':TARGET['SK']})==TARGET
    d.delete_item(TableName='pipeline',Key=C.wire({'PK':TARGET['PK'],'SK':TARGET['SK']}))
    d.delete_item(TableName='pipeline',Key=C.wire(R.control_key(PERIOD)))
    with pytest.raises(R.WorkUnavailable):create(writer)
    assert get({'PK':TARGET['PK'],'SK':TARGET['SK']}) is None


def test_shortening_updates_both_index_deadlines_and_refuses_extension(world):
    writer,d,put,get,*_=world;create(writer)
    def update(expiry):
        return writer.transact_write_items(TransactItems=[{'Update':{'TableName':'pipeline','Key':C.wire({'PK':TARGET['PK'],'SK':TARGET['SK']}),
            'UpdateExpression':'SET expiresAt=:expiry, GSI3SK=:expiry','ExpressionAttributeValues':C.wire({':expiry':expiry})}}])
    update(NOW+50)
    assert get(R.work_key(PERIOD,1))['deadlineEpoch']==NOW+50
    assert get(R.lookup_key('pipeline',TARGET['PK'],TARGET['SK']))['deadlineEpoch']==NOW+50
    with pytest.raises(R.WorkUnavailable):update(NOW+51)


def test_target_ttl_loss_still_has_exact_discoverable_work(world):
    writer,d,put,get,*_=world;create(writer)
    d.delete_item(TableName='pipeline',Key=C.wire({'PK':TARGET['PK'],'SK':TARGET['SK']}))
    assert get(R.work_key(PERIOD,1)) is not None
    delete(writer);assert get(R.control_key(PERIOD))['pendingCount']==0


def test_lost_ack_never_allocates_hole_or_decrements_twice(world):
    writer,d,put,get,*_=world;real=writer.client.transact_write_items
    class Lost:
        def __getattr__(self,n):return getattr(writer.client,n)
        def transact_write_items(self,**kw):real(**kw);raise RuntimeError('synthetic lost ack')
    proxy=TrackedClient(Lost(),registries=list(writer.registries.values()),now=lambda:NOW)
    with pytest.raises(RuntimeError):create(proxy)
    assert get(R.control_key(PERIOD))['nextOrdinal']==2
    with pytest.raises(Exception):create(writer)
    assert get(R.control_key(PERIOD))['nextOrdinal']==2
    with pytest.raises(RuntimeError):delete(proxy)
    delete(writer);assert get(R.control_key(PERIOD))['pendingCount']==0


def test_identical_existing_registry_condition_is_not_duplicated(world):
    writer,d,put,get,registry,*_=world
    writer.transact_write_items(TransactItems=[C.condition('pipeline',registry),
        {'Put':{'TableName':'pipeline','Item':C.wire(TARGET),'ConditionExpression':'attribute_not_exists(PK)'}}])
    assert get(R.control_key(PERIOD))['pendingCount']==1
