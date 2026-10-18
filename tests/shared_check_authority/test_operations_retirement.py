"""Selective actual Moto removal, with no real service or deployment calls."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_transactions import ACCOUNT, PAYLOAD, WORKER, AuthorityError, world, admit
from test_governed_history import url_payload, url_summary
from shared_operations import receipt_events
from shared_operations.receipts import RETENTION_SECONDS
from shared_check_authority.operations_retirement import OperationsRetirement
from shared_check_authority.expiry import Expiry
from shared_check_authority.recovery import Recovery
from shared_check_authority.inventory import INVENTORY_KEY
from url_lease_recovery.app import run_passes


def enable(a, *, history=False):
    a.s = replace(a.s, operations_producer_enabled=True,
                  governed_history_settlement_enabled=history,
                  receipt_retention_seconds=RETENTION_SECONDS,
                  counter_retention_seconds=RETENTION_SECONDS)


def history_receipt(world, *, pause=False):
    a, e, _, row, _, clock = world
    enable(a, history=True)
    payload = url_payload()
    proof = a.prepare(e, payload, 'history', client_check_id='history')
    op = a.admit(e, payload, proof, client_check_id='history')
    if pause:
        a.s = replace(a.s, operations_producer_enabled=False)
    clock[0] += 5
    a.settle(WORKER, proof, op['executionToken'], 'complete', result_summary=url_summary('history'))
    return proof, row('CHECK#' + proof)


def helper(world):
    a = world[0]
    return OperationsRetirement(a.ddb, 'authority', now=a.now), a._partition(ACCOUNT, 'k1')


def all_rows(a):
    return sorted(a.ddb.Table('authority').scan()['Items'], key=lambda x: (x['PK'], x['SK']))


@pytest.mark.parametrize('pause', [False, True])
def test_settlement_indexes_earlier_ops_deadline_even_after_producer_pause(world, pause):
    proof, stored = history_receipt(world, pause=pause)
    meta = stored['operationsReceipt']
    assert stored['expiresAt'] == stored['retentionDeadlineEpoch'] == meta['expiresAtEpoch'] + 5
    assert stored['GSI1SK'].startswith(f'{int(meta["expiresAtEpoch"]):012d}#')
    if pause:
        assert 'operationsTerminalReasonCode' not in stored


def test_exact_deadline_removes_only_ops_and_reschedules_longer_history(world):
    a, e, _, row, _, clock = world
    proof, before = history_receipt(world)
    worker, pk = helper(world)
    clock[0] = int(before['operationsReceipt']['expiresAtEpoch']) - 1
    assert worker.retire(pk, 'CHECK#' + proof) is False
    assert row('CHECK#' + proof) == before
    ledger_before = all_rows(a)
    clock[0] += 1
    assert worker.retire(pk, 'CHECK#' + proof) is True
    after = row('CHECK#' + proof)
    expected = deepcopy(before)
    expected.pop('operationsReceipt'); expected.pop('operationsTerminalReasonCode')
    expected['GSI1SK'] = f'{int(before["expiresAt"]):012d}#{pk}#CHECK#{proof}'
    assert after == expected
    assert after['governedHistory'] == before['governedHistory']
    assert after['governedHistory']['expiresAtEpoch'] > clock[0]
    for old in ledger_before:
        if old['SK'] != 'CHECK#' + proof:
            assert a._get('authority', {k: old[k] for k in ('PK', 'SK')}) == old
    assert receipt_events(after, now_epoch=clock[0]) == {'coverage': 'NOT_RECORDED', 'events': []}
    assert worker.retire(pk, 'CHECK#' + proof) is False


def test_existing_bounded_expiry_sweep_physically_retires_then_later_deletes_history(world):
    a, e, _, row, _, clock = world
    proof, before = history_receipt(world)
    pk = a._partition(ACCOUNT, 'k1')
    clock[0] = int(before['operationsReceipt']['expiresAtEpoch'])
    worker = Expiry(a.ddb, 'authority', now=a.now)
    result = worker.sweep()
    assert result['operationsRetired'] == 1
    assert 'operationsReceipt' not in row('CHECK#' + proof)
    assert row('CHECK#' + proof)['expiresAt'] == before['expiresAt']
    clock[0] = int(before['expiresAt'])
    assert worker.expire(pk, 'CHECK#' + proof) is True
    assert row('CHECK#' + proof) is None
    assert row('PERIOD#p1')['usedChecks'] == 1


def test_both_deadlines_overdue_allow_original_full_row_delete_despite_earlier_index(world):
    a, e, _, row, _, clock = world
    proof, before = history_receipt(world)
    clock[0] = int(before['expiresAt'])
    assert Expiry(a.ddb, 'authority', now=a.now).expire(a._partition(ACCOUNT, 'k1'), 'CHECK#' + proof)
    assert row('CHECK#' + proof) is None
    assert row('PERIOD#p1')['usedChecks'] == 1


def test_pending_retirement_keeps_every_accounting_and_queue_field(world):
    a, e, _, row, _, clock = world
    enable(a)
    proof, op = admit(world)
    before = row('CHECK#' + proof)
    worker, pk = helper(world)
    clock[0] = int(before['operationsReceipt']['expiresAtEpoch'])
    ledger = deepcopy(row('PERIOD#p1'))
    assert worker.retire(pk, 'CHECK#' + proof)
    expected = deepcopy(before); expected.pop('operationsReceipt')
    assert row('CHECK#' + proof) == expected
    assert row('PERIOD#p1') == ledger and row('INFLIGHT')['activeCount'] == 1


def test_pending_queue_can_erase_then_recover_without_charge_or_reference_recreation(world):
    a, e, _, row, _, clock = world
    enable(a)
    proof, op = admit(world)
    clock[0] = int(row('CHECK#' + proof)['operationsReceipt']['expiresAtEpoch'])
    result = Recovery(a.ddb, 'authority', now=a.now).sweep()
    assert result['operationsRetired'] == 1 and result['recovered'] == 1
    after = row('CHECK#' + proof)
    assert 'operationsReceipt' not in after and 'operationsTerminalReasonCode' not in after
    assert after['state'] == 'SETTLED' and after['chargedChecks'] == 0
    assert row('PERIOD#p1')['reservedChecks'] == row('PERIOD#p1')['usedChecks'] == 0


def test_pause_after_pending_retirement_checkpoints_before_unrecovered_item(world):
    a, e, _, row, _, clock = world
    enable(a)
    proof, op = admit(world)
    clock[0] = int(row('CHECK#' + proof)['operationsReceipt']['expiresAtEpoch'])
    checks = iter([True, True, False])
    worker = Recovery(a.ddb, 'authority', now=a.now)
    first = worker.sweep(can_continue=lambda: next(checks))
    assert first['operationsRetired'] == 1 and first['recovered'] == 0 and first['cursor'] is None
    assert row('CHECK#' + proof)['state'] == 'ADMITTED'
    assert worker.sweep()['recovered'] == 1


@pytest.mark.parametrize('access', ['DELETING', 'MISSING'])
def test_erasure_can_remove_private_refs_during_deletion_or_orphan_without_recreating_authority(world, access):
    a, e, _, row, change, clock = world
    proof, before = history_receipt(world)
    worker, pk = helper(world)
    clock[0] = int(before['operationsReceipt']['expiresAtEpoch'])
    if access == 'MISSING':
        a.ddb.Table('authority').delete_item(Key={'PK': pk, 'SK': 'ACCESS'})
    else:
        change('authority', 'ACCESS', state=access)
    access_before = row('ACCESS')
    assert worker.retire(pk, 'CHECK#' + proof)
    assert row('ACCESS') == access_before
    assert row('CHECK#' + proof)['governedHistory'] == before['governedHistory']


@pytest.mark.parametrize('race', ['delete', 'metadata', 'inventory', 'authority'])
def test_concurrent_change_cannot_erase_wrong_generation_or_resurrect_deleted_row(world, monkeypatch, race):
    a, e, _, row, change, clock = world
    proof, before = history_receipt(world)
    worker, pk = helper(world)
    clock[0] = int(before['operationsReceipt']['expiresAtEpoch'])
    original = worker.client.transact_write_items
    calls = []
    def racing(**kwargs):
        calls.append(1)
        if race == 'delete':
            a.ddb.Table('authority').delete_item(Key={'PK': pk, 'SK': 'CHECK#' + proof})
        elif race == 'metadata':
            altered = deepcopy(before)
            altered['operationsReceipt']['logicalCheckRef'] = 'lc1_' + 'f' * 32
            a.ddb.Table('authority').put_item(Item=altered)
        elif race == 'inventory':
            inventory = a._get('authority', INVENTORY_KEY)
            a.ddb.Table('authority').put_item(Item={**inventory, 'revision': 2})
        else:
            change('authority', 'ACCESS', state='REVOKED')
        return original(**kwargs)
    monkeypatch.setattr(worker.client, 'transact_write_items', racing)
    if race == 'delete':
        assert worker.retire(pk, 'CHECK#' + proof) is False
        assert row('CHECK#' + proof) is None
    else:
        with pytest.raises(AuthorityError, match='OPERATIONS_RETIREMENT_TRANSACTION_UNCERTAIN'):
            worker.retire(pk, 'CHECK#' + proof)
        assert 'operationsReceipt' in row('CHECK#' + proof)
    assert len(calls) == 1 and row('PERIOD#p1')['usedChecks'] == 1


def test_write_response_lost_after_erasure_is_not_retried_or_counted_as_acknowledged(world, monkeypatch):
    a, e, _, row, _, clock = world
    proof, before = history_receipt(world)
    worker, pk = helper(world)
    clock[0] = int(before['operationsReceipt']['expiresAtEpoch'])
    original = worker.client.transact_write_items
    calls = []
    def uncertain(**kwargs):
        calls.append(1); original(**kwargs); raise RuntimeError('PRIVATE_RESPONSE')
    monkeypatch.setattr(worker.client, 'transact_write_items', uncertain)
    assert worker.retire(pk, 'CHECK#' + proof) is False
    assert len(calls) == 1 and 'operationsReceipt' not in row('CHECK#' + proof)
    assert worker.retire(pk, 'CHECK#' + proof) is False and len(calls) == 1


def test_erasure_never_remints_on_admission_or_settlement_replay(world):
    a, e, _, row, _, clock = world
    enable(a)
    proof, op = admit(world)
    a.settle(WORKER, proof, op['executionToken'], 'complete')
    before = row('CHECK#' + proof)
    worker, pk = helper(world)
    clock[0] = int(before['operationsReceipt']['expiresAtEpoch'])
    assert worker.retire(pk, 'CHECK#' + proof)
    # Exercise the early replay return independently of current request auth;
    # no proof/session refresh or new runtime request is made.
    a._account = lambda event: ACCOUNT
    a._verify_token = lambda *args, **kwargs: None
    assert a.admit(e, PAYLOAD, proof, count_attempt=False)['admitted'] is False
    # Existing settlement expires logically, so it cannot mint replacement refs.
    with pytest.raises(AuthorityError):
        a.settle(WORKER, proof, op['executionToken'], 'complete')
    assert 'operationsReceipt' not in row('CHECK#' + proof)


def test_missing_ops_default_noop_cannot_touch_history_or_other_families(world):
    a, e, _, row, _, clock = world
    proof, op = admit(world)
    a.settle(WORKER, proof, op['executionToken'], 'complete')
    before = all_rows(a)
    worker, pk = helper(world)
    assert worker.retire(pk, 'CHECK#' + proof) is False
    assert all_rows(a) == before
    with pytest.raises(AuthorityError, match='OPERATIONS_RETIREMENT_REFERENCE_INVALID'):
        worker.retire(pk, 'PERIOD#p1')


class Context:
    def get_remaining_time_in_millis(self):
        return 25000


def test_existing_scheduler_exposes_only_aggregate_retirement_count(world):
    a, e, _, row, _, clock = world
    proof, before = history_receipt(world)
    clock[0] = int(before['operationsReceipt']['expiresAtEpoch'])
    result = run_passes(a.ddb, 'authority', Context(), now=a.now)
    assert result['operationsRetired'] == 1 and result['failed'] == 0
    encoded = json.dumps(result)
    for private in [ACCOUNT, proof, before['operationsReceipt']['logicalCheckRef'], 'operationsReceipt', 'cursor']:
        assert private not in encoded
    assert row('CHECK#' + proof)['governedHistory'] == before['governedHistory']


@pytest.mark.parametrize('response', [{}, {'ResponseMetadata': {'HTTPStatusCode': 503}}])
def test_malformed_read_is_unavailable_not_acknowledged_absence(world, monkeypatch, response):
    a, _, _, row, _, clock = world
    proof, before = history_receipt(world)
    worker, pk = helper(world)
    clock[0] = int(before['operationsReceipt']['expiresAtEpoch'])
    table = a.ddb.Table('authority')
    monkeypatch.setattr(table, 'get_item', lambda **kwargs: response)
    monkeypatch.setattr(a.ddb, 'Table', lambda name: table)
    with pytest.raises(AuthorityError, match='^OPERATIONS_RETIREMENT_READ_UNAVAILABLE$'):
        worker.retire(pk, 'CHECK#' + proof)


def test_pause_after_settled_erasure_does_not_skip_due_full_receipt(world):
    a, _, _, row, _, clock = world
    proof, before = history_receipt(world)
    clock[0] = int(before['expiresAt'])
    worker = Expiry(a.ddb, 'authority', now=a.now)
    # Earlier preparation rows can also be due; stop precisely after this
    # receipt loses its operations metadata, not after a fixed callback count.
    first = worker.sweep(can_continue=lambda: 'operationsReceipt' in row('CHECK#' + proof))
    assert first['operationsRetired'] == 1 and row('CHECK#' + proof) is not None
    assert 'operationsReceipt' not in row('CHECK#' + proof)
    assert worker.sweep()['deleted'] >= 1
    assert row('CHECK#' + proof) is None
