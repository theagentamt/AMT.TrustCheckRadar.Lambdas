"""Real index discovery and conditional erasure; no TTL/full-coverage assertion."""
from uuid import uuid4
import pytest
from tests.shared_campaign_work.test_transactions_dynamodb import world,NOW
from tests.shared_campaign_work.test_scheduler_dynamodb import Metrics
from shared_campaign_work import configuration as C,records as R,aggregates as A
ID='33333333-3333-4333-8333-333333333333'

@pytest.fixture
def aggregate_world(world,monkeypatch):
    writer,d,put,get,*_=world
    d.create_table(TableName='intelligence',BillingMode='PAY_PER_REQUEST',KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],
      AttributeDefinitions=[{'AttributeName':n,'AttributeType':t} for n,t in (('PK','S'),('SK','S'),('expiryPartition','S'),('expiresAt','N'))],
      GlobalSecondaryIndexes=[{'IndexName':'ExpirationIndex','KeySchema':[{'AttributeName':'expiryPartition','KeyType':'HASH'},{'AttributeName':'expiresAt','KeyType':'RANGE'}],'Projection':{'ProjectionType':'KEYS_ONLY'}}])
    for name,value in {'CAMPAIGN_PERIOD_LIFECYCLE_ENABLED':'true','INTELLIGENCE_TABLE_NAME':'intelligence','CAMPAIGN_AGGREGATE_TABLE_ID':ID}.items():monkeypatch.setenv(name,value)
    class Client:
        def __getattr__(self,name):return getattr(writer.client,name)
        def describe_table(self,**kw):
            if kw['TableName']!='intelligence':return writer.client.describe_table(**kw)
            value=d.describe_table(**kw);value['Table']['TableId']=ID;value['Table']['TableArn']='arn:aws:dynamodb:us-east-1:107827791950:table/intelligence';return value
    return Client(),d,put,get


def row(expiry=NOW-1):
    while True:
        ident=str(uuid4())
        if A.partition('dev',ident)=='EXPIRY#dev#00':break
    return {'PK':'CAMPAIGN#'+ident,'SK':'AGGREGATE','campaignId':ident,'schemaVersion':1,'taxonomyVersion':1,
      'categoryId':'other','periodWeek':'2026-W01','state':'PENDING_REVIEW','contributorCount':10,'submissionCount':10,
      'dimensionSchemaVersion':1,'languageIds':[],'tacticIds':[],'channelIds':[],'contributorCountBand':'10-24',
      'submissionCountBand':'10-24','riskBand':'high','summaryKey':'campaign.other','trendDirection':'new','expiresAt':expiry,
      'version':1,'environment':'dev','expiryPartition':'EXPIRY#dev#00'}


def test_expired_exact_row_removed_future_preserved_and_no_full_erasure_claim(aggregate_world):
    client,d,put,get=aggregate_world;expired=row();future=row(NOW+10)
    for value in (expired,future):d.put_item(TableName='intelligence',Item=C.wire(value))
    result=A.tick(client,Metrics(),now=lambda:NOW)
    assert result['metrics']['AggregateWorkDeleted']==1 and result['metrics']['AggregateHeartbeat']==1
    assert result['aggregateErasureComplete'] is False
    actual=[C.plain(v) for v in d.scan(TableName='intelligence')['Items']];assert actual==[future]
    cursor=get({'PK':'AGGREGATE_SWEEP#dev','SK':'STATE'});assert cursor['cursor'] is None and cursor['shard']==2


def test_poison_first_page_cannot_starve_later_valid_aggregate(aggregate_world):
    client,d,put,get=aggregate_world
    for _ in range(17):d.put_item(TableName='intelligence',Item=C.wire(row(NOW-2)|{'unknown':True}))
    valid=row();d.put_item(TableName='intelligence',Item=C.wire(valid))
    result=A.tick(client,Metrics(),now=lambda:NOW)
    assert result['metrics']['AggregateWorkUnverified']==17 and result['metrics']['AggregateWorkDeleted']==1
    assert d.scan(TableName='intelligence')['Count']==17


