"""Typed metadata compatibility with actual atomic paid-closure transactions."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import types

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_transactions import ACCOUNT, WORKER, world, admit, AuthorityError
from test_entitlements import writer_world
from test_purchase_shortening import funded, revoke
from test_governed_history import url_payload
from test_operations_producer import enable
from shared_check_authority import purchase_usage
from shared_check_authority.operations_ingestion import ReceiptIngestion
from shared_operations import receipt_events
from shared_operations.receipts import RETENTION_SECONDS

BASE = 'df6c54885e4cfc79646f36d6bc6972bdf09927b7'
ROOT = Path(__file__).resolve().parents[2]


def pending(writer_world, *, operations=True, history=False):
    writer, w, decisions, period = funded(writer_world)
    a, event, _, row, _, _ = w
    a.s = replace(a.s, operations_producer_enabled=operations,
        governed_history_settlement_enabled=history,
        receipt_retention_seconds=2*RETENTION_SECONDS, counter_retention_seconds=2*RETENTION_SECONDS)
    a.s.validate()
    if history:
        payload = url_payload()
        proof = a.prepare(event, payload, 'new-history', client_check_id='new-history')
        operation = a.admit(event, payload, proof, client_check_id='new-history')
    else:
        proof, operation = admit(w)
    return writer, w, decisions, period, proof, operation


@pytest.mark.parametrize('history,operations,captured', [(False,True,False),(False,True,True),
    (True,True,True),(True,False,False)])
def test_actual_due_closure_preserves_metadata_deadline_and_zero_charge(writer_world, history, operations, captured):
    writer,w,decisions,period,proof,operation = pending(writer_world,operations=operations,history=history)
    a, _, _, row, _, clock = w
    if captured: ReceiptIngestion(a).capture(WORKER, proof)
    before = deepcopy(row('CHECK#'+proof))
    result = revoke(writer,w,decisions,clock[0]-RETENTION_SECONDS)
    assert writer.synchronize_paid(w[1],'reference','revoke') == result
    settled = row('CHECK#'+proof)
    assert settled['state']=='SETTLED' and settled['chargedChecks']==0 and settled['processingOutcome']=='failed'
    assert settled['expiresAt']==settled['retentionDeadlineEpoch']==before['retentionDeadlineEpoch']
    assert a._get('authority',period['purchaseUsageKey']) is None
    assert row(period['SK'])['reservedChecks']==row(period['SK'])['usedChecks']==0
    assert row('INFLIGHT')['activeCount']==0
    if operations:
        assert settled['operationsReceipt']==before['operationsReceipt']
        assert settled['operationsTerminalReasonCode']=='OPERATION_EXPIRED'
        assert settled['GSI1SK'].startswith(f'{int(before["operationsReceipt"]["expiresAtEpoch"]):012d}#')
        facts=ReceiptIngestion(a).capture(WORKER,proof)['projection']
        assert facts['sourceState']=='SETTLED' and facts['events'][-1]['chargedChecks']==0
        assert facts['events'][-1]['reasonCode']=='OPERATION_EXPIRED'
    if history:
        assert settled['governedHistoryAcceptedAtEpoch']==before['governedHistoryAcceptedAtEpoch']
        assert 'governedHistory' not in settled  # no result narrative reconstructed
    assert a.settle(WORKER,proof,operation['executionToken'],'complete')['chargedChecks']==0


def test_due_closure_removes_expired_projection_without_new_ttl_or_reference(writer_world):
    writer,w,decisions,period,proof,_ = pending(writer_world)
    a,event,_,row,_,clock=w
    ReceiptIngestion(a).capture(WORKER,proof)
    before=row('CHECK#'+proof)
    clock[0]=int(before['operationsReceipt']['expiresAtEpoch'])+1
    event['requestContext']['authorizer']['jwt']['claims']['exp']=str(clock[0]+90)
    revoke(writer,w,decisions,clock[0]-RETENTION_SECONDS)
    settled=row('CHECK#'+proof)
    assert not {'operationsReceipt','operationsTerminalReasonCode','operationsProjection'} & set(settled)
    assert settled['expiresAt']==before['retentionDeadlineEpoch']
    assert receipt_events(settled,now_epoch=clock[0])=={'coverage':'NOT_RECORDED','events':[]}
    assert row(period['SK'])['usedChecks']==0


@pytest.mark.parametrize('mutation', ['unknown-field','partial-history','wrong-history-type','wrong-service',
    'wrong-access','wrong-deadline','swapped-capture'])
def test_malformed_metadata_still_refuses_without_accounting_mutation(writer_world,mutation):
    writer,w,decisions,period,proof,_=pending(writer_world,history=True)
    a,_,_,row,_,clock=w
    ReceiptIngestion(a).capture(WORKER,proof)
    changed=deepcopy(row('CHECK#'+proof))
    if mutation=='unknown-field':changed['unknown']=True
    elif mutation=='partial-history':changed.pop('governedHistorySourceType')
    elif mutation=='wrong-history-type':changed['governedHistoryResultType']='message'
    elif mutation=='wrong-service':changed['operationsReceipt'].update(service='message_check',inputKind='pasted_text')
    elif mutation=='wrong-access':changed['operationsReceipt']['accessClass']='trial'
    elif mutation=='wrong-deadline':changed['operationsReceipt']['expiresAtEpoch']=int(changed['retentionDeadlineEpoch'])+1
    else:changed['operationsProjection']['reference']['logicalCheckRef']='lc1_'+'e'*32
    a.ddb.Table('authority').put_item(Item=changed)
    before=a.ddb.Table('authority').scan()['Items']
    with pytest.raises(AuthorityError,match='^PURCHASE_USAGE_RECONCILIATION_REQUIRED$'):
        revoke(writer,w,decisions,clock[0]-RETENTION_SECONDS)
    assert a.ddb.Table('authority').scan()['Items']==before


def test_concurrent_first_capture_is_not_erased_by_old_paid_snapshot(writer_world,monkeypatch):
    writer,w,decisions,period,proof,_=pending(writer_world)
    a,_,_,row,_,clock=w
    real=a.client.transact_write_items
    def competing(**kwargs):
        monkeypatch.setattr(a.client,'transact_write_items',real)
        assert ReceiptIngestion(a).capture(WORKER,proof)['decision']=='CAPTURED'
        return real(**kwargs)
    monkeypatch.setattr(a.client,'transact_write_items',competing)
    with pytest.raises(AuthorityError):revoke(writer,w,decisions,clock[0]-RETENTION_SECONDS)
    assert row('CHECK#'+proof)['state']=='ADMITTED'
    assert row('CHECK#'+proof)['operationsProjection']['sourceState']=='ADMITTED'
    assert row(period['SK'])['reservedChecks']==1 and row('INFLIGHT')['activeCount']==1


@pytest.mark.parametrize('scope',['full_url','origin_only','sanitized_message','recovery_clarification'])
@pytest.mark.parametrize('retained',[False,True])
def test_legacy_paid_actions_exactly_match_pinned_release(world,scope,retained):
    a,_,_,row,_,clock=world
    if retained:
        a.s=replace(a.s,receipt_retention_seconds=2*RETENTION_SECONDS,counter_retention_seconds=2*RETENTION_SECONDS)
    proof,_=admit(world)
    pending=row('CHECK#'+proof)|{'projectionScope':scope,'clientCheckId':'legacy-client'}
    if scope=='recovery_clarification':
        from shared_recovery_contract.constants import VERSION
        pending['recoveryTransportVersion']=VERSION
    a.ddb.Table('authority').put_item(Item=pending)
    period=row('PERIOD#p1');usage=a._get('authority',period['purchaseUsageKey'])
    now=int(usage['expiresAt'])+1
    module=types.ModuleType('shared_check_authority._pinned_usage');module.__package__='shared_check_authority'
    old=subprocess.check_output(['git','show',BASE+':src/shared_check_authority/purchase_usage.py'],cwd=ROOT)
    exec(compile(old,'<pinned purchase_usage>','exec'),module.__dict__)
    before=module.due_reservation_actions(a.ddb,'authority',period,usage,period['endEpoch'],now=now)
    after=purchase_usage.due_reservation_actions(a.ddb,'authority',period,usage,period['endEpoch'],now=now)
    assert after==before
