"""Actual stream/scheduled handler composition over synthetic SDK/Moto state."""
import importlib
import os
import sys
from copy import deepcopy
from types import SimpleNamespace
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('Isolated actual SDK only',allow_module_level=True)
from tests.campaign_deletion_bridge.test_completion_dynamodb import qualified,world,CMD,NOW,OP,ACCOUNT,INV,partition,locator,get,withdrawal,candidate,result
from shared_campaign_recovery import records as R

class Context:
    invoked_function_arn='arn:aws:lambda:us-east-1:107827791950:function:campaign-test:live'
    def get_remaining_time_in_millis(self):return 30000


def event(row=CMD):
    return {'Records':[{'eventSource':'aws:dynamodb','eventSourceARN':
        'arn:aws:dynamodb:us-east-1:107827791950:table/ledger/stream/2026-09-24T00:00:00.000',
        'eventName':'INSERT','dynamodb':{'SequenceNumber':'1','NewImage':R.wire(row)}}]}

@pytest.fixture
def enabled(qualified,monkeypatch):
    env={'AWS_DEFAULT_REGION':'us-east-1','APP_ENVIRONMENT':'dev','PIPELINE_TABLE_NAME':'pipeline',
        'USERS_TABLE_NAME':'users','DELETION_LEDGER_TABLE_NAME':'ledger','CAMPAIGN_DELETION_STREAM_ENABLED':'true',
        'CAMPAIGN_COMPLETION_ENABLED':'true','CAMPAIGN_RECOVERY_ENABLED':'true',
        'CAMPAIGN_COMPLETION_MANIFEST_SHA256':'c'*64,'CAMPAIGN_COMPLETION_INVENTORY_REVISION':'1',
        'CAMPAIGN_RECOVERY_MANIFEST_SHA256':'b'*64,'CAMPAIGN_RECOVERY_INVENTORY_REVISION':'1',
        'CAMPAIGN_LOCATOR_MANIFEST_SHA256':'a'*64,'CAMPAIGN_LOCATOR_INVENTORY_REVISION':'1'}
    for k,v in env.items():monkeypatch.setenv(k,v)
    sys.modules.pop('app',None);sys.modules.pop('config',None)
    app=importlib.import_module('app');app.dynamodb=qualified.d;app.kms=qualified.kms
    monkeypatch.setattr(app.time,'time',lambda:NOW+1)
    return qualified,app


def add_index(w):
    w.d.update_table(TableName='ledger',AttributeDefinitions=[
        {'AttributeName':'campaignRecoveryPartition','AttributeType':'S'},
        {'AttributeName':'nextAttemptAtEpoch','AttributeType':'N'}],GlobalSecondaryIndexUpdates=[{'Create':{
        'IndexName':R.INDEX,'KeySchema':[{'AttributeName':'campaignRecoveryPartition','KeyType':'HASH'},
            {'AttributeName':'nextAttemptAtEpoch','KeyType':'RANGE'}],'Projection':{'ProjectionType':'KEYS_ONLY'}}}])


def test_stream_real_composition_completes_and_replay_never_restarts_sweep(enabled,monkeypatch):
    w,app=enabled
    out=app.lambda_handler(event(),Context());assert out['completed']==1 and out['pending']==0
    receipt=get(w,'ledger',CMD['PK'],'ACCOUNT_DELETION#CAMPAIGN')
    assert get(w,'ledger',CMD['PK'],R.JOB_PREFIX+OP) is None
    assert get(w,'ledger',CMD['PK'],R.CONTROL_SK)['state']=='SEALED'
    before=w.snapshot();calls=[]
    def forbidden(*a,**kw):calls.append(True);raise RuntimeError('must not sweep')
    monkeypatch.setattr(app,'delete_retained_contributions',forbidden)
    assert app.lambda_handler(event(),Context())['completed']==1
    assert calls==[] and w.snapshot()==before
    assert get(w,'ledger',CMD['PK'],'ACCOUNT_DELETION#CAMPAIGN')==receipt


