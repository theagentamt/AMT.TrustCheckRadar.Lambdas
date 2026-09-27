"""Modern publication pins all writer mutations without truncating contributors."""
import base64
from copy import deepcopy
import pytest
from tests.campaign_lifecycle.test_publication_dynamodb import world,PERIOD,NOW,ID,finish
from shared_campaign_work import configuration as C,records as R
from shared_campaign_work.transactions import TrackedClient
from shared_campaign_locators import core,period as P

@pytest.fixture
def modern(world,monkeypatch):
    p,d,put,get,summary,locator_marker=world
    import importlib.util,sys
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('modern_publication_service',Path(__file__).resolve().parents[2]/'src/campaign_lifecycle/service.py')
    service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service)
    monkeypatch.setitem(sys.modules,'service',service)
    start=(PERIOD+1)*P.PERIOD_SECONDS+1;end=(PERIOD+1)*P.PERIOD_SECONDS+P.RECOVERY_SECONDS
    ids={'pipeline':'11111111-1111-4111-8111-111111111111','outbox':'22222222-2222-4222-8222-222222222222'}
    d.create_table(TableName='outbox',BillingMode='PAY_PER_REQUEST',KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':n,'AttributeType':'S'} for n in ('PK','SK')])
    for name,value in {'APP_ENVIRONMENT':'dev','CAMPAIGN_PERIOD_WORK_ENABLED':'true','CAMPAIGN_PERIOD_WORK_MANIFEST_SHA256':'b'*64,'CAMPAIGN_PERIOD_WORK_INVENTORY_REVISION':'1',
        **{f'CAMPAIGN_PERIOD_WORK_{n.upper()}_TABLE_NAME':n for n in ids},**{f'CAMPAIGN_PERIOD_WORK_{n.upper()}_TABLE_ID':v for n,v in ids.items()}}.items():monkeypatch.setenv(name,value)
    config=C.pins();registry=get('pipeline',f'PERIOD#{PERIOD}','HMAC_KEY')|{'admissionSchemaVersion':2,'workSchemaVersion':1,'workManifestSha256':'b'*64,'workInventoryRevision':1}
    put('pipeline',registry);put('pipeline',locator_marker|{'approvedAtEpoch':start-1})
    marker={'PK':'INVENTORY#dev','SK':'CAMPAIGN_PERIOD_WORK','recordType':'CAMPAIGN_PERIOD_WORK_INVENTORY','schemaVersion':1,'environment':'dev','coverage':'VERIFIED_COMPLETE',
        'revision':1,'manifestSha256':'b'*64,'approvedAtEpoch':start-1,'admissionGeneration':config['generation'],'locatorManifestSha256':'a'*64,'locatorInventoryRevision':1,
        'minimumPeriodId':PERIOD,'resources':config['resources'],'writers':C.WRITERS,'baseline':'EXACT_ALL_TARGETS_INDEXED','restoreInvalidation':'REQUIRES_NEW_GENERATION'}
    put('pipeline',marker);put('pipeline',R.control_key(PERIOD)|{'recordType':'CAMPAIGN_PERIOD_WORK_CONTROL','schemaVersion':1,'environment':'dev','generation':config['generation'],
        'periodId':PERIOD,'manifestSha256':'b'*64,'inventoryRevision':1,'revision':1,'nextOrdinal':1,'pendingCount':0,'passRevision':0,'passCursor':0,'passHighWater':0,'lastProgressAtEpoch':0,'lastFullPassAtEpoch':0})
    class Client:
        def __getattr__(self,name):return getattr(d,name)
        def describe_table(self,**kw):
            result=d.describe_table(**kw);name=kw['TableName'];result['Table']['TableId']=ids[name];result['Table']['TableArn']='arn:aws:dynamodb:us-east-1:107827791950:table/'+name;return result
    client=Client();writer=TrackedClient(client,registries=[registry],now=lambda:start)
    rows=[C.plain(v) for v in d.scan(TableName='pipeline')['Items']]
    template=next(v for v in rows if v['SK'].startswith('CONTRIB#'))
    for v in rows:
        if v['PK'].startswith(('CANDIDATE#','CONTRIB#')):d.delete_item(TableName='pipeline',Key=C.wire({'PK':v['PK'],'SK':v['SK']}))
    values=[summary|{'expiresAt':end,'GSI3SK':end,'contributorCount':110,'submissionCount':110}]
    for n in range(110):
        token=base64.urlsafe_b64encode(n.to_bytes(32,'big')).decode().rstrip('=')
        value=template|{'SK':'CONTRIB#'+token,'GSI1PK':f'CONTRIB#{PERIOD}#{token}','expiresAt':end}
        values.extend([value,core.locator_for_target(value,'dev')])
    # Qualified source fixture construction uses actual tracked transactions.
    for offset in range(0,len(values),20):writer.transact_write_items(TransactItems=[{'Put':{'TableName':'pipeline','Item':C.wire(v),'ConditionExpression':'attribute_not_exists(PK)'}} for v in values[offset:offset+20]])
    p.d=client;p.now=lambda:start
    return p,d,put,get,writer,start,end


def test_more_than_transaction_limit_contributors_publish_whole_set_before_recovery_deadline(modern):
    p,d,put,get,writer,start,end=modern
    assert p.process(ID)['state']=='FROZEN'
    assert p.process(ID)['state']=='PUBLISHED'
    value=get('intelligence','CAMPAIGN#'+ID,'AGGREGATE')
    assert value['contributorCount']==110 and value['submissionCount']==110
    assert value['expiresAt']==start+400*86400 and value['expiryPartition'].startswith('EXPIRY#dev#')


def test_tombstone_insert_after_absence_read_invalidates_counter_snapshot(modern):
    p,d,put,get,writer,start,end=modern;p.process(ID)
    original=p._get;changed=[]
    def race(table,pk,sk):
        result=original(table,pk,sk)
        if sk=='TOMBSTONE' and not changed:
            changed.append(pk)
            tomb={'PK':pk,'SK':'TOMBSTONE','recordType':'CAMPAIGN_DELETION_TOMBSTONE','schemaVersion':2,'environment':'dev',
                'periodId':PERIOD,'createdAtEpoch':start,'deletionDeadlineEpoch':end,'GSI3PK':'EXPIRY#dev','GSI3SK':end}
            writer.transact_write_items(TransactItems=[{'Put':{'TableName':'pipeline','Item':C.wire(tomb),'ConditionExpression':'attribute_not_exists(PK)'}}])
        return result
    p._get=race
    with pytest.raises(R.WorkUnavailable):p.process(ID)
    assert get('intelligence','CAMPAIGN#'+ID,'AGGREGATE') is None
    assert get('pipeline','CANDIDATE#'+ID,'SUMMARY')['lifecycleState']=='FROZEN'


def test_expiry_boundary_suppresses_without_extending_any_source_clock(modern):
    p,d,put,get,writer,start,end=modern;p.now=lambda:end
    assert p.process(ID)['state']=='FROZEN' and p.process(ID)['state']=='SUPPRESSED'
    assert get('intelligence','CAMPAIGN#'+ID,'AGGREGATE') is None
    assert get('pipeline','CANDIDATE#'+ID,'SUMMARY')['expiresAt']==end
