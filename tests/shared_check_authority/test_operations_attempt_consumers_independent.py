"""Independent local timing/owner/ticket counterexamples; no external calls."""
from copy import deepcopy
from dataclasses import replace
import importlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_transactions import world, WORKER, ACCOUNT
from test_operations_callers import bounded_world, consumer, api_event
from test_operations_attempt_consumers import setup_flow, attempts, ticket_case
from shared_check_authority.operations_ingestion import ReceiptIngestion
import shared_check_authority.operations_callers as callers

FAMILIES = [('url_check', 'url_consumer.service'),
            ('message_check', 'message_consumer.service'),
            ('recovery_clarification', 'recovery_consumer.service')]


@pytest.mark.parametrize('family,module', FAMILIES)
def test_actual_invocation_duration_excludes_post_start_pre_dispatch_work(bounded_world, monkeypatch, family, module):
    w = bounded_world
    ticks = [0.0]
    service, base, calls, path, req, proof = setup_flow(w, family, clock=lambda: ticks[0])
    implementation = importlib.import_module(module)
    original_begin = implementation._begin_attempt
    def preparation(*args, **kwargs):
        result = original_begin(*args, **kwargs)
        ticks[0] += .75  # Work after durable start, before actual invocation.
        return result
    monkeypatch.setattr(implementation, '_begin_attempt', preparation)
    original_provider = service.provider
    def provider(body):
        ticks[0] += .1
        return original_provider(body)
    service.provider = provider
    status, response = service.handle(api_event(base, 'POST ' + path,
                                               req | {'operationProof': proof}))
    assert status == 200 and response['state'] == 'settled' and len(calls) == 1
    recorded = attempts(w, proof)
    assert len(recorded) == 2 and recorded[1]['durationBucket'] == 'lt_250ms'


def test_rules_only_cannot_dispatch_after_enabled_optional_work_exhausts_budget(bounded_world, monkeypatch):
    from message_consumer.service import Consumer
    from message_evaluator.policy_v3 import evaluate
    from shared_message_contract import validation_v3 as v3
    w = bounded_world
    ticks = [0.0]
    _, base, _, path, req = consumer(w, 'message_check')
    req['transportVersion'] = v3.VERSION
    calls = []
    class Budget:
        def reserve(self):
            pytest.fail('rules-only invoked vendor budget')
        def failed(self, _):
            pytest.fail('rules-only reported vendor budget failure')
    def provider(body):
        calls.append(1)
        return evaluate(body['checkId'], body['intent'], rules_only=True,
                        lookup=lambda *_: pytest.fail('lookup'), ai=lambda *_: pytest.fail('AI'))
    service = Consumer(w[0], provider, lambda _: None, Budget(),
                       allow_candidate3_rules_only=True, clock=lambda: ticks[0],
                       remaining_ms=lambda: 29000)
    status, prepared = service.handle(api_event(base, 'POST ' + path + '/prepare', req))
    assert status == 200 and prepared['state'] == 'prepared'
    proof = prepared['operationProof']
    original = ReceiptIngestion.start_attempt
    def delayed(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        ticks[0] = 25.0
        return result
    monkeypatch.setattr(ReceiptIngestion, 'start_attempt', delayed)
    status, response = service.handle(api_event(base, 'POST ' + path,
                                               req | {'operationProof': proof}))
    assert status == 200 and not calls and response['accounting']['chargedChecks'] == 0
    recorded = attempts(w, proof)
    assert len(recorded) == 1 and recorded[0]['eventType'] == 'attempt_started'


def _begun(w, **changes):
    from test_operations_callers import pending
    proof, op, event = pending(w)
    event['routeKey'] = 'POST /v1/url-checks'
    arguments = dict(family='url_check', client_check_id='bound-client',
                     transport_version='1.0.0-candidate.2', scope=object(),
                     request_started=0.0, provider_budget_ms=18000,
                     remaining_ms=lambda: 29000, clock=lambda: 0.0)
    arguments.update(changes)
    token = arguments.pop('execution_token', op['executionToken'])
    return proof, op, event, arguments, token


@pytest.mark.parametrize('case', ['client', 'version', 'family', 'scope', 'remaining',
                                  'sdk', 'clock-back', 'execution'])
def test_unqualified_begin_never_mutates_source_or_mints_attempt(bounded_world, case):
    w = bounded_world
    changes = {}
    if case == 'client': changes['client_check_id'] = 'different-client'
    elif case == 'version': changes['transport_version'] = '1.0.0-candidate.1'
    elif case == 'family': changes['family'] = 'message_check'
    elif case == 'scope': changes['scope'] = None
    elif case == 'remaining': changes['remaining_ms'] = lambda: 23999
    elif case == 'clock-back':
        values = iter([.1, .05])
        changes['clock'] = lambda: next(values)
    elif case == 'execution': changes['execution_token'] = 'f' * 32
    proof, op, event, arguments, token = _begun(w, **changes)
    if case == 'sdk':
        w[0].client.meta.config.read_timeout = 60
    before = deepcopy(w[3]('CHECK#' + proof))
    assert callers.begin_authenticated_attempt(w[0], event, proof, token, **arguments) is None
    assert w[3]('CHECK#' + proof) == before
    assert 'operationsProjection' not in before


def test_completed_ticket_has_one_atomic_consumer_across_threads(bounded_world):
    from concurrent.futures import ThreadPoolExecutor
    _, _, _, _, ticket = ticket_case(bounded_world)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: ticket.claim.consume(ticket), range(16)))
    assert results.count(True) == 1 and results.count(False) == 15


def test_tampered_ticket_does_not_consume_genuine_in_request_capability(bounded_world):
    w = bounded_world
    proof, op, event, scope, ticket = ticket_case(w)
    fake = replace(ticket, reference='at1_' + 'f' * 32)
    def capture(value):
        return callers.capture_authenticated(w[0], event, proof, family='url_check',
            client_check_id='bound-client', transport_version='1.0.0-candidate.2',
            remaining_ms=lambda: 29000, clock=lambda: 0.0,
            attempt=value, attempt_scope=scope)
    assert capture(fake) == 'REVALIDATED'
    assert len(attempts(w, proof)) == 1
    assert capture(ticket) == 'CAPTURED' and len(attempts(w, proof)) == 2


@pytest.mark.parametrize('boundary', ['enter', 'complete'])
def test_bad_measurement_clock_cannot_be_rearmed_to_forge_finish(bounded_world, boundary):
    w = bounded_world
    proof, _, event, arguments, token = _begun(w)
    ticket = callers.begin_authenticated_attempt(w[0], event, proof, token, **arguments)
    assert ticket is not None
    if boundary == 'enter':
        assert callers.enter_internal_attempt(ticket, clock=lambda: float('nan')) is None
        assert callers.enter_internal_attempt(ticket, clock=lambda: .1) is None
        assert callers.complete_internal_attempt(ticket, succeeded=True, clock=lambda: .2) is None
    else:
        entered = callers.enter_internal_attempt(ticket, clock=lambda: .1)
        assert entered is not None
        assert callers.complete_internal_attempt(entered, succeeded=True, clock=lambda: .05) is None
        assert callers.complete_internal_attempt(entered, succeeded=True, clock=lambda: .2) is None
    assert len(attempts(w, proof)) == 1