def test_scheduled_handler_completes_expired_stream_work_and_counts_actual_result(enabled):
    w,app=enabled;add_index(w)
    out=app.lambda_handler({'schemaVersion':1,'operation':'reconcile-campaign-cleanup'},Context())
    assert out['CommandsAttempted']==out['CommandsCompleted']==1 and out['CommandsUnverified']==0
    assert out['complete'] is False and out['receiptEligible'] is False
    assert get(w,'ledger',CMD['PK'],R.JOB_PREFIX+OP) is None
    replay=app.lambda_handler({'schemaVersion':1,'operation':'reconcile-campaign-cleanup'},Context())
    assert replay['CommandsAttempted']==0


def test_withdrawal_handler_updates_consent_and_removes_job_without_new_retention(enabled):
    w,app=enabled;cmd,state=withdrawal(w)
    assert app.lambda_handler(event(cmd),Context())['completed']==1
    assert get(w,'ledger',cmd['PK'],cmd['SK'])['status']=='COMPLETE'
    audit=get(w,'users',state['PK'],f'CAMPAIGN_CONSENT#{OP}#{NOW+1}#{OP}#COMPLETED')
    assert audit['expiresAt']==NOW+1+400*86400
    assert app.lambda_handler(event(cmd),Context())['completed']==1
    assert get(w,'users',audit['PK'],audit['SK'])==audit

@pytest.mark.parametrize('missing',['CAMPAIGN_COMPLETION_INVENTORY','CAMPAIGN_RECOVERY_INVENTORY'])
def test_missing_actual_qualification_marker_never_acknowledges_completion(enabled,missing):
    w,app=enabled;w.d.delete_item(TableName='ledger',Key=R.wire(R.key('INVENTORY#dev',missing)))
    with pytest.raises(RuntimeError,match='RECONCILIATION_REQUIRED'):app.lambda_handler(event(),Context())
    assert get(w,'ledger',CMD['PK'],R.JOB_PREFIX+OP) is not None
    assert get(w,'ledger',CMD['PK'],'ACCOUNT_DELETION#CAMPAIGN') is None

@pytest.mark.parametrize('field,value',[
    ('CAMPAIGN_COMPLETION_MANIFEST_SHA256',''),('CAMPAIGN_COMPLETION_INVENTORY_REVISION',0),
    ('CAMPAIGN_RECOVERY_MANIFEST_SHA256','wrong'),('CAMPAIGN_LOCATOR_INVENTORY_REVISION',0)])
def test_enabled_missing_pins_fail_before_any_mutation(enabled,monkeypatch,field,value):
    w,app=enabled;monkeypatch.setattr(app.config,field,value);before=w.snapshot()
    with pytest.raises(RuntimeError,match='qualification pins'):app.lambda_handler(event(),Context())
    assert w.snapshot()==before and not w.calls


def test_default_false_stream_never_acknowledges_delivered_pending_batch(enabled,monkeypatch):
    w,app=enabled;monkeypatch.setattr(app.config,'CAMPAIGN_DELETION_STREAM_ENABLED',False);before=w.snapshot()
    with pytest.raises(RuntimeError,match='STREAM_DISABLED'):app.lambda_handler(event(),Context())
    assert w.snapshot()==before and not w.calls
    monkeypatch.setattr(app.config,'CAMPAIGN_RECOVERY_ENABLED',False)
    assert app.lambda_handler({'schemaVersion':1,'operation':'reconcile-campaign-cleanup'},Context())=={'enabled':False,'complete':False}


def test_completion_false_keeps_cleanup_pending_without_receipt(enabled,monkeypatch):
    w,app=enabled;monkeypatch.setattr(app.config,'CAMPAIGN_COMPLETION_ENABLED',False)
    with pytest.raises(RuntimeError,match='RECONCILIATION_REQUIRED'):app.lambda_handler(event(),Context())
    assert get(w,'ledger',CMD['PK'],R.JOB_PREFIX+OP) is not None
    assert get(w,'ledger',CMD['PK'],'ACCOUNT_DELETION#CAMPAIGN') is None


