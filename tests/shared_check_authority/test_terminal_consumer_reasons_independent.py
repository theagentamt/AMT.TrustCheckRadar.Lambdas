"""Independent telemetry containment and zero-provider budget counterexamples; local Moto only."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('isolated local Moto only', allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tests/shared_check_authority'))
from test_transactions import world
from test_operations_producer import enable
from message_consumer.service import Consumer as Message
from recovery_consumer.service import Consumer as Recovery
from url_consumer.service import Consumer as Url
import shared_operations


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


message = load('independent_message_fixture', 'tests/message_consumer/test_integration.py')
recovery = load('independent_recovery_fixture', 'tests/recovery_runtime/test_integration.py')


@pytest.mark.parametrize('kind', ['message', 'recovery'])
def test_local_budget_exception_before_provider_does_not_assert_provider_cause(world, kind):
    enable(world[0])
    if kind == 'message':
        service, base, calls, budget = message.system(world)
        proof = message.prepare(service, base)
        request = message.request() | {'operationProof': proof}
        dispatch = lambda: service.handle(message.event(base, 'POST /v1/message-checks', request))
    else:
        service, base, calls, budget = recovery.system(world)
        proof = recovery.prepare(service, base)
        request = recovery.request() | {'operationProof': proof}
        dispatch = lambda: service.handle(recovery.event(base, '', request))
    def failed_reservation():
        raise RuntimeError('synthetic-local-budget-store-failure')
    budget.reserve = failed_reservation
    status, result = dispatch()
    assert status == 200 and result['state'] == 'settled'
    assert result['accounting']['chargedChecks'] == 0 and not calls
    row = world[3]('CHECK#' + proof)
    # Preserve the legacy public outcome, but do not turn it into provider facts.
    assert result['outcome']['processingOutcome'] == 'unavailable'
    assert row.get('operationsTerminalReasonCode') not in {
        'PROVIDER_UNAVAILABLE', 'PROVIDER_RESPONSE_INVALID'}
    assert dispatch()[1]['accounting'] == result['accounting'] and not calls
    assert world[3]('PERIOD#p1')['usedChecks'] == world[3]('PERIOD#p1')['reservedChecks'] == 0


@pytest.mark.parametrize('consumer', [Url, Message, Recovery])
@pytest.mark.parametrize('outcome,wrong', [
    ('complete', 'PROVIDER_UNAVAILABLE'),
    ('partial', 'SETTLED_FAILED'),
    ('failed', 'COVERAGE_PARTIAL'),
    ('unavailable', 'SETTLED_FAILED'),
])
def test_optional_classifier_wrong_but_listed_code_cannot_reach_settlement(consumer, outcome, wrong, monkeypatch):
    service = consumer.__new__(consumer)
    service.a = SimpleNamespace(s=SimpleNamespace(operations_producer_enabled=True))
    monkeypatch.setattr(shared_operations, 'consumer_terminal_reason', lambda *a, **k: wrong)
    summary = {'synthetic': 'same-object'}
    options = service._settlement_options(outcome, summary, ['PROVIDER_UNAVAILABLE'])
    assert options == {'result_summary': summary}
    assert options['result_summary'] is summary


@pytest.mark.parametrize('consumer', [Url, Message, Recovery])
def test_optional_classifier_exception_or_non_code_leaves_legacy_settlement_arguments(consumer, monkeypatch):
    service = consumer.__new__(consumer)
    service.a = SimpleNamespace(s=SimpleNamespace(operations_producer_enabled=True))
    summary = {'synthetic': 'same-object'}
    def broken(*a, **k):
        raise RuntimeError('synthetic-private-detail-not-logged')
    for helper in [broken, lambda *a, **k: {'unsafe': 'not-code'}, lambda *a, **k: 'RAW_UNAPPROVED_CODE']:
        monkeypatch.setattr(shared_operations, 'consumer_terminal_reason', helper)
        assert service._settlement_options('partial', summary) == {'result_summary': summary}


@pytest.mark.parametrize('consumer', [Url, Message, Recovery])
def test_missing_or_non_boolean_true_flag_never_invokes_classifier(consumer, monkeypatch):
    def forbidden(*a, **k):
        pytest.fail('optional classifier invoked while disabled')
    monkeypatch.setattr(shared_operations, 'consumer_terminal_reason', forbidden)
    for settings in [None, SimpleNamespace(), SimpleNamespace(operations_producer_enabled=False),
                     SimpleNamespace(operations_producer_enabled='true'), SimpleNamespace(operations_producer_enabled=1)]:
        service = consumer.__new__(consumer)
        service.a = SimpleNamespace(s=settings)
        assert service._settlement_options('partial', None) == {'result_summary': None}


@pytest.mark.parametrize('consumer', [Url, Message, Recovery])
@pytest.mark.parametrize('codes', [None, [], ['BUDGET_LIMIT'], ['PROVIDER_RESPONSE_INVALID'],
                                  ['PROVIDER_UNAVAILABLE', 'BUDGET_LIMIT']])
def test_even_compatible_helper_code_requires_exact_singleton_source_fact(consumer, codes, monkeypatch):
    service = consumer.__new__(consumer)
    service.a = SimpleNamespace(s=SimpleNamespace(operations_producer_enabled=True))
    monkeypatch.setattr(shared_operations, 'consumer_terminal_reason',
                        lambda *a, **k: 'PROVIDER_UNAVAILABLE')
    assert service._settlement_options('unavailable', None, codes) == {'result_summary': None}
