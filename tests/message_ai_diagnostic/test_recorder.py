"""Isolated unit/functional regressions; never live provider acceptance."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from message_evaluator import ai_provider, proposer
from message_evaluator.usage import usage_report
from shared_message_contract.runtime import unique_pairs

SPEC = importlib.util.spec_from_file_location('diagnostic_recorder', ROOT / 'evaluation/message_ai/span_evidence/diagnostic_recorder.py')
r = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)
INTENT = {'entryPoint': 'message', 'language': 'en', 'target': {
    'entities': [], 'reviewedLinks': [],
    'sanitizedText': '🔐 Café notice: authorize the transfer today or your account will be frozen.',
    'scope': 'sanitized_message', 'sourceType': 'pasted_text', 'speakerRole': 'other', 'withheldLinks': False}}
KEY = 'unit-test-only-noncredential-marker'
CURRENT_INSTRUCTIONS = ai_provider.INSTRUCTIONS
CURRENT_SCHEMA = ai_provider.SCHEMA
CURRENT_FORMAT_NAME = ai_provider.FORMAT_NAME
HISTORICAL_REQUEST_BYTES = (Path(__file__).parent / 'fixtures/pr166-dev-019-en.request.json').read_bytes()
HISTORICAL_REQUEST = json.loads(HISTORICAL_REQUEST_BYTES, object_pairs_hook=unique_pairs)
HISTORICAL_INSTRUCTIONS = HISTORICAL_REQUEST['input'][0]['content'][0]['text']


@pytest.fixture(autouse=True)
def modules(monkeypatch):
    # This recorder's execution authority is deliberately frozen to PR166. Only
    # these isolated tests select its immutable historical request format.
    # Synthetic responses exercise the current strict parser, not a historical live run.
    monkeypatch.setattr(ai_provider, 'INSTRUCTIONS', HISTORICAL_INSTRUCTIONS)
    monkeypatch.setattr(ai_provider, 'SCHEMA', HISTORICAL_REQUEST['text']['format']['schema'])
    monkeypatch.setattr(ai_provider, 'FORMAT_NAME', HISTORICAL_REQUEST['text']['format']['name'])
    for name, value in [('ai_provider', ai_provider), ('proposer', proposer),
                        ('usage_report', usage_report), ('unique_pairs', unique_pairs)]:
        monkeypatch.setattr(r, name, value)


def case():
    return {'caseId': 'dev-019-en', 'intent': deepcopy(INTENT), 'requestBody': deepcopy(HISTORICAL_REQUEST),
            'requestBodySha256': r.REQUEST_SHA256}


def envelope(quotes):
    value = {'result': {'assessment': 'warning', 'context': 'clear', 'reasons': [
        {'code': 'AI_PAYMENT_PRESSURE', 'quotes': quotes, 'support': {'paymentRequest':0,'pressure':0}}]}}
    return {'model': r.MODEL, 'service_tier': 'default', 'status': 'completed',
            'usage': {'input_tokens': 1491, 'output_tokens': 206, 'total_tokens': 1697,
                      'input_tokens_details': {'cached_tokens': 1280}, 'output_tokens_details': {'reasoning_tokens': 103}},
            'output': [{'type': 'message', 'role': 'assistant', 'status': 'completed',
                        'content': [{'type': 'output_text', 'text': json.dumps(value)}]}]}


def deliver(monkeypatch, data):
    calls = []
    def transport(intent, settings, budget_ms, **kw):
        calls.append(1)
        assert kw['secret_loader']() == KEY
        assert kw['body_builder'](intent, settings) == case()['requestBody']
        return kw['response_parser'](json.dumps(data).encode(), intent['target']['sanitizedText'])
    monkeypatch.setattr(proposer, 'propose', transport)
    return calls


def test_exact_original_serialized_request():
    assert hashlib.sha256(HISTORICAL_REQUEST_BYTES).hexdigest() == r.REQUEST_SHA256
    assert hashlib.sha256(json.dumps(case()['requestBody'], ensure_ascii=False).encode()).hexdigest() == r.REQUEST_SHA256
    assert hashlib.sha256(HISTORICAL_INSTRUCTIONS.encode()).hexdigest() == 'b85d182c0a2a81327b0629a7f7fdbeedc36e5b963bdb496f90c735f2751035f6'
    body = ai_provider.request_body(INTENT, ai_provider.AISettings(r.MODEL, r.SECRET_ARN, 8000, 512, 'low'))
    assert body == HISTORICAL_REQUEST


def test_current_prompt_cannot_reuse_historical_recorder_authority(monkeypatch):
    monkeypatch.setattr(ai_provider, 'INSTRUCTIONS', CURRENT_INSTRUCTIONS)
    monkeypatch.setattr(ai_provider, 'SCHEMA', CURRENT_SCHEMA)
    monkeypatch.setattr(ai_provider, 'FORMAT_NAME', CURRENT_FORMAT_NAME)
    body = ai_provider.request_body(INTENT, ai_provider.AISettings(r.MODEL, r.SECRET_ARN, 8000, 512, 'low'))
    assert hashlib.sha256(json.dumps(body, ensure_ascii=False).encode()).hexdigest() != r.REQUEST_SHA256
    body['input'][0]['content'][0]['text'] = HISTORICAL_INSTRUCTIONS
    body['text']['format'].update(schema=HISTORICAL_REQUEST['text']['format']['schema'],
                                name=HISTORICAL_REQUEST['text']['format']['name'])
    assert body == HISTORICAL_REQUEST  # Only the private instructions/format changed.
    forbidden_calls = []
    def forbidden(*_, **__):
        forbidden_calls.append(True)
        pytest.fail('Historical request drift must stop before secret, network or parser work')
    real_transport = proposer.propose
    def guarded_transport(intent, settings, budget_ms, **kwargs):
        kwargs.update(secret_loader=forbidden, connection_factory=forbidden)
        return real_transport(intent, settings, budget_ms, **kwargs)
    monkeypatch.setattr(proposer, 'propose', guarded_transport)
    monkeypatch.setattr(ai_provider, 'parse', forbidden)
    value = case(); before = deepcopy(value)
    result = r.one(value, KEY)
    assert value == before and forbidden_calls == []
    assert result['failure'] == 'APPLICATION_ADAPTER_REJECTED_OR_TRANSPORT_UNCERTAIN'
    assert result['httpStatus'] is result['parserRejectionStage'] is result['usage'] is None
    assert not result['adapterValidated'] and result['assessment'] is None


@pytest.mark.parametrize('status', sorted(r.PROVIDER_STATUSES))
def test_finite_status_observation_does_not_change_strict_acceptance(monkeypatch, status):
    data = envelope(['today']); data['status'] = status
    deliver(monkeypatch, data)
    result = r.one(case(), KEY)
    assert result['providerStatus'] == status
    assert result['adapterValidated'] is (status == 'completed')
    assert result['parserRejectionStage'] == (None if status == 'completed' else 'ENVELOPE_STATUS')


@pytest.mark.parametrize('reason', sorted(r.INCOMPLETE_REASONS))
def test_incomplete_reason_is_allowlisted_and_never_accepts_partial_output(monkeypatch, reason):
    data = envelope(['today'])
    data.update(status='incomplete', incomplete_details={'reason': reason, 'private': 'DO_NOT_RETAIN'})
    data['usage'].update(output_tokens=512, total_tokens=2003, output_tokens_details={'reasoning_tokens': 512})
    deliver(monkeypatch, data)
    result = r.one(case(), KEY)
    assert result['providerStatus'] == 'incomplete' and result['providerIncompleteReason'] == reason
    assert result['providerIncompleteDetailsPresent'] is True and result['providerErrorPresent'] is False
    assert result['parserRejectionStage'] == 'ENVELOPE_STATUS'
    assert not result['adapterValidated'] and result['assessment'] is None
    assert 'DO_NOT_RETAIN' not in json.dumps(result)


def test_refusal_type_is_distinguished_without_retaining_refusal_text(monkeypatch):
    data = envelope(['today'])
    data['output'][0]['content'] = [{'type': 'refusal', 'refusal': 'PRIVATE_REFUSAL_BODY'}]
    deliver(monkeypatch, data)
    result = r.one(case(), KEY)
    assert result['providerStatus'] == 'completed' and result['providerRefusalTypePresent'] is True
    assert result['providerIncompleteReason'] is None and result['providerIncompleteDetailsPresent'] is False
    assert result['parserRejectionStage'] == 'CONTENT_SHAPE' and not result['adapterValidated']
    assert 'PRIVATE_REFUSAL_BODY' not in json.dumps(result)


def test_provider_error_presence_never_retains_error_payload(monkeypatch):
    data = envelope(['today'])
    data.update(status='failed', error={'code': 'PRIVATE_ERROR_CODE', 'message': 'PRIVATE_ERROR_BODY'})
    deliver(monkeypatch, data)
    result = r.one(case(), KEY)
    assert result['providerStatus'] == 'failed' and result['providerErrorPresent'] is True
    assert result['parserRejectionStage'] == 'ENVELOPE_STATUS' and not result['adapterValidated']
    assert 'PRIVATE_ERROR' not in json.dumps(result)


@pytest.mark.parametrize('value', [None, [], 'private', True, 42])
def test_non_object_metadata_is_unknown(value):
    assert all(v is None for v in r.envelope_metadata(value).values())


@pytest.mark.parametrize('status,reason', [('UNTRUSTED_STATUS', 'UNTRUSTED_REASON'), ([], []), (True, 2)])
def test_arbitrary_status_and_reason_are_never_retained(status, reason):
    result = r.envelope_metadata({'status': status, 'incomplete_details': {'reason': reason}})
    assert result['providerStatus'] is None and result['providerIncompleteReason'] is None
    assert result['providerIncompleteDetailsPresent'] is True
    assert 'UNTRUSTED' not in json.dumps(result)


def test_incomplete_reason_is_not_misattributed_to_completed_envelope(monkeypatch):
    data = envelope(['today'])
    data['incomplete_details'] = {'reason': 'max_output_tokens'}
    deliver(monkeypatch, data)
    result = r.one(case(), KEY)
    assert result['providerStatus'] == 'completed' and result['providerIncompleteDetailsPresent'] is True
    assert result['providerIncompleteReason'] is None
    assert result['parserRejectionStage'] == 'ENVELOPE_STATUS' and not result['adapterValidated']


@pytest.mark.parametrize('output', [None, [], [None], [{'type': 'reasoning'}],
    [{'type': 'message', 'role': 'assistant', 'content': []}],
    [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'PRIVATE_TYPE'}]}]])
def test_uninspectable_refusal_structure_is_unknown_not_false(output):
    assert r.envelope_metadata({'output': output})['providerRefusalTypePresent'] is None


@pytest.mark.parametrize('quotes,relation', [
    (['today', 'authorize the transfer'], 'REVERSED_DISJOINT'),
    (['today', 'today'], 'EXACT_DUPLICATE'),
    (['authorize the transfer today', 'today'], 'CURRENT_WITHIN_PREVIOUS'),
    (['today', 'authorize the transfer today'], 'CURRENT_CONTAINS_PREVIOUS'),
    (['transfer today', 'today or your account'], 'PARTIAL_OVERLAP'),
])
def test_real_parser_rejection_retains_only_finite_detail(monkeypatch, quotes, relation):
    calls = deliver(monkeypatch, envelope(quotes))
    result = r.one(case(), KEY)
    assert calls == [1]
    assert result['parserRejectionStage'] == 'SPAN_ORDER_OR_OVERLAP'
    assert result['parserRejectionDetail'] == {'reasonCode': 'AI_PAYMENT_PRESSURE', 'spanRelation': relation}
    assert result['diagnosticOutcome'] == 'NEW_SPAN_VIOLATION'
    assert result['assessment'] is None and not result['adapterValidated']
    assert not result['qualificationCriteriaMet']
    assert not {'outputText', 'raw', 'spans', 'quotes', 'responseId'} & set(result)
    assert KEY not in json.dumps(result)
    assert all(quote not in json.dumps(result) for quote in quotes)


def test_success_is_only_non_reproduction(monkeypatch):
    deliver(monkeypatch, envelope(['authorize the transfer today']))
    result = r.one(case(), KEY)
    assert result['adapterValidated'] and result['failure'] is None
    assert result['diagnosticOutcome'] == 'NON_REPRODUCTION_NOT_QUALIFICATION'
    assert result['parserRejectionDetail'] is None
    assert result['assessment']['reasons'][0]['spans'] == [{'start': 15, 'end': 43}]
    assert result['qualificationCriteriaMet'] is False


@pytest.mark.parametrize('change,reason', [
    ('absent', 'USAGE_ABSENT'), ('cost', 'COST_BOUND_EXCEEDED'), ('model', 'MODEL_MISMATCH')])
def test_unknown_or_excess_usage_blocks_parser(monkeypatch, change, reason):
    data = envelope(['today'])
    if change == 'absent': del data['usage']
    if change == 'cost':
        data['usage']['output_tokens'] = 513
        data['usage']['total_tokens'] = 2004
    if change == 'model': data['model'] = 'different-model'
    calls = deliver(monkeypatch, data)
    def forbidden(*args, **kwargs): pytest.fail('parser called with unqualified usage')
    monkeypatch.setattr(ai_provider, 'parse', forbidden)
    result = r.one(case(), KEY)
    assert calls == [1] and result['usage'] is None
    assert result['usageFailureReason'] == reason and result['parserRejectionStage'] is None
    assert all(result[k] is None for k in r.envelope_metadata(None))


@pytest.mark.parametrize('escaped', [False, True])
def test_key_echo_never_retained(monkeypatch, escaped):
    data = envelope(['today'])
    data['output'][0]['content'][0]['text'] = KEY if not escaped else ''.join('\\u%04x' % ord(c) for c in KEY)
    deliver(monkeypatch, data)
    result = r.one(case(), KEY)
    assert KEY not in json.dumps(result) and result['assessment'] is None
    assert not result['adapterValidated']
    if not escaped:
        assert all(result[k] is None for k in r.envelope_metadata(None))
    else:
        # Literal backslash-u text is not the key itself. Only finite envelope
        # metadata survives; this malformed assessment still rejects as before.
        assert result['providerStatus'] == 'completed'
        assert result['parserRejectionStage'] == 'ASSESSMENT_JSON'


def test_fabricated_or_ambiguous_evidence_not_repaired(monkeypatch):
    deliver(monkeypatch, envelope(['invented text']))
    result = r.one(case(), KEY)
    assert result['parserRejectionStage'] == 'EVIDENCE_NOT_FOUND'
    assert result['parserRejectionDetail'] is None and result['assessment'] is None


def test_even_a_credential_collision_with_a_closed_enum_cannot_be_saved(monkeypatch):
    def failed(*args, **kwargs): raise ValueError('not retained')
    monkeypatch.setattr(proposer, 'propose', failed)
    with pytest.raises(ValueError, match='UNSAFE_RESULT'):
        r.one(case(), 'RESPONSE_UNAVAILABLE')


@pytest.mark.parametrize('detail', [
    {'reasonCode': 'AI_PRETEXT', 'spanRelation': 'invented'},
    {'reasonCode': 'AI_PRETEXT', 'spanRelation': 'EXACT_DUPLICATE', 'quote': 'private'},
    {'reasonCode': 'private', 'spanRelation': 'EXACT_DUPLICATE'}])
def test_recorder_checks_callback_allowlist_independently(monkeypatch, detail):
    deliver(monkeypatch, envelope(['today']))
    def invalid(*args, **kw):
        kw['on_rejection']('SPAN_ORDER_OR_OVERLAP')
        kw['on_rejection_detail'](detail)
        raise ValueError('private exception')
    monkeypatch.setattr(ai_provider, 'parse', invalid)
    result = r.one(case(), KEY)
    assert result['parserRejectionDetail'] is None
    assert result['diagnosticOutcome'] == 'DIAGNOSTIC_INCOMPLETE'
    assert 'private' not in json.dumps(result)


def make_plan(tmp_path):
    tmp_path.chmod(0o700)
    body = tmp_path / 'request-body.json'
    body.write_bytes(json.dumps(case()['requestBody'], ensure_ascii=False).encode())
    body.chmod(0o600)
    plan = {'packetIdentity': r.PACKET_ID, 'model': r.MODEL, 'generationAttemptsMaximum': 1,
            'automaticRetries': 0, 'countRequests': 0, 'diagnosticOnly': True, 'fullQualificationMayPass': False,
            'proposedSupervisedAttemptWallLimitMs': 18000, 'proposedBatchWallLimitMs': 300000,
            'inferenceProfile': {'maxOutputTokens': 512, 'reasoningEffort': 'low'},
            'generationCases': [case()], 'backendMain': 'base', 'backendRevision': 'reviewed', 'sourceTree': 'tree',
            'budgetProposal': {'reservationNanoUsd': r.RESERVE, 'hardCapNanoUsd': r.TOTAL_CAP}}
    allocation = {'allocationId': r.PACKET_ID, 'allocationState': 'PREALLOCATED_PENDING_SEPARATE_KEY_USE_APPROVAL',
                  'backendMain': 'base', 'sourceRevision': 'reviewed', 'actualGenerationIntents': 0,
                  'generationAttemptCap': 1, 'amountHeldNanoUsd': r.RESERVE}
    ledger = {'ledgerId': 'unit-ledger', 'currency': 'USD', 'ceilingNanoUsd': 50000000000,
              'allocations': [allocation], 'totalHeldNanoUsd': r.RESERVE,
              'remainingUnallocatedNanoUsd': 50000000000 - r.RESERVE, 'priorReservationReleases': 0}
    ledger_path = tmp_path / 'ledger.json'
    r.save(ledger_path, ledger)
    plan['cumulativeBudget'] = {'ledgerPath': str(ledger_path), 'ledgerSha256': r.sha(ledger_path),
        'ledgerId': 'unit-ledger', 'sharedCeilingNanoUsd': 50000000000,
        'thisAllocationId': r.PACKET_ID, 'thisBatchReservationNanoUsd': r.RESERVE}
    path = tmp_path / 'plan.json'
    r.save(path, plan)
    return path, plan


def scope_value(path):
    return {'planSha256': r.sha(path), 'runnerSha256': r.sha(r.__file__),
            'secretArn': r.SECRET_ARN, 'secretVersion': r.SECRET_VERSION, 'secretUseApproved': True,
            'generationAttemptCap': 1, 'budgetNanoUsd': r.TOTAL_CAP, 'authorityDirectory': str(r.AUTHORITY),
            'metadataAndOfficialPriceRequalified': True, 'sourceAndRecorderReviewApproved': True,
            'reservationNanoUsd': r.RESERVE, 'approvedAtUtc': r.utc(), 'prerequisitesCheckedAtUtc': r.utc()}


@pytest.mark.parametrize('change', ['missing', 'false', 'stale', 'hash', 'missing-allocation'])
def test_invalid_approval_cannot_reach_worker_or_key(tmp_path, monkeypatch, change):
    path, plan = make_plan(tmp_path)
    authority = tmp_path / 'attempt'
    monkeypatch.setattr(r, 'AUTHORITY', authority)
    monkeypatch.setattr(r, 'bind_application', lambda *_: None)
    def forbidden(*args, **kwargs): pytest.fail('execution reached without approval')
    monkeypatch.setattr(r, 'supervise', forbidden)
    monkeypatch.setattr(r, 'read_approved_key', forbidden)
    scope = scope_value(path)
    if change == 'false': scope['secretUseApproved'] = False
    if change == 'stale': scope['approvedAtUtc'] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    if change == 'hash': scope['planSha256'] = 'wrong'
    if change == 'missing-allocation':
        plan['cumulativeBudget']['thisAllocationId'] = 'absent'
        path.unlink(); r.save(path, plan); scope['planSha256'] = r.sha(path)
    scope_path = tmp_path / 'scope.json'
    if change != 'missing': r.save(scope_path, scope)
    with pytest.raises((ValueError, FileNotFoundError)):
        r.execute(path, scope_path, ROOT)
    assert not authority.exists()


def test_exclusive_fence_survives_failed_execution(tmp_path, monkeypatch):
    path, plan = make_plan(tmp_path)
    monkeypatch.setattr(r, 'AUTHORITY', tmp_path / 'attempt')
    monkeypatch.setattr(r, 'bind_application', lambda *_: None)
    scope_path = tmp_path / 'scope.json'; r.save(scope_path, scope_value(path))
    calls = []
    def stopped(*args): calls.append(1); return 'WORKER_FAILED_NO_RELAUNCH'
    monkeypatch.setattr(r, 'supervise', stopped)
    result = r.execute(path, scope_path, ROOT)
    assert result['qualificationCriteriaMet'] is False and result['reservationReleased'] is False
    with pytest.raises(FileExistsError): r.execute(path, scope_path, ROOT)
    assert calls == [1]
    assert r.private(r.AUTHORITY, directory=True)


@pytest.mark.parametrize('change', ['second-case', 'body', 'wrong-mode', 'duplicate-key'])
def test_plan_drift_stops_offline(tmp_path, change):
    path, plan = make_plan(tmp_path)
    if change == 'second-case': plan['generationCases'].append(case())
    if change == 'body': (tmp_path / 'request-body.json').write_bytes(b'{}')
    if change == 'wrong-mode': path.chmod(0o644)
    if change == 'duplicate-key': path.write_text('{"x":1,"x":2}')
    if change == 'second-case': path.unlink(); r.save(path, plan)
    with pytest.raises(ValueError): r.validate_plan(path)


def stall(channel, plan, directory):
    r.save(directory / 'dev-019-en-intent.json', {'unitTestOnly': True})
    channel.send_bytes(b'START')
    assert channel.recv_bytes(8) == b'GO'
    time.sleep(3)
    r.save(directory / 'unexpected-late-write.json', {})


def second_start(channel, plan, directory):
    r.save(directory / 'dev-019-en-intent.json', {'unitTestOnly': True})
    channel.send_bytes(b'START'); channel.recv_bytes(8)
    r.save(directory / 'dev-019-en-result.json', {})
    channel.send_bytes(b'DONE'); channel.recv_bytes(8)
    channel.send_bytes(b'START'); channel.recv_bytes(8)


@pytest.mark.parametrize('target,outcome', [(stall, 'ATTEMPT_WALL_TIMEOUT'),
    (second_start, 'SUPERVISOR_INTERRUPTED_OR_PROTOCOL_FAILURE')])
def test_supervisor_kills_or_rejects_without_second_dispatch(tmp_path, target, outcome):
    assert r.supervise({'generationCases': [case()]}, tmp_path, worker_target=target,
                       attempt_seconds=.15, batch_seconds=2) == outcome
    assert not (tmp_path / 'unexpected-late-write.json').exists()
