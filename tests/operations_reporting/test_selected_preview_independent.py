"""Independent pure boundaries plus actual local Moto ingestion-to-preview."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pytest

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).resolve().parents[1] / 'shared_check_authority')]
from test_selected_preview import capture, preview, SCOPE, DAY, END, START
from test_transactions import world, WORKER, admit
from test_operations_producer import enable
from shared_check_authority.operations_ingestion import ReceiptIngestion
from operations_reporting import reducer
from operations_reporting.reducer import ProjectionFault, aggregate_selected_captures


@pytest.mark.parametrize('value', [None, {}, (), '[]'])
def test_nonlist_capture_batch_fails_closed(value):
    with pytest.raises(ProjectionFault, match='^INVALID_ARGUMENT$'):
        aggregate_selected_captures(value, local_day=DAY, as_of_epoch=END+1, source_scope=SCOPE)


def test_one_expired_input_aborts_whole_preview_even_if_outside_selected_day():
    expired = capture(accepted=START-100, expiry=START-1)
    with pytest.raises(ProjectionFault, match='^LINKAGE_EXPIRED$'):
        preview([capture(terminal=True), expired])


def test_terminal_after_supplied_asof_is_not_counted_as_current():
    settled = capture(terminal=True)
    settled['events'][1]['eventTimeEpoch'] = END+2
    with pytest.raises(ProjectionFault, match='^INVALID_EVENT_TIME$'):
        preview([settled], now=END+1)


def test_same_logical_reference_cannot_swap_original_event_identity():
    first = capture()
    changed = deepcopy(first)
    changed['reference']['acceptedEventId'] = 'op1_' + 'a'*32
    changed['events'][0]['eventId'] = changed['reference']['acceptedEventId']
    with pytest.raises(ProjectionFault, match='^FACT_CONFLICT$'):
        preview([first, changed])


def test_conflicting_terminal_snapshot_aborts_not_last_write_wins():
    first = capture(terminal=True)
    changed = deepcopy(first)
    changed['events'][1]['eventTimeEpoch'] += 1
    with pytest.raises(ProjectionFault, match='^EVENT_ID_CONFLICT$'):
        preview([first, changed])


def test_total_event_bound_is_checked_in_addition_to_capture_count(monkeypatch):
    monkeypatch.setattr(reducer, 'MAX_BATCH_FACTS', 5)
    with pytest.raises(ProjectionFault, match='^BATCH_BOUND_EXCEEDED$'):
        preview([capture(terminal=True)]*3)


def test_unencodable_unknown_value_has_only_finite_error():
    malformed = capture() | {'unexpected': '\ud800'}
    with pytest.raises(ProjectionFault, match='^INVALID_EVENT$') as caught:
        preview([malformed])
    assert str(caught.value) == 'INVALID_EVENT'


def test_actual_guarded_capture_attempt_settlement_and_replay_to_preview(world):
    authority, _, _, row, _, clock = world
    enable(authority)
    proof, operation = admit(world)
    ingest = ReceiptIngestion(authority)
    admitted = ingest.capture(WORKER, proof)['projection']
    start = ingest.start_attempt(WORKER, proof, operation['executionToken'])['projection']
    attempt_ref = next(e['attemptRef'] for e in start['events'] if e['eventType'] == 'attempt_started')
    clock[0] += 1
    authority.settle(WORKER, proof, operation['executionToken'], 'complete')
    settled = ingest.finish_attempt(WORKER, proof, operation['executionToken'], attempt_ref,
                                  outcome='succeeded', duration_ms=100)['projection']
    replay = ingest.capture(WORKER, proof)['projection']
    before = {name: deepcopy(authority.ddb.Table(name).scan()['Items'])
              for name in ('users', 'devices', 'deletion', 'authority')}
    snapshots = [replay, admitted, settled, start, replay]
    retained = deepcopy(snapshots)
    day = datetime.fromtimestamp(clock[0], ZoneInfo('America/Chicago')).date().isoformat()
    report = aggregate_selected_captures(snapshots, local_day=day, as_of_epoch=clock[0], source_scope=SCOPE)
    assert report['accepted'] == report['settled']['complete'] == 1
    assert report['byAccessClass']['paid']['deductedChecks'] == 1
    assert report['internalAttempts'] == {'started':1, 'awaitingFinish':0, 'succeeded':1, 'failed':0, 'denied':0}
    assert report['sourceScope']['coverage'] == 'selected_subset_not_environment_total'
    assert report['producerCoverage'] == 'not_qualified' and report['deliveryPerformed'] is False
    assert snapshots == retained
    assert {name: authority.ddb.Table(name).scan()['Items'] for name in before} == before
    deadline = settled['reference']['expiresAtEpoch']
    with pytest.raises(ProjectionFault, match='^LINKAGE_EXPIRED$'):
        aggregate_selected_captures(snapshots, local_day=day, as_of_epoch=deadline, source_scope=SCOPE)
    assert row('CHECK#' + proof)['operationsReceipt']['expiresAtEpoch'] == deadline