def test_shared_ledger_sidecars_controls_inventory_receipts_do_not_poison_stream(enabled):
    w,app=enabled
    rows=[R.new_job(CMD),R.new_control(CMD['PK'],'dev'),w.marker,
          {'PK':CMD['PK'],'SK':'ACCOUNT_DELETION#CAMPAIGN'}, {'PK':CMD['PK'],'SK':'ACCOUNT_DELETION#IDENTITY'}]
    before=w.snapshot()
    out=app.lambda_handler({'Records':[event(row)['Records'][0] for row in rows]},Context())
    assert out['ignored']==5 and out['pending']==0 and w.snapshot()==before

@pytest.mark.parametrize('change',[{'eventSource':'aws:sqs'},{'eventSourceARN':
    'arn:aws:dynamodb:us-east-1:107827791950:table/wrong/stream/2026-09-24T00:00:00.000'}])
def test_foreign_stream_is_rejected_without_mutation(enabled,change):
    w,app=enabled;batch=event();batch['Records'][0].update(change);before=w.snapshot()
    with pytest.raises(RuntimeError,match='RECONCILIATION_REQUIRED'):app.lambda_handler(batch,Context())
    assert w.snapshot()==before and not w.calls


def test_mixed_batch_failure_replays_completed_record_safely(enabled):
    w,app=enabled;malformed=CMD|{'status':'UNKNOWN'}
    batch={'Records':event()['Records']+event(malformed)['Records']}
    with pytest.raises(RuntimeError,match='RECONCILIATION_REQUIRED'):app.lambda_handler(batch,Context())
    before=w.snapshot()
    with pytest.raises(RuntimeError,match='RECONCILIATION_REQUIRED'):app.lambda_handler(batch,Context())
    assert w.snapshot()==before


def test_scheduled_retired_key_stays_unverified_without_receipt(enabled,monkeypatch):
    # Real scheduled composition detects missing proof, counts unresolved rather
    # than treating successful callback return as erasure.
    w,app=enabled;add_index(w)
    row=get(w,'pipeline','PERIOD#1498','HMAC_KEY');w.put(row|{'status':'RETIRED'})
    out=app.lambda_handler({'schemaVersion':1,'operation':'reconcile-campaign-cleanup'},Context())
    assert out['CommandsAttempted']==out['CommandsUnverified']==1 and out['CommandsCompleted']==0
    assert get(w,'ledger',CMD['PK'],R.JOB_PREFIX+OP) is not None

@pytest.mark.parametrize('completion_enabled',[False,True])
def test_atomic_receipt_race_prevents_late_cleanup_writes(enabled,monkeypatch,completion_enabled):
    w,app=enabled;monkeypatch.setattr(app.config,'CAMPAIGN_COMPLETION_ENABLED',completion_enabled)
    real=w.d.transact_write_items;first=[True];proof=[None]
    def race(**kw):
        if first[0]:
            first[0]=False;assert candidate(w).complete(CMD)['campaignComplete']
            proof[0]=w.snapshot()
        return real(**kw)
    w.d.transact_write_items=race
    with pytest.raises(RuntimeError,match='RECONCILIATION_REQUIRED'):app.lambda_handler(event(),Context())
    assert w.snapshot()==proof[0]
    assert get(w,'ledger',CMD['PK'],R.CONTROL_SK)['state']=='SEALED'


def test_terminal_account_ack_is_not_campaign_completion(enabled):
    w,app=enabled
    w.put(CMD|{'status':'COMPLETE','eventType':'account.deletion.completed','completedAtEpoch':NOW,
               'retainUntilEpoch':NOW+120*86400},'ledger')
    before=w.snapshot();out=app.lambda_handler(event(),Context())
    assert out['terminalAcknowledged']==1 and out['completed']==0 and w.snapshot()==before


