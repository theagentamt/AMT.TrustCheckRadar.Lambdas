"""Independent adversarial reducer checks; entirely local synthetic facts."""
import copy
import json
from itertools import permutations

import pytest

from operations_reporting.reducer import (
    CORRECTION_SECONDS, ProjectionFault, aggregate_day, day_bounds,
    reconcile_rejection_snapshot,
)

DAY = '2026-10-03'
START, END = day_bounds(DAY)


def admission(*, at=START + 10, ref='1', identity='1'):
    return dict(schemaVersion='1.0.0-operations-candidate.1', eventType='check_accepted',
                eventId='op1_' + identity * 32, eventTimeEpoch=at, environment='dev',
                trafficClass='customer', service='message_check', inputKind='ocr',
                logicalCheckRef='lc1_' + ref * 32, lifecycleRevision=1,
                processingOutcome=None, accountingState='pending', chargedChecks=None,
                accessClass='paid', reasonCode='NONE', attemptRef=None, attemptKind=None,
                component=None, attemptOrdinal=None, providerFamily=None,
                attemptOutcome=None, durationBucket=None, providerRequestUnits=None,
                estimatedCostMicrousd=None)


def terminal(a, *, at=None):
    return dict(a, eventType='check_terminal', eventId='op1_' + '2' * 32,
                eventTimeEpoch=a['eventTimeEpoch'] + 3 if at is None else at,
                lifecycleRevision=2, processingOutcome='complete', accountingState='charged', chargedChecks=1)


def attempt(a, *, at=None):
    return dict(a, eventType='attempt_started', eventId='op1_' + '3' * 32,
                eventTimeEpoch=a['eventTimeEpoch'] + 1 if at is None else at,
                lifecycleRevision=None, accountingState=None, accessClass=None,
                attemptRef='at1_' + '3' * 32, attemptKind='provider_call',
                component='message_evaluator', attemptOrdinal=1, providerFamily='openai')


def finish(b, *, cost=0, units=0):
    return dict(b, eventType='attempt_finished', eventId='op1_' + '4' * 32,
                eventTimeEpoch=b['eventTimeEpoch'] + 2, attemptOutcome='succeeded',
                durationBucket='lt_250ms', estimatedCostMicrousd=cost, providerRequestUnits=units)


def run(rows, *, now=END + 60, day=DAY, rejects=None):
    return aggregate_day(rows, [] if rejects is None else rejects, local_day=day,
                         environment='dev', as_of_epoch=now)


def rejection(count=3):
    return dict(schemaVersion='1.0.0-rejection-aggregate-candidate.1',
                eventType='request_rejected', windowStartEpoch=START,
                windowSeconds=300, environment='dev', trafficClass='customer',
                service='message_check', reasonCode='RATE_LIMITED', rejectedCount=count)


def test_every_delivery_permutation_is_same_without_mutating_callers():
    a = admission(); b = attempt(a); facts = [a, terminal(a), b, finish(b)]
    original = copy.deepcopy(facts)
    expected = run(facts)
    for order in permutations(facts):
        assert run(list(order) * 2) == expected
    assert facts == original
    assert expected['accepted'] == expected['settled']['complete'] == 1
    assert expected['providerAttemptsByFamily']['openai']['started'] == 1


@pytest.mark.parametrize('cost,units', [(None, None), (0, 0), (0, None), (None, 0), (999, 2)])
def test_unknown_cost_and_unit_coverage_are_independent_of_zero(cost, units):
    a = admission(); b = attempt(a)
    counts = run([a, b, finish(b, cost=cost, units=units)])['providerAttemptsByFamily']['openai']
    assert counts['costKnownProviderAttempts'] == int(cost is not None)
    assert counts['costUnknownProviderAttempts'] == int(cost is None)
    assert counts['requestUnitsKnownProviderAttempts'] == int(units is not None)
    assert counts['requestUnitsUnknownProviderAttempts'] == int(units is None)
    assert counts['knownEstimatedCostMicrousd'] == (cost or 0)
    assert counts['knownProviderRequestUnits'] == (units or 0)


