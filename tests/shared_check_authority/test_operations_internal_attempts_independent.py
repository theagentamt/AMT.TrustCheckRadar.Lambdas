"""Independent actual Moto attempt-ledger race/replay/privacy regressions."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_transactions import world, WORKER, ACCOUNT, AuthorityError, TrustedWorkerContext
from test_operations_internal_attempts import start, attempt_events
from test_operations_ingestion import fresh
from shared_check_authority.operations_retirement import OperationsRetirement
from shared_operations.projection import validate_capture
from shared_operations.receipts import OperationsReceiptFault
from shared_operations.attempts import _finish_id


def test_plain_capture_cannot_return_future_attempt_after_wall_clock_rollback(world):
    ingest, proof, op = fresh(world)
    clock = world[5]
    accepted = clock[0]
    clock[0] += 10
    ingest.start_attempt(WORKER, proof, op['executionToken'])
    before = deepcopy(world[3]('CHECK#' + proof))
    clock[0] = accepted + 5
    with pytest.raises(AuthorityError):
        ingest.capture(WORKER, proof)
    assert world[3]('CHECK#' + proof) == before


def test_finish_event_id_cannot_alias_accepted_or_terminal_event(world):
    from shared_operations.attempts import finish_internal_attempt
    _, _, _, first = start(world)
    base = first['projection']
    begun = attempt_events(base)[0]
    for key in ('acceptedEventId', 'terminalEventId'):
        captured = deepcopy(base)
        captured['reference'][key] = _finish_id(begun['attemptRef'])
        if key == 'acceptedEventId':
            captured['events'][0]['eventId'] = captured['reference'][key]
        with pytest.raises(OperationsReceiptFault):
            finish = finish_internal_attempt(captured['reference'], begun,
                observed_at=begun['eventTimeEpoch'], outcome='succeeded', duration_ms=1)
            captured['events'].append(finish)
            validate_capture(captured)


def test_validated_projection_cannot_gain_identifier_between_validation_and_return(world, monkeypatch):
    import shared_operations.projection as module
    _, _, _, first = start(world)
    captured = deepcopy(first['projection'])
    real_copy = module.deepcopy
    def changed_before_copy(value):
        captured['events'][0]['accountId'] = 'SYNTHETIC_FORBIDDEN_IDENTIFIER'
        return real_copy(value)
    monkeypatch.setattr(module, 'deepcopy', changed_before_copy)
    with pytest.raises(OperationsReceiptFault):
        module.validate_capture(captured)


@pytest.mark.parametrize('committed', [False, True])
def test_finish_uncertain_ack_is_one_write_no_retry_and_explicit_reconciliation(world, monkeypatch, committed):
    ingest, proof, op, first = start(world)
    begun = attempt_events(first['projection'])[0]
    real = ingest.client.transact_write_items
    calls = []
    before = deepcopy(world[3]('CHECK#' + proof))
    def uncertain(**kwargs):
        calls.append(1)
        if committed:
            real(**kwargs)
        raise TimeoutError('SYNTHETIC_PRIVATE_WRITE_DETAIL')
    monkeypatch.setattr(ingest.client, 'transact_write_items', uncertain)
    with pytest.raises(AuthorityError, match='^OPERATIONS_INGESTION_WRITE_UNKNOWN$'):
        ingest.finish_attempt(WORKER, proof, op['executionToken'], begun['attemptRef'],
                              outcome='succeeded', duration_ms=100)
    assert calls == [1]
    after = world[3]('CHECK#' + proof)
    assert len(attempt_events(after['operationsProjection'])) == (2 if committed else 1)
    assert {k: v for k, v in after.items() if k != 'operationsProjection'} == {
        k: v for k, v in before.items() if k != 'operationsProjection'}
    monkeypatch.setattr(ingest.client, 'transact_write_items', real)
    result = ingest.capture(WORKER, proof)
    assert len(attempt_events(result['projection'])) == (2 if committed else 1)
    assert world[3]('PERIOD#p1')['usedChecks'] == 0


def test_distinct_start_writers_cas_to_one_stable_attempt(world, monkeypatch):
    ingest, proof, op = fresh(world)
    real = ingest.client.transact_write_items
    winner = []
    def racing_start(**kwargs):
        monkeypatch.setattr(ingest.client, 'transact_write_items', real)
        winner.append(ingest.start_attempt(WORKER, proof, op['executionToken']))
        return real(**kwargs)
    monkeypatch.setattr(ingest.client, 'transact_write_items', racing_start)
    with pytest.raises(AuthorityError, match='^OPERATIONS_INGESTION_CONFLICT$'):
        ingest.start_attempt(WORKER, proof, op['executionToken'])
    retained = world[3]('CHECK#' + proof)['operationsProjection']
    assert retained == winner[0]['projection'] and len(attempt_events(retained)) == 1
    assert ingest.start_attempt(WORKER, proof, op['executionToken'])['projection'] == retained


def test_conflicting_finish_writers_never_replace_the_winner(world, monkeypatch):
    ingest, proof, op, first = start(world)
    ref = attempt_events(first['projection'])[0]['attemptRef']
    real = ingest.client.transact_write_items
    winner = []
    def racing_finish(**kwargs):
        monkeypatch.setattr(ingest.client, 'transact_write_items', real)
        winner.append(ingest.finish_attempt(WORKER, proof, op['executionToken'], ref,
                                          outcome='failed', duration_ms=15000))
        return real(**kwargs)
    monkeypatch.setattr(ingest.client, 'transact_write_items', racing_finish)
    with pytest.raises(AuthorityError, match='^OPERATIONS_INGESTION_CONFLICT$'):
        ingest.finish_attempt(WORKER, proof, op['executionToken'], ref,
                              outcome='succeeded', duration_ms=100)
    assert world[3]('CHECK#' + proof)['operationsProjection'] == winner[0]['projection']
    assert attempt_events(winner[0]['projection'])[1]['attemptOutcome'] == 'failed'


def test_concurrent_authoritative_terminal_cannot_be_lost_by_finish(world, monkeypatch):
    ingest, proof, op, first = start(world)
    a = world[0]
    ref = attempt_events(first['projection'])[0]['attemptRef']
    real = ingest.client.transact_write_items
    def settled_during_finish(**kwargs):
        monkeypatch.setattr(ingest.client, 'transact_write_items', real)
        a.settle(WORKER, proof, op['executionToken'], 'complete')
        return real(**kwargs)
    monkeypatch.setattr(ingest.client, 'transact_write_items', settled_during_finish)
    with pytest.raises(AuthorityError, match='^OPERATIONS_INGESTION_CONFLICT$'):
        ingest.finish_attempt(WORKER, proof, op['executionToken'], ref,
                              outcome='succeeded', duration_ms=100)
    assert world[3]('CHECK#' + proof)['chargedChecks'] == 1
    captured = ingest.capture(WORKER, proof)['projection']
    assert len(attempt_events(captured)) == 1
    assert captured['events'][1]['eventType'] == 'check_terminal'
    assert captured['events'][1]['chargedChecks'] == 1


def test_finish_write_delayed_past_original_deadline_returns_no_expired_projection(world, monkeypatch):
    ingest, proof, op, first = start(world)
    a, _, _, row, _, clock = world
    ref = attempt_events(first['projection'])[0]['attemptRef']
    a.settle(WORKER, proof, op['executionToken'], 'complete')
    original = deepcopy(row('CHECK#' + proof))
    deadline = int(original['operationsReceipt']['expiresAtEpoch'])
    clock[0] = deadline - 1
    real = ingest.client.transact_write_items
    calls = []
    def slow(**kwargs):
        calls.append(1)
        clock[0] = deadline
        return real(**kwargs)
    monkeypatch.setattr(ingest.client, 'transact_write_items', slow)
    result = ingest.finish_attempt(WORKER, proof, op['executionToken'], ref,
                                  outcome='succeeded', duration_ms=100)
    assert result == {'decision': 'EXPIRED', 'projection': None} and calls == [1]
    # A client-side dispatch check is not a server clock/physical erasure claim.
    assert row('CHECK#' + proof)['operationsReceipt'] == original['operationsReceipt']
    monkeypatch.setattr(ingest.client, 'transact_write_items', real)
    assert ingest.capture(WORKER, proof) == {'decision': 'EXPIRED', 'projection': None}
    assert not {'operationsReceipt', 'operationsProjection'} & set(row('CHECK#' + proof))
    assert row('PERIOD#p1')['usedChecks'] == 1


@pytest.mark.parametrize('mutation', ['profile-deleting', 'deletion-ledger'])
def test_finish_revalidates_account_deletion_at_write_boundary(world, monkeypatch, mutation):
    ingest, proof, op, first = start(world)
    _, _, put, row, change, _ = world
    ref = attempt_events(first['projection'])[0]['attemptRef']
    real = ingest.client.transact_write_items
    before = deepcopy(row('CHECK#' + proof))
    def deleting(**kwargs):
        monkeypatch.setattr(ingest.client, 'transact_write_items', real)
        if mutation == 'profile-deleting':
            change('users', 'PROFILE', status='DELETING')
        else:
            put('deletion', 'ACCOUNT_DELETION', status='REQUESTED')
        return real(**kwargs)
    monkeypatch.setattr(ingest.client, 'transact_write_items', deleting)
    with pytest.raises(AuthorityError, match='^OPERATIONS_INGESTION_CONFLICT$'):
        ingest.finish_attempt(WORKER, proof, op['executionToken'], ref,
                              outcome='succeeded', duration_ms=100)
    assert row('CHECK#' + proof) == before


def test_missing_terminal_reason_still_uses_actual_assessment_time_to_fence_start(world):
    ingest, proof, op = fresh(world)
    clock = world[5]
    accepted = clock[0]
    clock[0] += 10
    ingest.start_attempt(WORKER, proof, op['executionToken'])
    # A wall-clock rollback must not make an accepted-only terminal projection
    # conceal that the recorded attempt began after its actual settlement.
    clock[0] = accepted + 5
    world[0].settle(WORKER, proof, op['executionToken'], 'unavailable')
    row = deepcopy(world[3]('CHECK#' + proof))
    assert row.get('operationsTerminalReasonCode') is None
    with pytest.raises(AuthorityError, match='^OPERATIONS_INGESTION_SOURCE_INVALID$'):
        ingest.capture(WORKER, proof)
    assert world[3]('CHECK#' + proof) == row
    assert world[3]('PERIOD#p1')['usedChecks'] == 0


@pytest.mark.parametrize('ack', [None, {}, {'ResponseMetadata': {'HTTPStatusCode': 503}}])
def test_nonacknowledged_finish_response_never_returns_projection_or_retries(world, monkeypatch, ack):
    ingest, proof, op, first = start(world)
    ref = attempt_events(first['projection'])[0]['attemptRef']
    real = ingest.client.transact_write_items
    calls = []
    def malformed_ack(**kwargs):
        calls.append(1)
        real(**kwargs)
        return ack
    monkeypatch.setattr(ingest.client, 'transact_write_items', malformed_ack)
    with pytest.raises(AuthorityError, match='^OPERATIONS_INGESTION_WRITE_UNKNOWN$'):
        ingest.finish_attempt(WORKER, proof, op['executionToken'], ref,
                              outcome='succeeded', duration_ms=100)
    assert calls == [1] and len(attempt_events(world[3]('CHECK#' + proof)['operationsProjection'])) == 2


def test_malformed_stored_execution_token_is_finite_source_rejection(world):
    ingest, proof, op = fresh(world)
    world[4]('authority', 'CHECK#' + proof, executionToken='é' * 32)
    before = deepcopy(world[3]('CHECK#' + proof))
    with pytest.raises(AuthorityError, match='^OPERATIONS_INGESTION_SOURCE_INVALID$'):
        ingest.start_attempt(WORKER, proof, op['executionToken'])
    assert world[3]('CHECK#' + proof) == before