def test_default_env_flags_are_all_closed(enabled,monkeypatch):
    w,app=enabled
    for name in ('CAMPAIGN_DELETION_STREAM_ENABLED','CAMPAIGN_COMPLETION_ENABLED','CAMPAIGN_RECOVERY_ENABLED'):
        monkeypatch.delenv(name,raising=False)
    reloaded=importlib.reload(app.config)
    assert reloaded.CAMPAIGN_DELETION_STREAM_ENABLED is False
    assert reloaded.CAMPAIGN_COMPLETION_ENABLED is False and reloaded.CAMPAIGN_RECOVERY_ENABLED is False


def test_pending_periods_continue_fairly_until_actual_full_proof(enabled):
    w,app=enabled
    for p in (1499,1500):
        row=get(w,'pipeline',partition(p),'TOMBSTONE')
        row['locatorCleanupState']['operationId']='00000000-0000-4000-8000-000000000003'
        w.put(row)
    for _ in range(2):
        with pytest.raises(RuntimeError,match='RECONCILIATION_REQUIRED'):app.lambda_handler(event(),Context())
        assert get(w,'ledger',CMD['PK'],R.JOB_PREFIX+OP) is not None
    assert app.lambda_handler(event(),Context())['completed']==1


def test_scheduled_completion_lost_ack_does_not_leave_due_work(enabled):
    w,app=enabled;add_index(w);real=w.d.transact_write_items;lost=[]
    def lose_completion(**kw):
        out=real(**kw)
        if any('Put' in a and R.plain(a['Put']['Item']).get('SK')=='ACCOUNT_DELETION#CAMPAIGN' for a in kw['TransactItems']):
            lost.append(True);raise RuntimeError('synthetic lost completion ack')
        return out
    w.d.transact_write_items=lose_completion
    out=app.lambda_handler({'schemaVersion':1,'operation':'reconcile-campaign-cleanup'},Context())
    assert lost==[True] and out['CommandsCompleted']==1 and out['CommandsUnverified']==0
    assert get(w,'ledger',CMD['PK'],R.JOB_PREFIX+OP) is None


def test_future_terminal_never_acknowledged_even_when_completion_disabled(enabled,monkeypatch):
    w,app=enabled;monkeypatch.setattr(app.config,'CAMPAIGN_COMPLETION_ENABLED',False)
    w.put(CMD|{'status':'COMPLETE','eventType':'account.deletion.completed','completedAtEpoch':NOW+100,
               'retainUntilEpoch':NOW+100+120*86400},'ledger')
    before=w.snapshot()
    with pytest.raises(RuntimeError,match='RECONCILIATION_REQUIRED'):app.lambda_handler(event(),Context())
    assert w.snapshot()==before


def test_low_budget_never_starts_cleanup_or_completion(enabled):
    w,app=enabled;before=w.snapshot()
    class Short(Context):
        def get_remaining_time_in_millis(self):return 5000
    with pytest.raises(RuntimeError,match='RECONCILIATION_REQUIRED'):app.lambda_handler(event(),Short())
    assert w.snapshot()==before and not w.calls


def test_declared_command_with_wrong_key_is_not_ignored_as_shared_metadata(enabled):
    w,app=enabled;before=w.snapshot()
    with pytest.raises(RuntimeError,match='RECONCILIATION_REQUIRED'):
        app.lambda_handler(event(CMD|{'SK':'CAMPAIGN_RECOVERY_CONTROL'}),Context())
    assert w.snapshot()==before and not w.calls


def test_budget_drop_after_key_read_stops_before_mac_dispatch(enabled):
    w,app=enabled;remaining=[30000];real=w.d.get_item
    def read(**kw):
        out=real(**kw)
        if kw['TableName']=='pipeline' and R.plain(kw['Key'])=={'PK':'PERIOD#1500','SK':'HMAC_KEY'}:remaining[0]=5000
        return out
    w.d.get_item=read
    class Falling(Context):
        def get_remaining_time_in_millis(self):return remaining[0]
    before=w.snapshot()
    with pytest.raises(RuntimeError,match='RECONCILIATION_REQUIRED'):app.lambda_handler(event(),Falling())
    assert w.calls==[] and w.snapshot()==before
