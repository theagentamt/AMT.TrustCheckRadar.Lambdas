"""Actual local Moto transactions; no AWS session, endpoint or runtime calls."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_transactions import ACCOUNT, WORKER, AuthorityError, TrustedWorkerContext, world, admit
from test_operations_producer import enable, SCHEMA
from test_operations_retirement import history_receipt
from test_deletion import setup, finish, items
from shared_check_authority.operations_ingestion import ReceiptIngestion
from shared_check_authority.operations_retirement import OperationsRetirement
from shared_check_authority.expiry import Expiry
from shared_check_authority.inventory import INVENTORY_KEY
from shared_operations.projection import validate_capture
from shared_operations.receipts import OperationsReceiptFault


def fresh(world):
    enable(world[0])
    proof, operation = admit(world)
    return ReceiptIngestion(world[0]), proof, operation


def test_actual_receipt_capture_and_replay_preserve_every_other_field(world):
    a, _, _, row, _, clock = world
    ingest, proof, _ = fresh(world)
    before = deepcopy(row('CHECK#' + proof)); ledger = deepcopy(row('PERIOD#p1'))
    first = ingest.capture(WORKER, proof)
    assert first['decision'] == 'CAPTURED'
    captured = first['projection']
    assert captured['sourceState'] == 'ADMITTED' and captured['coverage'] == 'ACCEPTED_ONLY'
    assert captured['reference']['expiresAtEpoch'] == before['operationsReceipt']['expiresAtEpoch']
    assert row('CHECK#' + proof) == before | {'operationsProjection': captured}
    assert row('PERIOD#p1') == ledger and row('INFLIGHT')['activeCount'] == 1
    clock[0] += 10
    replay = ingest.capture(WORKER, proof)
    assert replay == first | {'decision': 'REVALIDATED'}
    for event in captured['events']: SCHEMA.validate(event)
    encoded = json.dumps(captured)
    for value in [ACCOUNT, proof, before['payloadHmac'], before['executionToken'], 'https://']:
        assert value not in encoded
    assert not any(r['SK'].startswith('OPS') for r in a.ddb.Table('authority').scan()['Items'])


@pytest.mark.parametrize('outcome,reason,charge', [('complete', None, 1), ('inconclusive', None, 0),
    ('failed', 'SETTLED_FAILED', 0), ('partial', None, 0), ('blocked', None, 0),
    ('invalid_input', None, 0), ('unsupported', None, 0), ('unavailable', 'SERVICE_DISABLED', 0)])
def test_capture_enriches_authoritative_settlement_without_recharging(world, outcome, reason, charge):
    a, _, _, row, _, _ = world
    ingest, proof, operation = fresh(world)
    old = ingest.capture(WORKER, proof)['projection']
    result = a.settle(WORKER, proof, operation['executionToken'], outcome, operations_reason_code=reason)
    captured = ingest.capture(WORKER, proof)['projection']
    assert captured['sourceState'] == 'SETTLED' and captured['reference'] == old['reference']
    assert captured['events'][0] == old['events'][0]
    assert len(captured['events']) == (1 if reason is None and outcome == 'partial' else 2)
    assert result['chargedChecks'] == row('PERIOD#p1')['usedChecks'] == charge
    before = deepcopy(row('PERIOD#p1'))
    assert ingest.capture(WORKER, proof)['decision'] == 'REVALIDATED'
    assert row('PERIOD#p1') == before
    for event in captured['events']: SCHEMA.validate(event)


@pytest.mark.parametrize('race', ['deletion', 'access', 'inventory', 'erase', 'terminal', 'projection'])
def test_atomic_source_and_account_races_cannot_publish_stale_facts(world, monkeypatch, race):
    a, _, put, row, change, _ = world
    ingest, proof, operation = fresh(world)
    real = ingest.client.transact_write_items; calls = []
    def changed(**kwargs):
        calls.append(1)
        monkeypatch.setattr(ingest.client, 'transact_write_items', real)
        if race == 'deletion': put('deletion', 'ACCOUNT_DELETION', status='REQUESTED')
        elif race == 'access': change('authority', 'ACCESS', state='DELETING')
        elif race == 'inventory':
            inventory = a._get('authority', INVENTORY_KEY)
            a.ddb.Table('authority').put_item(Item=inventory | {'revision': 2})
        elif race == 'erase':
            a.ddb.Table('authority').delete_item(Key={'PK': a._partition(ACCOUNT, 'k1'), 'SK': 'CHECK#' + proof})
        elif race == 'terminal': a.settle(WORKER, proof, operation['executionToken'], 'complete')
        else: assert ingest.capture(WORKER, proof)['decision'] == 'CAPTURED'
        return real(**kwargs)
    monkeypatch.setattr(ingest.client, 'transact_write_items', changed)
    with pytest.raises(AuthorityError, match='^OPERATIONS_INGESTION_CONFLICT$'):
        ingest.capture(WORKER, proof)
    after = row('CHECK#' + proof)
    if race == 'erase': assert after is None
    elif race == 'projection': assert after['operationsProjection']['sourceState'] == 'ADMITTED'
    else: assert 'operationsProjection' not in after
    assert len(calls) == 1


@pytest.mark.parametrize('owner', ['wrong-context', 'copied-row', 'client-json'])
def test_owner_isolation_uses_actual_proof_and_partition(world, owner):
    a, _, _, row, _, _ = world
    ingest, proof, _ = fresh(world)
    before = deepcopy(row('CHECK#' + proof))
    if owner == 'copied-row':
        a.ddb.Table('authority').put_item(Item=before | {'PK': a._partition('other-account', 'k1')})
    worker = {'account': ACCOUNT} if owner == 'client-json' else TrustedWorkerContext('other-account')
    with pytest.raises(AuthorityError): ingest.capture(worker, proof)
    assert row('CHECK#' + proof) == before


def test_deleted_or_restored_receipt_cannot_repopulate_after_deletion(world):
    a, _, _, row, _, _ = world
    ingest, proof, _ = fresh(world)
    ingest.capture(WORKER, proof)
    old = deepcopy(row('CHECK#' + proof))
    old_access = deepcopy(row('ACCESS'))
    old_profile = a._get('users', {'PK': 'USER#' + ACCOUNT, 'SK': 'PROFILE'})
    bridge, command, _ = setup(world)
    assert finish(bridge, command)['complete']
    assert items(a, a._partition(ACCOUNT, 'k1')) == []
    with pytest.raises(AuthorityError): ingest.capture(WORKER, proof)
    # A deliberately restored stale receipt is still fenced by the real
    # deletion ledger/profile. This is not a backup/restore deployment test.
    a.ddb.Table('authority').put_item(Item=old)
    a.ddb.Table('authority').put_item(Item=old_access)
    a.ddb.Table('users').put_item(Item=old_profile)
    with pytest.raises(AuthorityError): ingest.capture(WORKER, proof)
    assert row('CHECK#' + proof) == old


def test_lost_write_response_is_unknown_and_deliberate_replay_is_revalidated(world, monkeypatch):
    a, _, _, row, _, _ = world
    ingest, proof, _ = fresh(world)
    real = ingest.client.transact_write_items; calls = []
    def lost(**kwargs):
        calls.append(1); real(**kwargs); raise TimeoutError('PRIVATE_RESPONSE')
    monkeypatch.setattr(ingest.client, 'transact_write_items', lost)
    with pytest.raises(AuthorityError, match='^OPERATIONS_INGESTION_WRITE_UNKNOWN$'):
        ingest.capture(WORKER, proof)
    assert len(calls) == 1 and 'operationsProjection' in row('CHECK#' + proof)
    before = deepcopy(row('CHECK#' + proof))
    monkeypatch.setattr(ingest.client, 'transact_write_items', real)
    assert ingest.capture(WORKER, proof)['decision'] == 'REVALIDATED'
    assert row('CHECK#' + proof) == before


@pytest.mark.parametrize('boundary', ['before-write', 'after-write'])
def test_original_deadline_rechecked_around_dispatch_never_returns_expired_facts(world, monkeypatch, boundary):
    a, _, _, row, _, clock = world
    ingest, proof, _ = fresh(world)
    deadline = int(row('CHECK#' + proof)['operationsReceipt']['expiresAtEpoch'])
    if boundary == 'before-write':
        get = ingest._get
        def slow(table, key):
            result = get(table, key)
            if key['SK'] == 'ACCOUNT_DELETION': clock[0] = deadline
            return result
        monkeypatch.setattr(ingest, '_get', slow)
    else:
        real = ingest.client.transact_write_items
        def slow(**kwargs):
            result = real(**kwargs); clock[0] = deadline; return result
        monkeypatch.setattr(ingest.client, 'transact_write_items', slow)
    assert ingest.capture(WORKER, proof) == {'decision': 'EXPIRED', 'projection': None}
    assert ('operationsProjection' in row('CHECK#' + proof)) == (boundary == 'after-write')
    assert OperationsRetirement(a.ddb, 'authority', now=a.now).retire(a._partition(ACCOUNT, 'k1'), 'CHECK#' + proof)
    assert 'operationsProjection' not in row('CHECK#' + proof)


@pytest.mark.parametrize('orphan', [False, True])
def test_retirement_erases_all_captured_links_and_preserves_history(world, orphan):
    a, _, _, row, _, clock = world
    proof, before = history_receipt(world)
    ingest = ReceiptIngestion(a)
    ingest.capture(WORKER, proof)
    before = row('CHECK#' + proof)
    if orphan:
        # Earlier worker shapes can remove only the old fields and reschedule
        # at History expiry. New direct erasure accepts that known index shape.
        altered = deepcopy(before); altered.pop('operationsReceipt'); altered.pop('operationsTerminalReasonCode')
        altered['GSI1SK'] = f'{int(before["expiresAt"]):012d}#{before["PK"]}#{before["SK"]}'
        a.ddb.Table('authority').put_item(Item=altered)
    clock[0] = int(before['operationsReceipt']['expiresAtEpoch'])
    assert OperationsRetirement(a.ddb, 'authority', now=a.now).retire(before['PK'], before['SK'])
    after = row('CHECK#' + proof)
    assert not {'operationsReceipt', 'operationsTerminalReasonCode', 'operationsProjection'} & set(after)
    assert after['governedHistory'] == before['governedHistory']
    assert after['expiresAt'] == before['expiresAt']
    assert ingest.capture(WORKER, proof) == {'decision': 'NOT_RECORDED', 'projection': None}


@pytest.mark.parametrize('field,value', [('extra', 'PRIVATE'), ('schemaVersion', True),
    ('coverage', 'HEALTHY'), ('events', []), ('sourceState', 'UNKNOWN')])
def test_capture_schema_rejects_hostile_or_ambiguous_stored_values(world, field, value):
    _, _, _, _, _, _ = world
    ingest, proof, _ = fresh(world)
    captured = ingest.capture(WORKER, proof)['projection']
    captured[field] = value
    with pytest.raises(OperationsReceiptFault): validate_capture(captured)


def test_default_off_refuses_before_reads_and_no_metadata_is_explicit(world, monkeypatch):
    a, _, _, row, _, _ = world
    proof, _ = admit(world)
    ingest = ReceiptIngestion(a)
    with pytest.raises(AuthorityError, match='^OPERATIONS_INGESTION_DISABLED$'): ingest.capture(WORKER, proof)
    enable(a)
    assert ingest.capture(WORKER, proof) == {'decision': 'NOT_RECORDED', 'projection': None}
    assert 'operationsProjection' not in row('CHECK#' + proof)


def test_terminal_projection_cannot_be_overwritten_by_restored_admitted_source(world):
    a, _, _, row, _, _ = world
    ingest, proof, operation = fresh(world)
    admitted = deepcopy(row('CHECK#' + proof))
    a.settle(WORKER, proof, operation['executionToken'], 'complete')
    terminal = ingest.capture(WORKER, proof)['projection']
    restored = admitted | {'operationsProjection': terminal}
    a.ddb.Table('authority').put_item(Item=restored)
    with pytest.raises(AuthorityError, match='^OPERATIONS_INGESTION_SOURCE_INVALID$'):
        ingest.capture(WORKER, proof)
    assert row('CHECK#' + proof) == restored


@pytest.mark.parametrize('target', ['receipt', 'inventory', 'profile', 'deletion'])
def test_malformed_read_cannot_be_treated_as_source_or_deletion_absence(world, monkeypatch, target):
    a, _, _, row, _, _ = world
    ingest, proof, _ = fresh(world)
    factory = a.ddb.Table; writes = []
    def table(name):
        result = factory(name); get = result.get_item
        def read(**kwargs):
            sk = kwargs['Key']['SK']
            if (target == 'receipt' and sk == 'CHECK#' + proof
                    or target == 'inventory' and sk == 'HMAC_KEY_INVENTORY'
                    or target == 'profile' and sk == 'PROFILE'
                    or target == 'deletion' and sk == 'ACCOUNT_DELETION'):
                return {}
            return get(**kwargs)
        result.get_item = read
        return result
    monkeypatch.setattr(a.ddb, 'Table', table)
    monkeypatch.setattr(ingest.client, 'transact_write_items', lambda **kw: writes.append(kw))
    with pytest.raises(AuthorityError): ingest.capture(WORKER, proof)
    assert writes == []


def test_malformed_write_error_metadata_stays_finite_and_never_retries(world, monkeypatch):
    from botocore.exceptions import ClientError
    _, _, _, _, _, _ = world
    ingest, proof, _ = fresh(world)
    error = ClientError({'Error': {'Code': 'TransactionCanceledException'}}, 'TransactWriteItems')
    error.response['Error'] = 'PRIVATE_MALFORMED_SENTINEL'
    calls = []
    def write(**kwargs):
        calls.append(1); raise error
    monkeypatch.setattr(ingest.client, 'transact_write_items', write)
    with pytest.raises(AuthorityError, match='^OPERATIONS_INGESTION_WRITE_UNKNOWN$'):
        ingest.capture(WORKER, proof)
    assert len(calls) == 1


def test_seen_older_settled_index_is_repaired_atomically_without_new_deadline(world):
    a, _, _, row, _, clock = world
    proof, _ = history_receipt(world)
    before = row('CHECK#' + proof)
    old = deepcopy(before)
    old['GSI1SK'] = f'{int(before["expiresAt"]):012d}#{before["PK"]}#{before["SK"]}'
    a.ddb.Table('authority').put_item(Item=old)
    ingest = ReceiptIngestion(a)
    assert ingest.capture(WORKER, proof)['decision'] == 'CAPTURED'
    after = row('CHECK#' + proof)
    assert after['GSI1SK'] == before['GSI1SK']
    assert after['expiresAt'] == before['expiresAt']
    assert after['operationsReceipt'] == before['operationsReceipt']
    clock[0] = int(before['operationsReceipt']['expiresAtEpoch'])
    assert Expiry(a.ddb, 'authority', now=a.now).sweep()['operationsRetired'] == 1
    assert row('CHECK#' + proof)['governedHistory'] == before['governedHistory']
    assert 'operationsProjection' not in row('CHECK#' + proof)


@pytest.mark.parametrize('settled', [False, True])
def test_unknown_cleanup_index_cannot_acquire_new_projection(world, settled):
    a, _, _, row, _, _ = world
    ingest, proof, operation = fresh(world)
    if settled: a.settle(WORKER, proof, operation['executionToken'], 'complete')
    changed = row('CHECK#' + proof) | {'GSI1SK': 'UNKNOWN'}
    a.ddb.Table('authority').put_item(Item=changed)
    with pytest.raises(AuthorityError, match='^OPERATIONS_INGESTION_SOURCE_INVALID$'):
        ingest.capture(WORKER, proof)
    assert row('CHECK#' + proof) == changed
