"""New-period control initialization is atomic and never bootstraps inventory."""
import pytest
from tests.scripts.test_initialize_campaign_period import world,I,REQUEST,NOW,PERIOD,current
from shared_campaign_work import configuration as C,records as R

@pytest.fixture
def modern(world,monkeypatch):
    tool,d,key,tags,clock,inventory=world
    outbox='trustcheckradar-dev-campaign-outbox'
    d.create_table(TableName=outbox,BillingMode='PAY_PER_REQUEST',KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':n,'AttributeType':'S'} for n in ('PK','SK')])
    ids={'pipeline':'11111111-1111-4111-8111-111111111111','outbox':'22222222-2222-4222-8222-222222222222'}
    env={'APP_ENVIRONMENT':'dev','AWS_REGION':I.REGION,'CAMPAIGN_PERIOD_ADMISSION_ACCOUNT_ID':I.ACCOUNT,
         'CAMPAIGN_PERIOD_ADMISSION_ENABLED':'true','CAMPAIGN_PERIOD_ADMISSION_GENERATION':REQUEST['generation'],
         'CAMPAIGN_PERIOD_WORK_ENABLED':'true','CAMPAIGN_PERIOD_WORK_MANIFEST_SHA256':'b'*64,'CAMPAIGN_PERIOD_WORK_INVENTORY_REVISION':'1',
         'CAMPAIGN_PERIOD_WORK_PIPELINE_TABLE_NAME':I.TABLE,'CAMPAIGN_PERIOD_WORK_OUTBOX_TABLE_NAME':outbox,
         **{f'CAMPAIGN_PERIOD_WORK_{n.upper()}_TABLE_ID':v for n,v in ids.items()}}
    for k,v in env.items():monkeypatch.setenv(k,v)
    config=C.pins();marker={'PK':'INVENTORY#dev','SK':'CAMPAIGN_PERIOD_WORK','recordType':'CAMPAIGN_PERIOD_WORK_INVENTORY','schemaVersion':1,'environment':'dev',
        'coverage':'VERIFIED_COMPLETE','revision':1,'manifestSha256':'b'*64,'approvedAtEpoch':NOW-10,'admissionGeneration':REQUEST['generation'],
        'locatorManifestSha256':'a'*64,'locatorInventoryRevision':1,'minimumPeriodId':PERIOD-1,'resources':config['resources'],'writers':C.WRITERS,
        'baseline':'EXACT_ALL_TARGETS_INDEXED','restoreInvalidation':'REQUIRES_NEW_GENERATION'}
    d.put_item(TableName=I.TABLE,Item=C.wire(marker));describe=d.describe_table
    def described(**kw):
        result=describe(**kw);name=kw['TableName'];result['Table']['TableId']=ids['pipeline' if name==I.TABLE else 'outbox'];return result
    monkeypatch.setattr(d,'describe_table',described)
    return tool,d,marker


def test_registry_and_empty_control_created_atomically_after_existing_approval(modern):
    tool,d,marker=modern;plan=tool.plan(REQUEST)
    assert current(d) is None and plan['row']['admissionSchemaVersion']==2
    assert tool.apply(plan)['initialized']
    first=current(d);assert tool.apply(plan)['alreadyPresent'] and current(d)==first
    row=C.plain(d.get_item(TableName=I.TABLE,Key=C.wire(R.control_key(PERIOD)),ConsistentRead=True)['Item'])
    assert row==plan['workControl'] and row['pendingCount']==0


def test_missing_inventory_or_nonempty_work_never_creates_control(modern):
    tool,d,marker=modern
    d.put_item(TableName=I.TABLE,Item=C.wire({'PK':R.work_key(PERIOD,1)['PK'],'SK':'UNKNOWN','restored':True}))
    with pytest.raises(I.Unavailable):tool.plan(REQUEST)
    assert current(d) is None


def test_marker_race_rolls_back_registry_and_control(modern,monkeypatch):
    tool,d,marker=modern;plan=tool.plan(REQUEST);real=d.transact_write_items
    def race(**kw):
        d.put_item(TableName=I.TABLE,Item=C.wire(marker|{'revision':2}));return real(**kw)
    monkeypatch.setattr(d,'transact_write_items',race)
    with pytest.raises(I.Unavailable):tool.apply(plan)
    assert current(d) is None
    assert not d.get_item(TableName=I.TABLE,Key=C.wire(R.control_key(PERIOD)),ConsistentRead=True).get('Item')
