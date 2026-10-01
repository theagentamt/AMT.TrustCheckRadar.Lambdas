"""Real worker composition with modern work binding (synthetic resource IDs only)."""
import os
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('Isolated SDK only',allow_module_level=True)
from tests.shared_campaign_work.test_completion_compatibility_dynamodb import modern,qualified,world
from tests.campaign_deletion_bridge.test_worker_completion_dynamodb import enabled,Context,event,add_index
from tests.campaign_deletion_bridge.test_completion_dynamodb import CMD,NOW,OP,get,partition
from shared_campaign_work import configuration as C,records as W
from shared_campaign_work.retirement import SEAL_FIELDS,SCHEDULED_FIELDS
from orchestration import process,CleanupGuard

TICK={'schemaVersion':1,'operation':'reconcile-campaign-cleanup'}

@pytest.fixture
def worker(modern,enabled,monkeypatch):
    w,client,kms,clock=modern
    same,app=enabled
    assert same is w
    app.dynamodb=client;app.kms=kms
    monkeypatch.setattr(app.time,'time',lambda:clock)
    add_index(w)
    return w,client,app,clock


def open_period(worker,monkeypatch):
    w,client,app,_=worker
    for sk in ('CAMPAIGN_LOCATORS','CAMPAIGN_PERIOD_WORK'):
        row=get(w,'pipeline','INVENTORY#dev',sk);w.put(row|{'minimumPeriodId':1500})
    row=get(w,'pipeline','PERIOD#1500','HMAC_KEY')
    row={k:v for k,v in row.items() if k not in SEAL_FIELDS|SCHEDULED_FIELDS}
    w.put(row|{'status':'ENABLED','admissionState':'OPEN','admissionChangedAtEpoch':NOW-1})
    app.kms=w.kms
    monkeypatch.setattr(app.time,'time',lambda:NOW+1)
    return w,client,app


def test_actual_scheduled_open_cleanup_creates_tracked_tomb_and_receipt(worker,monkeypatch):
    w,client,app=open_period(worker,monkeypatch)
    calls=[];real=client.describe_table
    monkeypatch.setattr(client,'describe_table',lambda **kw:(calls.append(kw['TableName']),real(**kw))[1])
    out=app.lambda_handler(TICK,Context())
    assert out['CommandsAttempted']==out['CommandsCompleted']==1
    assert out['CommandsUnverified']==0 and set(calls)=={'pipeline','outbox'}
    receipt=get(w,'ledger',CMD['PK'],'ACCOUNT_DELETION#CAMPAIGN')
    assert receipt['operationId']==OP
    tomb=get(w,'pipeline',partition(1500),'TOMBSTONE')
    assert tomb['locatorCleanupState']['phase']=='SEEK'
    key=W.lookup_key('pipeline',tomb['PK'],tomb['SK'])
    lookup=get(w,'pipeline',key['PK'],key['SK'])
    assert lookup and get(w,'pipeline','PERIOD_WORK_CONTROL#1500','STATE')['pendingCount']==1
    before=w.snapshot()
    assert app.lambda_handler(TICK,Context())['CommandsAttempted']==0
    assert w.snapshot()==before


def test_actual_stream_retired_proof_reaches_receipt_without_mac(worker):
    w,client,app,_=worker
    out=app.lambda_handler(event(),Context())
    assert out['completed']==1 and out['pending']==0
    assert get(w,'ledger',CMD['PK'],'ACCOUNT_DELETION#CAMPAIGN')['operationId']==OP
    assert not w.calls


def test_binding_mismatch_refuses_before_cleanup_or_receipt(worker,monkeypatch):
    w,client,app=open_period(worker,monkeypatch)
    real=client.describe_table
    def wrong(**kw):
        result=real(**kw);result['Table']['TableId']='33333333-3333-4333-8333-333333333333';return result
    monkeypatch.setattr(client,'describe_table',wrong)
    before=w.snapshot()
    with pytest.raises(RuntimeError,match='RECONCILIATION_REQUIRED'):app.lambda_handler(event(),Context())
    assert w.snapshot()==before and not w.calls
    assert get(w,'ledger',CMD['PK'],'ACCOUNT_DELETION#CAMPAIGN') is None


def test_budget_drop_at_guarded_binding_prevents_sdk_call_and_mutation(worker,monkeypatch):
    w,client,app=open_period(worker,monkeypatch)
    remaining=[30000];describes=[];real=CleanupGuard.__getattr__
    def expire(self,name):
        method=real(self,name)
        if name=='describe_table':remaining[0]=5999
        return method
    monkeypatch.setattr(CleanupGuard,'__getattr__',expire)
    monkeypatch.setattr(client,'describe_table',lambda **kw:describes.append(kw))
    before=w.snapshot()
    with pytest.raises(RuntimeError):
        process(CMD,dynamodb=client,kms=w.kms,settings=app.config,aws_account_id='107827791950',
                aws_region='us-east-1',remaining_ms=lambda:remaining[0],now=lambda:NOW+1)
    assert not describes and not w.calls and w.snapshot()==before


def test_guard_still_refuses_unapproved_sdk_methods(worker):
    _,client,_,_=worker
    guard=CleanupGuard(client,'ledger',CMD,lambda:30000)
    with pytest.raises(AttributeError):guard.scan
