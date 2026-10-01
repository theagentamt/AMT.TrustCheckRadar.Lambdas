import hashlib
import pytest
from tests.shared_campaign_work.test_transactions_dynamodb import world,PERIOD,NOW,EVENT
from tests.shared_campaign_work.test_lifecycle_dynamodb import engine
from shared_campaign_work import configuration as C,records as R
from shared_campaign_work.outbox import validate_locator,paired_delete

ACCOUNT='synthetic-account'
HASH=hashlib.sha256(ACCOUNT.encode()).hexdigest()


def rows():
    event={'PK':'EVENT#'+EVENT,'SK':'OBSERVATION_READY','accountId':ACCOUNT,'statisticsEventId':EVENT,
           'environment':'dev','expiresAt':NOW+100}
    locator={'PK':'ACCOUNT#'+HASH,'SK':'OUTBOX#'+EVENT,'recordType':'CAMPAIGN_OUTBOX_LOCATOR','schemaVersion':2,
       'recordVersion':1,'environment':'dev','accountIdHash':HASH,'statisticsEventId':EVENT,
       'eventPK':event['PK'],'eventSK':event['SK'],'eventExpiresAt':NOW+100,'logicalExpiresAt':NOW+86500}
    return event,locator


def seed(world):
    writer,*_=world;event,locator=rows()
    writer.transact_write_items(TransactItems=[{'Put':{'TableName':'outbox','Item':C.wire(row),'ConditionExpression':'attribute_not_exists(PK)'}} for row in (event,locator)])
    return event,locator


def test_modern_locator_preserves_original_bound_without_ttl_field(world):
    event,locator=seed(world)
    assert validate_locator(locator,'dev',HASH)==locator and 'expiresAt' not in locator
    assert locator['logicalExpiresAt']==event['expiresAt']+86400
    for changed in (locator|{'expiresAt':locator['logicalExpiresAt']},locator|{'schemaVersion':1},locator|{'logicalExpiresAt':NOW+999999}):
        with pytest.raises(R.WorkUnavailable):validate_locator(changed,'dev',HASH)


def test_event_deadline_deletes_event_and_still_live_locator_atomically(world,monkeypatch):
    writer,d,put,get,*_=world;event,locator=seed(world)
    result=engine(world,monkeypatch,NOW+101).period_pass(PERIOD)
    assert result['attempted']>=1 and result['backlog']==0
    assert d.scan(TableName='outbox')['Items']==[]
    assert get(R.lookup_key('outbox',event['PK'],event['SK'])) is None
    assert get(R.lookup_key('outbox',locator['PK'],locator['SK'])) is None


def test_account_owned_pair_and_index_delete_preserves_other_account(world):
    writer,d,put,get,*_=world;event,locator=seed(world)
    command={'PK':'ACCOUNT#'+ACCOUNT,'SK':'ACCOUNT_DELETION','environment':'dev','accountId':ACCOUNT,'status':'REQUESTED'}
    d.put_item(TableName='pipeline',Item=C.wire(command))
    other={'PK':'UNRELATED','SK':'ROW','preserved':True};d.put_item(TableName='outbox',Item=C.wire(other))
    paired_delete(writer.client,outbox_table='outbox',ledger_table='pipeline',command=command,locator=locator,event=event,now=NOW)
    assert d.scan(TableName='outbox')['Items']==[C.wire(other)]
    assert get(R.control_key(PERIOD))['pendingCount']==0


def test_actual_account_data_producer_consumes_pair_before_real_receipt(world):
    import boto3
    from scripts.qualification.account_cleanup import _account_modules
    service,_=_account_modules()
    writer,d,put,get,*_=world;event,locator=seed(world)
    command={'PK':'ACCOUNT#'+ACCOUNT,'SK':'ACCOUNT_DELETION','schemaVersion':1,'recordVersion':1,'environment':'dev',
        'eventType':'account.deletion.requested','accountId':ACCOUNT,'status':'REQUESTED','occurredAtEpoch':NOW-10,
        'deleteByEpoch':NOW-10+86400,'operationId':'69a43d58-54d1-4edc-9941-8a37a5ab8d79'}
    d.put_item(TableName='pipeline',Item=C.wire(command));resource=boto3.resource('dynamodb',region_name='us-east-1')
    result=service.delete_campaign_outbox(command,outbox_table=resource.Table('outbox'),ledger_table=resource.Table('pipeline'),
        locator_coverage_status='approved',now_epoch=NOW,work_client=writer.client)
    assert result['complete'] and result['deleted']==2 and get(R.control_key(PERIOD))['pendingCount']==0
    receipt=get({'PK':command['PK'],'SK':'ACCOUNT_DELETION#CAMPAIGN_OUTBOX'})
    assert receipt['operationId']==command['operationId'] and receipt['retainUntilEpoch']==NOW+120*86400


def test_actual_export_reader_omits_expired_v2_metadata_with_table_binding(world):
    import boto3
    from types import SimpleNamespace
    from account_export_api.reader import Reader
    writer,d,put,get,*_=world;event,locator=seed(world)
    authority=SimpleNamespace(now=lambda:NOW+101,ddb=boto3.resource('dynamodb',region_name='us-east-1'))
    reader=Reader(authority,{'outbox':'outbox'},None,None,campaign_work_client=writer.client)
    assert reader._observation({'account':ACCOUNT},locator) is None
    broken=locator|{'logicalExpiresAt':locator['logicalExpiresAt']+1}
    with pytest.raises(R.WorkUnavailable):reader._observation({'account':ACCOUNT},broken)
