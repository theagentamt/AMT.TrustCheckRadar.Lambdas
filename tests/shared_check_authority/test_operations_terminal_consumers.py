"""Actual Moto consumer settlement facts; no provider cost or runtime coverage claim."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_transactions import world
from test_operations_callers import bounded_world, consumer, api_event
from test_consumer import result as url_result
from shared_operations import consumer_terminal_reason, receipt_events
from operations_reporting.reducer import aggregate_day, day_bounds
from operations_reporting.revisions import validate_summary

FAMILIES = ['url_check', 'message_check', 'recovery_clarification']


def execute(w, family, provider):
    service, base, _, path, request = consumer(w, family)
    calls = []
    def dispatch(body):
        calls.append(1)
        return provider(body)
    service.provider = dispatch
    status, prepared = service.handle(api_event(base, 'POST ' + path + '/prepare', request))
    assert status == 200 and prepared['state'] == 'prepared'
    proof = prepared['operationProof']
    status, response = service.handle(api_event(base, 'POST ' + path, request | {'operationProof': proof}))
    assert status == 200 and response['state'] == 'settled'
    return service, base, path, request, proof, response, calls


def failure(_):
    raise TimeoutError('PRIVATE_PROVIDER_DIAGNOSTIC_SENTINEL')


def projections(w, proof):
    result = receipt_events(w[3]('CHECK#' + proof), now_epoch=w[5][0])
    return result


@pytest.mark.parametrize('family', FAMILIES)
def test_real_consumer_failure_is_one_known_zero_charge_settlement_and_replay(bounded_world, family):
    w = bounded_world
    service, base, path, req, proof, response, calls = execute(w, family, failure)
    assert response['accounting']['chargedChecks'] == 0
    projected = projections(w, proof)
    assert projected['coverage'] == 'CHECK_LIFECYCLE_RECORDED'
    accepted, terminal = projected['events']
    assert terminal['processingOutcome'] == ('failed' if family == 'url_check' else 'unavailable')
    assert terminal['reasonCode'] == ('SETTLED_FAILED' if family == 'url_check' else 'PROVIDER_UNAVAILABLE')
    assert terminal['chargedChecks'] == 0 and terminal['logicalCheckRef'] == accepted['logicalCheckRef']
    before = deepcopy(w[3]('PERIOD#p1'))
    for _ in range(2):
        status, replay = service.handle(api_event(base, 'POST ' + path + '/reconcile',
            {'transportVersion': req['transportVersion'], 'checkId': req['checkId'], 'operationProof': proof}))
        assert status == 200 and replay['accounting'] == response['accounting']
    assert calls == [1] and w[3]('PERIOD#p1') == before
    assert before['reservedChecks'] == 0 and before['usedChecks'] == 0
    assert projections(w, proof) == projected
    assert 'PRIVATE_PROVIDER' not in json.dumps(response) + json.dumps(projected)
    assert 'operations' not in json.dumps(response)
    # Duplicate terminal input is idempotent at the real aggregation boundary.
    day = datetime.fromtimestamp(w[5][0], timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('America/Chicago')).date().isoformat()
    summary = aggregate_day(projected['events'] * 2, [], local_day=day, environment='dev', as_of_epoch=day_bounds(day)[1] + 60)
    validate_summary(summary)
    assert summary['producerCoverage'] == 'not_qualified'
    service_counts = summary['byService'][family]
    assert service_counts['accepted'] == 1 and service_counts['pending'] == 0
    assert service_counts['settled'][terminal['processingOutcome']] == 1


@pytest.mark.parametrize('family', FAMILIES)
def test_real_validated_partial_is_generic_coverage_not_provider_root_cause(bounded_world, family):
    def partial(body):
        check = body['checkId']
        if family == 'url_check':
            return url_result(check) | {'verdict': 'high_risk', 'processingOutcome': 'partial', 'coverage': 'limited',
                'reasonCodes': ['KNOWN_THREAT_MATCH'], 'threatTypes': ['MALWARE'], 'lookupValidUntil': '2027-01-15T08:01:00Z'}
        if family == 'message_check':
            from message_evaluator.policy import result
            return result(check, rules=['REQUEST_SECRET_DISCLOSURE'], limits=['PROVIDER_UNAVAILABLE'])
        from shared_recovery_contract.validation import outcome
        return outcome(check, 'partial', 'INCOMPLETE_RESPONSE')
    w = bounded_world
    *_, proof, response, calls = execute(w, family, partial)
    projected = projections(w, proof)
    assert projected['coverage'] == 'CHECK_LIFECYCLE_RECORDED'
    terminal = projected['events'][1]
    assert terminal['processingOutcome'] == 'partial' and terminal['reasonCode'] == 'COVERAGE_PARTIAL'
    assert terminal['chargedChecks'] == response['accounting']['chargedChecks'] == 0 and calls == [1]


@pytest.mark.parametrize('family', ['message_check', 'recovery_clarification'])
@pytest.mark.parametrize('code', ['PROVIDER_UNAVAILABLE', 'PROVIDER_RESPONSE_INVALID', 'BUDGET_LIMIT'])
def test_unavailable_closed_result_only_records_single_provider_fact(bounded_world, family, code):
    def unavailable(body):
        if family == 'message_check':
            from message_evaluator.policy import result
            return result(body['checkId'], limits=[code])
        from shared_recovery_contract.validation import outcome
        return outcome(body['checkId'], 'unavailable', code)
    w = bounded_world
    *_, proof, response, calls = execute(w, family, unavailable)
    projected = projections(w, proof)
    assert response['accounting']['chargedChecks'] == 0 and calls == [1]
    if code == 'BUDGET_LIMIT':
        assert projected['coverage'] == 'TERMINAL_REASON_NOT_RECORDED' and len(projected['events']) == 1
    else:
        assert projected['coverage'] == 'CHECK_LIFECYCLE_RECORDED' and projected['events'][1]['reasonCode'] == code


def test_mixed_provider_and_budget_limit_preserves_unknown_not_precedence(bounded_world):
    from message_evaluator.policy import result
    w = bounded_world
    *_, proof, response, _ = execute(w, 'message_check',
        lambda body: result(body['checkId'], limits=['PROVIDER_UNAVAILABLE', 'BUDGET_LIMIT']))
    assert response['accounting']['chargedChecks'] == 0
    assert projections(w, proof)['coverage'] == 'TERMINAL_REASON_NOT_RECORDED'


@pytest.mark.parametrize('family', ['message_check', 'recovery_clarification'])
def test_actual_budget_refusal_never_claims_provider_dispatch_or_failure(bounded_world, family):
    w = bounded_world
    service, base, calls, path, req = consumer(w, family)
    service.budget.reserve = lambda: None
    status, prepared = service.handle(api_event(base, 'POST ' + path + '/prepare', req))
    proof = prepared['operationProof']
    status, response = service.handle(api_event(base, 'POST ' + path, req | {'operationProof': proof}))
    assert status == 200 and response['accounting']['chargedChecks'] == 0 and not calls
    assert projections(w, proof)['coverage'] == 'TERMINAL_REASON_NOT_RECORDED'


@pytest.mark.parametrize('family', FAMILIES)
@pytest.mark.parametrize('injection', ['raise', 'invalid-string', 'non-string', 'missing-export'])
def test_optional_classifier_failure_preserves_authoritative_settlement(bounded_world, monkeypatch, family, injection):
    import shared_operations
    def optional(*args, **kwargs):
        if injection == 'raise':
            raise RuntimeError('PRIVATE_CLASSIFIER_DETAIL')
        return 'PRIVATE_CLASSIFIER_DETAIL' if injection == 'invalid-string' else {'reason': 'PRIVATE_CLASSIFIER_DETAIL'}
    if injection == 'missing-export':
        monkeypatch.delattr(shared_operations, 'consumer_terminal_reason')
    else:
        monkeypatch.setattr(shared_operations, 'consumer_terminal_reason', optional)
    w = bounded_world
    *_, proof, response, calls = execute(w, family, failure)
    assert response['accounting']['chargedChecks'] == 0 and calls == [1]
    assert projections(w, proof)['coverage'] == 'TERMINAL_REASON_NOT_RECORDED'
    assert 'PRIVATE_CLASSIFIER_DETAIL' not in json.dumps(response) + str(w[3]('CHECK#' + proof))


@pytest.mark.parametrize('family', FAMILIES)
def test_default_off_skips_optional_classification_and_preserves_legacy_settlement(world, monkeypatch, family):
    import shared_operations
    def forbidden(*args, **kwargs):
        raise AssertionError('default-off must not invoke classification')
    monkeypatch.setattr(shared_operations, 'consumer_terminal_reason', forbidden)
    original = world[0].settle
    options = []
    def recording(*args, **kwargs):
        options.append(set(kwargs))
        return original(*args, **kwargs)
    monkeypatch.setattr(world[0], 'settle', recording)
    *_, proof, response, calls = execute(world, family, failure)
    assert response['accounting']['chargedChecks'] == 0 and calls == [1]
    assert options == [{'result_summary'}]
    row = world[3]('CHECK#' + proof)
    assert not {'operationsReceipt', 'operationsTerminalReasonCode', 'operationsProjection'} & set(row)


@pytest.mark.parametrize('codes', [None, [], ['BUDGET_LIMIT'], ['PRIVATE_CODE'],
    ['PROVIDER_UNAVAILABLE', 'BUDGET_LIMIT'], ['PROVIDER_UNAVAILABLE', 'PROVIDER_UNAVAILABLE'],
    'PROVIDER_UNAVAILABLE', {'PROVIDER_UNAVAILABLE': True}, [None], [['PROVIDER_UNAVAILABLE']]])
def test_ambiguous_codes_cannot_be_turned_into_provider_cause(codes):
    assert consumer_terminal_reason('unavailable', provider_codes=codes) is None


@pytest.mark.parametrize('family', ['message_check', 'recovery_clarification'])
def test_budget_reserve_exception_never_imputes_provider_cause(bounded_world, family):
    w = bounded_world
    service, base, calls, path, req = consumer(w, family)
    def broken_budget():
        raise RuntimeError('PRIVATE_BUDGET_DETAIL')
    service.budget.reserve = broken_budget
    _, prepared = service.handle(api_event(base, 'POST ' + path + '/prepare', req))
    proof = prepared['operationProof']
    status, response = service.handle(api_event(base, 'POST ' + path, req | {'operationProof': proof}))
    assert status == 200 and response['accounting']['chargedChecks'] == 0 and not calls
    assert projections(w, proof)['coverage'] == 'TERMINAL_REASON_NOT_RECORDED'
    assert 'PRIVATE_BUDGET_DETAIL' not in json.dumps(response)


@pytest.mark.parametrize('family', FAMILIES)
@pytest.mark.parametrize('wrong_reason', ['COVERAGE_PARTIAL', 'NONE'])
def test_wrong_listed_classifier_result_cannot_break_failed_settlement(bounded_world, monkeypatch, family, wrong_reason):
    import shared_operations
    monkeypatch.setattr(shared_operations, 'consumer_terminal_reason', lambda *a, **kw: wrong_reason)
    w = bounded_world
    *_, proof, response, calls = execute(w, family, failure)
    assert response['accounting']['chargedChecks'] == 0 and calls == [1]
    assert projections(w, proof)['coverage'] == 'TERMINAL_REASON_NOT_RECORDED'


@pytest.mark.parametrize('family', FAMILIES)
def test_wrong_listed_classifier_result_cannot_break_complete_charge(bounded_world, monkeypatch, family):
    import shared_operations
    monkeypatch.setattr(shared_operations, 'consumer_terminal_reason', lambda *a, **kw: 'PROVIDER_UNAVAILABLE')
    w = bounded_world
    service, base, calls, path, req = consumer(w, family)
    _, prepared = service.handle(api_event(base, 'POST ' + path + '/prepare', req))
    proof = prepared['operationProof']
    status, response = service.handle(api_event(base, 'POST ' + path, req | {'operationProof': proof}))
    assert status == 200 and response['state'] == 'settled' and len(calls) == 1
    # Message's default unsupported prose is inconclusive; others complete.
    expected = 0 if family == 'message_check' else 1
    assert response['accounting']['chargedChecks'] == expected
    assert projections(w, proof)['events'][1]['reasonCode'] == ('RESULT_INCONCLUSIVE' if not expected else 'NONE')