def test_refresh_race_never_deletes_changed_aggregate(aggregate_world,monkeypatch):
    client,d,put,get=aggregate_world;value=row();d.put_item(TableName='intelligence',Item=C.wire(value));real=d.transact_write_items
    def race(**kw):
        if any('Delete' in action for action in kw['TransactItems']):d.put_item(TableName='intelligence',Item=C.wire(value|{'version':2}))
        return real(**kw)
    monkeypatch.setattr(d,'transact_write_items',race)
    result=A.tick(client,Metrics(),now=lambda:NOW)
    assert result['metrics']['AggregateWorkDeleted']==0 and result['metrics']['AggregateWorkUnverified']==1
    assert C.plain(d.get_item(TableName='intelligence',Key=C.wire({'PK':value['PK'],'SK':'AGGREGATE'}))['Item'])['version']==2


def test_wrong_restored_resource_and_false_gate_refuse(aggregate_world,monkeypatch):
    client,d,*_=aggregate_world;value=row();d.put_item(TableName='intelligence',Item=C.wire(value))
    monkeypatch.setenv('CAMPAIGN_AGGREGATE_TABLE_ID','44444444-4444-4444-8444-444444444444')
    with pytest.raises(R.WorkUnavailable):A.tick(client,Metrics(),now=lambda:NOW)
    monkeypatch.setenv('CAMPAIGN_PERIOD_LIFECYCLE_ENABLED','false')
    with pytest.raises(R.WorkUnavailable):A.tick(object(),object(),now=lambda:NOW)
    assert d.scan(TableName='intelligence')['Count']==1


def test_completed_pass_resets_identifier_and_expired_cursor_restarts(aggregate_world):
    client,d,put,get=aggregate_world
    for _ in range(8):A.tick(client,Metrics(),now=lambda:NOW)
    key={'PK':'AGGREGATE_SWEEP#dev','SK':'STATE'};cursor=get(key)
    assert cursor['shard']==0 and cursor['cursor'] is None and cursor['lastFullPassAtEpoch']==NOW
    A.tick(client,Metrics(),now=lambda:NOW+86401)
    assert get(key)['highWaterEpoch']==NOW+86401 and get(key)['shard']==3


def test_actual_review_audit_keeps_original_deadline_and_is_explicitly_erased(aggregate_world):
    import importlib.util,json
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('aggregate_review_fixture',Path(__file__).resolve().parents[2]/'src/campaign_review/service.py')
    review=importlib.util.module_from_spec(spec);spec.loader.exec_module(review)
    client,d,put,get=aggregate_world;value=row(NOW+5);d.put_item(TableName='intelligence',Item=C.wire(value))
    event={'pathParameters':{'campaignId':value['campaignId']},'requestContext':{'authorizer':{'jwt':{'claims':{'cognito:groups':['campaign-reviewer']}}}},
           'body':json.dumps({'schemaVersion':1,'action':'confirm','reasonCode':'quality_verified','reason':'Reviewed synthetic aggregate fixture.'})}
    assert review.review(event,table_name='intelligence',reviewer_group='campaign-reviewer',minimum_contributors=10,dynamodb=d,now_epoch=NOW)['state']=='CONFIRMED'
    values=[C.plain(v) for v in d.scan(TableName='intelligence')['Items']]
    assert len(values)==2 and all(v['expiresAt']==NOW+5 and v['expiryPartition']==value['expiryPartition'] for v in values)
    with pytest.raises(review.ReviewError):review.review(event,table_name='intelligence',reviewer_group='campaign-reviewer',minimum_contributors=10,dynamodb=d,now_epoch=NOW+5)
    result=A.tick(client,Metrics(),now=lambda:NOW+6)
    assert result['metrics']['AggregateWorkDeleted']==2 and d.scan(TableName='intelligence')['Count']==0


def test_expired_poison_continuation_discards_key_but_advances_numeric_shard(aggregate_world):
    client,d,put,get=aggregate_world
    for _ in range(40):d.put_item(TableName='intelligence',Item=C.wire(row(NOW-1)|{'unknown':True}))
    A.tick(client,Metrics(),now=lambda:NOW)
    key={'PK':'AGGREGATE_SWEEP#dev','SK':'STATE'};before=get(key)
    assert before['shard']==0 and before['cursor'] is not None
    result=A.tick(client,Metrics(),now=lambda:NOW+86401)
    after=get(key)
    assert after['shard']==3 and after['cursor'] is None and after['passIncomplete']
    assert after['lastFullPassAtEpoch']==0 and result['metrics']['AggregateWorkUnverified']==1
    assert d.scan(TableName='intelligence')['Count']==40
