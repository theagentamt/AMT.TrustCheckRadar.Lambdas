import pytest
from tests.shared_campaign_work.test_transactions_dynamodb import world,PERIOD,NOW,create
from shared_campaign_work.scheduler import tick,METRICS
from shared_campaign_work import records as R,configuration as C

class Metrics:
    def __init__(self):self.calls=[]
    def put_metric_data(self,**kw):self.calls.append(kw)
class NoKms:
    def __getattr__(self,name):raise AssertionError('retirement disabled')


def test_closed_tick_has_no_sdk_calls(monkeypatch):
    monkeypatch.setenv('CAMPAIGN_PERIOD_LIFECYCLE_ENABLED','false')
    with pytest.raises(R.WorkUnavailable):tick(NoKms(),NoKms(),NoKms(),now=lambda:NOW)


def test_tick_numeric_cursor_reports_bounded_work_without_identifiers(world,monkeypatch):
    writer,d,put,get,*_=world;create(writer)
    monkeypatch.setenv('CAMPAIGN_PERIOD_LIFECYCLE_ENABLED','true');metrics=Metrics()
    result=tick(writer.client,NoKms(),metrics,now=lambda:NOW+101)
    assert result['metrics']['LifecycleHeartbeat']==1 and result['metrics']['LifecycleWorkAttempted']==1
    assert not result['periodComplete'] and len(metrics.calls)==1
    actual=metrics.calls[0]
    assert actual['Namespace']=='TrustCheckRadar/Campaign'
    assert {m['MetricName'] for m in actual['MetricData']}==set(METRICS)
    assert all(m['Dimensions']==[{'Name':'Environment','Value':'dev'}] for m in actual['MetricData'])
    cursor=get({'PK':'PERIOD_SWEEP#dev','SK':'STATE'})
    assert cursor['revision']==2 and 'targetPK' not in cursor


def test_missing_period_does_not_hold_numeric_sweep_forever(world,monkeypatch):
    writer,d,put,get,registry,*_=world
    monkeypatch.setenv('CAMPAIGN_PERIOD_LIFECYCLE_ENABLED','true');metrics=Metrics()
    d.delete_item(TableName='pipeline',Key=C.wire({'PK':registry['PK'],'SK':registry['SK']}))
    result=tick(writer.client,NoKms(),metrics,now=lambda:NOW)
    assert result['metrics']['LifecycleWorkUnverified']==2 and result['metrics']['LifecycleHeartbeat']==1
    assert get({'PK':'PERIOD_SWEEP#dev','SK':'STATE'})['revision']==2


def test_new_empty_period_does_not_emit_false_stall(world,monkeypatch):
    writer,d,put,get,*_=world
    monkeypatch.setenv('CAMPAIGN_PERIOD_LIFECYCLE_ENABLED','true')
    result=tick(writer.client,NoKms(),Metrics(),now=lambda:NOW)
    assert result['metrics']['LifecycleBacklog']==0
    assert result['metrics']['LifecycleProgressAgeSeconds']==0