def test_midnight_accepted_and_attempt_cohorts_do_not_move_on_late_finish():
    a = admission(at=END - 2); b = attempt(a, at=END + 1)
    old_day = run([a, b, finish(b)], now=END + 5)
    new_day = run([a, b, finish(b)], now=END + 5, day='2026-10-04')
    assert old_day['accepted'] == 1 and new_day['accepted'] == 0
    assert old_day['providerAttemptsByFamily']['openai']['started'] == 0
    assert new_day['providerAttemptsByFamily']['openai']['started'] == 1


def test_same_phase_with_another_identity_cannot_be_an_extra_check_or_attempt():
    a = admission(); b = attempt(a)
    for original in [a, b]:
        alias = dict(original, eventId='op1_' + 'f' * 32)
        with pytest.raises(ProjectionFault, match='^FACT_CONFLICT$'):
            run([a, original, alias])


def test_rejection_replacement_is_immutable_monotonic_head_not_delta():
    old = rejection(); new = rejection(9)
    old_copy = copy.deepcopy(old)
    head = reconcile_rejection_snapshot(old, new)
    assert head == new and old == old_copy
    assert run([], rejects=[head, head])['requestRejectionsByService']['message_check'] == {'RATE_LIMITED': 9}
    for rows in [[old, head], [head, old]]:
        with pytest.raises(ProjectionFault, match='^REJECTION_CONTEXT_CONFLICT$'):
            run([], rejects=rows)
    with pytest.raises(ProjectionFault, match='^STALE_REJECTION$'):
        reconcile_rejection_snapshot(head, old)


def test_correction_cutoff_is_conservative_and_never_reverses_prior_summary():
    a = admission(at=END - 1)
    prior = run([a], now=START + CORRECTION_SECONDS - 1)
    assert prior['pending'] == 1
    with pytest.raises(ProjectionFault, match='^CORRECTION_WINDOW_CLOSED$'):
        run([a], now=START + CORRECTION_SECONDS)
    assert prior['pending'] == 1


@pytest.mark.parametrize('outcome', ['partial', 'failed', 'inconclusive', 'blocked'])
def test_noncomplete_charged_never_enters_totals(outcome):
    a = admission(); t = dict(terminal(a), processingOutcome=outcome)
    with pytest.raises(ProjectionFault, match='^INVALID_EVENT$'):
        run([a, t])


def test_private_extensions_cyclic_and_nested_values_never_echo_or_log(capsys, caplog):
    a = admission()
    cyclic = {}; cyclic['self'] = cyclic
    for value in ['sentinel-private-address@invalid.test', cyclic, {'subject': 'sentinel-private'}]:
        invalid = dict(a, submittedContent=value)
        with pytest.raises(ProjectionFault) as error:
            run([invalid])
        assert error.value.args == ('INVALID_EVENT',)
    assert not caplog.text and capsys.readouterr() == ('', '')


def test_aggregate_is_identifier_free_and_explicitly_not_qualified():
    a = admission(); b = attempt(a)
    report = run([a, terminal(a), b, finish(b)])
    wire = json.dumps(report)
    for ref in [a['eventId'], a['logicalCheckRef'], b['attemptRef']]:
        assert ref not in wire
    assert report['producerCoverage'] == report['providerPriceCatalogCoverage'] == 'not_qualified'
    assert report['awsCostCoverage'] == report['storeFeesAndRevenueCoverage'] == 'unavailable'
    assert report['deliveryPerformed'] is False


def test_authority_completion_prevents_fabricating_a_new_later_dispatch():
    a = admission(); t = terminal(a); b = attempt(a, at=t['eventTimeEpoch'] + 1)
    with pytest.raises(ProjectionFault, match='^INVALID_EVENT_TIME$'):
        run([a, t, b])
