"""Actual publisher/cluster transactions through modern authoritative indexing."""
import os
from copy import deepcopy
import pytest
from tests.shared_campaign_locators.test_dynamodb import world,publish,aggregate,get,wire,PERIOD,NOW,EVENT
from shared_campaign_work import configuration as C,records as R


@pytest.fixture
def modern(world,monkeypatch):
    d,put=world
    ids={'pipeline':'11111111-1111-4111-8111-111111111111','outbox':'22222222-2222-4222-8222-222222222222'}
    for k,v in {'APP_ENVIRONMENT':'dev','CAMPAIGN_PERIOD_WORK_ENABLED':'true','CAMPAIGN_PERIOD_WORK_MANIFEST_SHA256':'b'*64,'CAMPAIGN_PERIOD_WORK_INVENTORY_REVISION':'1',
                **{f'CAMPAIGN_PERIOD_WORK_{n.upper()}_TABLE_NAME':n for n in ids},**{f'CAMPAIGN_PERIOD_WORK_{n.upper()}_TABLE_ID':v for n,v in ids.items()}}.items():monkeypatch.setenv(k,v)
    config=C.pins()
    marker={'PK':'INVENTORY#dev','SK':'CAMPAIGN_PERIOD_WORK','recordType':'CAMPAIGN_PERIOD_WORK_INVENTORY','schemaVersion':1,
        'environment':'dev','coverage':'VERIFIED_COMPLETE','revision':1,'manifestSha256':'b'*64,'approvedAtEpoch':NOW-100,
        'admissionGeneration':config['generation'],'locatorManifestSha256':'a'*64,'locatorInventoryRevision':1,
        'minimumPeriodId':PERIOD-1,'resources':config['resources'],'writers':C.WRITERS,'baseline':'EXACT_ALL_TARGETS_INDEXED','restoreInvalidation':'REQUIRES_NEW_GENERATION'}
    put(marker)
    for period in (PERIOD-1,PERIOD):
        row=get(d,'pipeline',f'PERIOD#{period}','HMAC_KEY');put(row|{'admissionSchemaVersion':2,'workSchemaVersion':1,'workManifestSha256':'b'*64,'workInventoryRevision':1})
        put(R.control_key(period)|{'recordType':'CAMPAIGN_PERIOD_WORK_CONTROL','schemaVersion':1,'environment':'dev','generation':config['generation'],
            'periodId':period,'manifestSha256':'b'*64,'inventoryRevision':1,'revision':1,'nextOrdinal':1,'pendingCount':0,
            'passRevision':0,'passCursor':0,'passHighWater':0,'lastProgressAtEpoch':0,'lastFullPassAtEpoch':0})
    class Client:
        def __getattr__(self,name):return getattr(d,name)
        def describe_table(self,**kw):
            result=d.describe_table(**kw);name=kw['TableName'];result['Table']['TableId']=ids[name]
            result['Table']['TableArn']='arn:aws:dynamodb:us-east-1:107827791950:table/'+name
            return result
    yield Client(),put


def test_actual_publisher_cluster_and_repeat_index_every_written_row(modern):
    import uuid
    d,put=modern
    assert publish(d)=='published' and aggregate(d)=='candidate-created'
    event=str(uuid.uuid4());assert publish(d,event)=='published' and aggregate(d,event)=='counted-repeat'
    rows=[C.plain(r) for r in d.scan(TableName='pipeline')['Items']]
    targets=[r for r in rows if r['PK'].startswith(('EVENT#','CANDIDATE#','CONTRIB#','BUCKET#'))]
    works=[r for r in rows if r['PK'].startswith('PERIOD_WORK#')]
    assert len(targets)==len(works)==get(d,'pipeline',R.control_key(PERIOD)['PK'],'STATE')['pendingCount']
    for target in targets:
        lookup=get(d,'pipeline',R.lookup_key('pipeline',target['PK'],target['SK'])['PK'],'RECORD')
        work=get(d,'pipeline',R.work_key(PERIOD,lookup['ordinal'])['PK'],R.work_key(PERIOD,lookup['ordinal'])['SK'])
        R.validate_pair(work,lookup,'dev',C.pins()['generation'],family='pipeline',pk=target['PK'],sk=target['SK'])


def test_actual_retained_account_cleanup_consumes_owned_work_indexes(modern):
    from types import SimpleNamespace
    from tests.shared_campaign_locators.test_dynamodb import COMMAND,TOKEN,PART
    from retained_periods import delete_retained_contributions
    d,put=modern;publish(d);aggregate(d);put(COMMAND,'ledger')
    kms=SimpleNamespace(generate_mac=lambda **kw:{'Mac':b'x'*32,'KeyId':kw['KeyId'],'MacAlgorithm':'HMAC_SHA_256'})
    results=[]
    for _ in range(4):
        results.append(delete_retained_contributions(COMMAND,environment='dev',aws_account_id='107827791950',aws_region='us-east-1',
            table_name='pipeline',deletion_ledger_table_name='ledger',retention_days=21,dynamodb=d,kms=kms,
            locator_manifest_sha256='a'*64,locator_inventory_revision=1,now_epoch=NOW,max_steps=10))
    assert any(result['deleted'] for result in results)
    rows=[C.plain(r) for r in d.scan(TableName='pipeline')['Items']]
    assert not any(r['PK']==PART and r['SK'].startswith('LOCATOR#') for r in rows)
    assert not any(r.get('targetPK')=='EVENT#'+EVENT for r in rows if r['PK'].startswith('PERIOD_WORK#'))
    work=[r for r in rows if r['PK'].startswith('PERIOD_WORK#'+str(PERIOD))]
    assert get(d,'pipeline',R.control_key(PERIOD)['PK'],'STATE')['pendingCount']==len(work)
