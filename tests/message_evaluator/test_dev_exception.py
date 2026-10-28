"""Dev approval boundaries with local callbacks only; no provider acceptance."""
import copy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from message_evaluator import ai_provider as ai, app
from shared_message_contract.validation import MessageError
from .test_normal_usage import INTENT, TEXT, response
from .test_reasoning_profile import COST

ROOT = Path(__file__).resolve().parents[2]
IDENTIFIER = 'atcr187-known-bug-dev-20261028'


@pytest.fixture
def exception(monkeypatch, tmp_path):
    source = ROOT / 'src/message_evaluator'
    for name in ('ai_dev_exceptions.json', 'ai_qualifications.json'):
        shutil.copyfile(source / name, tmp_path / name)
    shutil.copytree(source / 'dev_exceptions', tmp_path / 'dev_exceptions')
    shutil.copytree(ROOT / 'contracts/message-consumer/1.0.0-candidate.2', tmp_path / 'ai_contract')
    monkeypatch.setattr(ai, '__file__', str(tmp_path / 'ai_provider.py'))
    values = {'STAGE': 'dev', 'MESSAGE_AI_ENABLED': 'true', 'MESSAGE_AI_QUALIFIED': 'false',
              'MESSAGE_AI_QUALIFICATION_ID': '', 'MESSAGE_AI_DEV_EXCEPTION_ENABLED': 'true',
              'MESSAGE_AI_DEV_EXCEPTION_ID': IDENTIFIER, 'MESSAGE_AI_REASONING_EFFORT': 'low',
              'MESSAGE_AI_POLICY_VERSION': ai.POLICY, 'MESSAGE_AI_POLICY_APPROVAL_SHA256': ai.APPROVAL_SHA,
              'MESSAGE_PROPOSER_MODEL': COST.model, 'MESSAGE_PROPOSER_TIMEOUT_MS': '8000',
              'MESSAGE_PROPOSER_MAX_OUTPUT_TOKENS': '512',
              'MESSAGE_PROPOSER_SECRET_ARN': 'arn:aws:secretsmanager:us-east-1:107827791950:secret:trustcheckradar/dev/openai-ABC123'}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(ai.proposer, 'propose', lambda *a, **k: pytest.fail('unexpected transport'))
    return tmp_path


def test_explicit_dev_exception_loads_without_a_passed_qualification(exception):
    settings = ai.qualified_settings(COST)
    assert settings.model == COST.model and settings.max_output_tokens == 512
    assert settings.timeout_ms == 8000 and settings.reasoning_effort == 'low'
    assert json.loads((exception / 'ai_qualifications.json').read_text()) == {}
    record = json.loads((exception / 'ai_dev_exceptions.json').read_text())[IDENTIFIER]
    evidence = json.loads((exception / 'dev_exceptions' / (IDENTIFIER + '.json')).read_text())
    assert record['status'] == evidence['decision'] == 'accepted_known_bug_dev_only'
    assert record['qualificationPassed'] is evidence['qualificationPassed'] is False
    assert evidence['qualification']['outcome'] == 'NO_GO'
    assert evidence['qualification']['realCalls'] == 2 and evidence['qualification']['unrun'] == 12
    assert evidence['productionPublicReleaseAllowed'] is False


@pytest.mark.parametrize('stage', ['', 'uat', 'staging', 'production', 'prod', 'Dev'])
def test_exception_never_selects_outside_exact_dev(exception, monkeypatch, stage):
    monkeypatch.setenv('STAGE', stage)
    assert not ai.approval_selected()
    with pytest.raises(MessageError):
        ai.assess(INTENT, 1000, cost_policy=COST)


@pytest.mark.parametrize('key,value', [
    ('MESSAGE_AI_DEV_EXCEPTION_ENABLED', 'false'), ('MESSAGE_AI_DEV_EXCEPTION_ENABLED', 'TRUE'),
    ('MESSAGE_AI_DEV_EXCEPTION_ID', ''), ('MESSAGE_AI_DEV_EXCEPTION_ID', '../escape'),
    ('MESSAGE_AI_DEV_EXCEPTION_ID', 'unknown'), ('MESSAGE_AI_QUALIFIED', 'true'),
    ('MESSAGE_AI_QUALIFIED', ''), ('MESSAGE_AI_QUALIFICATION_ID', 'some-qualification'),
    ('MESSAGE_AI_ENABLED', 'false'), ('MESSAGE_AI_POLICY_VERSION', 'other'),
    ('MESSAGE_AI_POLICY_APPROVAL_SHA256', '0' * 64),
    ('MESSAGE_PROPOSER_TIMEOUT_MS', '7999'), ('MESSAGE_PROPOSER_TIMEOUT_MS', '8001'),
    ('MESSAGE_PROPOSER_MAX_OUTPUT_TOKENS', '2048'), ('MESSAGE_PROPOSER_MAX_OUTPUT_TOKENS', '256'),
    ('MESSAGE_AI_REASONING_EFFORT', ''), ('MESSAGE_AI_REASONING_EFFORT', 'high'),
    ('MESSAGE_PROPOSER_MODEL', 'gpt-5.4-mini')])
def test_environment_drift_fails_before_transport(exception, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(MessageError):
        ai.assess(INTENT, 1000, cost_policy=COST)


@pytest.mark.parametrize('changes', [
    {'stage': 'prod'}, {'qualificationPassed': True}, {'qualificationPassed': 0},
    {'status': 'approved'}, {'status': 'NO_GO'}, {'networkTimeoutMs': 7999},
    {'networkTimeoutMs': 8000.0}, {'model': 'other-model-2026-01-01'},
    {'policyVersion': 'other'}, {'promptSha256': '0' * 64}, {'schemaSha256': '0' * 64},
    {'contractSha256': '0' * 64}, {'evidenceSha256': '0' * 64},
    {'inferenceProfile': {'reasoningEffort': 'high', 'maxOutputTokens': 512}},
    {'costPolicy': COST.as_dict() | {'inputNanoUsdPerToken': 1}},
    {'costPolicy': COST.as_dict() | {'maxOutputTokens': 256}}, {'extra': True}])
def test_record_drift_fails_before_transport(exception, changes):
    path = exception / 'ai_dev_exceptions.json'
    records = json.loads(path.read_text())
    records[IDENTIFIER].update(changes)
    path.write_text(json.dumps(records))
    with pytest.raises(MessageError):
        ai.assess(INTENT, 1000, cost_policy=COST)


@pytest.mark.parametrize('field', ['costPolicy', 'stage', 'qualificationPassed', 'networkTimeoutMs'])
def test_required_exception_fields_cannot_be_omitted(exception, field):
    path = exception / 'ai_dev_exceptions.json'
    records = json.loads(path.read_text())
    del records[IDENTIFIER][field]
    path.write_text(json.dumps(records))
    with pytest.raises(MessageError):
        ai.assess(INTENT, 1000, cost_policy=COST)


def test_evidence_tampering_fails_before_transport(exception):
    (exception / 'dev_exceptions' / (IDENTIFIER + '.json')).write_text('{"qualificationPassed":true}')
    with pytest.raises(MessageError):
        ai.assess(INTENT, 1000, cost_policy=COST)


def test_exception_cannot_be_reused_as_a_qualification(exception, monkeypatch):
    monkeypatch.setenv('MESSAGE_AI_DEV_EXCEPTION_ENABLED', 'false')
    monkeypatch.setenv('MESSAGE_AI_DEV_EXCEPTION_ID', '')
    monkeypatch.setenv('MESSAGE_AI_QUALIFIED', 'true')
    monkeypatch.setenv('MESSAGE_AI_QUALIFICATION_ID', IDENTIFIER)
    assert ai.approval_selected()
    with pytest.raises(MessageError):
        ai.assess(INTENT, 1000, cost_policy=COST)


def primary_event():
    return {'schemaVersion': 3, 'checkId': 'check', 'policyVersion': ai.POLICY,
            'intent': copy.deepcopy(INTENT), 'executionBudgetMs': 1000,
            'runtimeMode': 'ai_primary_v1', 'providerCost': COST.as_dict()}


def primary_env(monkeypatch):
    for key, value in {'MESSAGE_EVALUATOR_ENABLED': 'true', 'MESSAGE_AI_PRIMARY_ENABLED': 'true',
                       'MESSAGE_AI_COST_POLICY': json.dumps(COST.as_dict())}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(app, 'lookup', lambda *_: pytest.fail('unexpected URL lookup'))


@pytest.mark.parametrize('stage', ['uat', 'production', 'prod'])
def test_primary_application_remains_disabled_outside_dev(exception, monkeypatch, stage):
    primary_env(monkeypatch)
    monkeypatch.setenv('STAGE', stage)
    monkeypatch.setattr(ai, 'assess', lambda *a, **k: pytest.fail('non-Dev AI boundary'))
    assert app.lambda_handler(primary_event(), SimpleNamespace(get_remaining_time_in_millis=lambda: 5000)) == {'enabled': False}


def test_dev_primary_uses_normal_usage_and_strict_parser_without_qualifying(exception, monkeypatch, capsys):
    primary_env(monkeypatch)
    calls = []
    raw = response() | {'model': COST.model}
    def transport(intent, settings, budget_ms, **kwargs):
        body = kwargs['body_builder'](intent, settings)
        assert body['tools'] == [] and body['store'] is False and body['max_output_tokens'] == 512
        assert body['reasoning'] == {'effort': 'low'}
        calls.append(body)
        return kwargs['response_parser'](json.dumps(raw).encode(), TEXT)
    monkeypatch.setattr(ai.proposer, 'propose', transport)
    result = app.lambda_handler(primary_event(), SimpleNamespace(get_remaining_time_in_millis=lambda: 5000))
    assert result['processingOutcome'] == 'complete' and result['aiAssessmentStatus'] == 'no_warning'
    assert len(calls) == 1 and 'message_ai_usage' in capsys.readouterr().out
    assert json.loads((exception / 'ai_qualifications.json').read_text()) == {}


def test_dev_exception_does_not_bypass_cost_mismatch(exception, monkeypatch):
    primary_env(monkeypatch)
    event = primary_event()
    event['providerCost']['inputNanoUsdPerToken'] = 1
    assert app.lambda_handler(event, SimpleNamespace(get_remaining_time_in_millis=lambda: 5000)) == {
        'enabled': True, 'errorCode': 'EVALUATOR_UNAVAILABLE'}


@pytest.mark.parametrize('kind', ['missing_usage', 'refusal', 'ungrounded_quote', 'overlapping_quotes'])
def test_exception_preserves_usage_refusal_and_grounding_rejection(exception, monkeypatch, capsys, kind):
    raw = response() | {'model': COST.model}
    if kind == 'missing_usage':
        del raw['usage']
    elif kind == 'refusal':
        raw['output'][0]['content'] = [{'type': 'refusal', 'refusal': 'offline fixture'}]
    else:
        quotes = ['Absent from this message.'] if kind == 'ungrounded_quote' else ['A gift card', 'gift card']
        result = {'assessment': 'warning', 'context': 'clear', 'reasons': [{
            'code': 'AI_PRETEXT', 'quotes': quotes,
            'support': {'claimOrSituation': 0, 'consequentialConnection': len(quotes) - 1}}]}
        raw['output'][0]['content'][0]['text'] = json.dumps({'result': result})
    def transport(intent, settings, budget_ms, **kwargs):
        return kwargs['response_parser'](json.dumps(raw).encode(), TEXT)
    monkeypatch.setattr(ai.proposer, 'propose', transport)
    with pytest.raises(MessageError):
        ai.assess(INTENT, 1000, cost_policy=COST)
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    if kind == 'missing_usage':
        assert events[0]['usage'] is None and events[0]['failureReason'] == 'USAGE_ABSENT'
    else:
        assert events[0]['usage'] is not None
        expected = {'refusal': 'CONTENT_SHAPE', 'ungrounded_quote': 'EVIDENCE_NOT_FOUND',
                    'overlapping_quotes': 'SPAN_ORDER_OR_OVERLAP'}[kind]
        assert events[1] == {'event': 'message_ai_rejection', 'stage': expected}


def test_exception_cannot_bypass_consumer_authentication(exception, monkeypatch, capsys):
    from message_consumer import app as consumer
    from shared_check_authority import runtime
    from shared_message_contract import POLICY, APPROVAL_SHA
    for key, value in {'MESSAGE_CONSUMER_ENABLED': 'true', 'AUTHORITY_ENABLED': 'true',
                       'MESSAGE_POLICY_VERSION': POLICY, 'MESSAGE_POLICY_APPROVAL_SHA256': APPROVAL_SHA,
                       'DEV_SUBJECT_ALLOWLIST_JSON': '["00000000-0000-0000-0000-000000000001"]',
                       'COGNITO_ISSUER': 'https://cognito-idp.us-east-1.amazonaws.com/offline',
                       'COGNITO_APP_CLIENT_ID': 'offline',
                       'COGNITO_REQUIRED_SCOPE': 'aws.cognito.signin.user.admin'}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(runtime, 'load_authority', lambda: pytest.fail('unauthorized authority access'))
    monkeypatch.setattr(consumer, 'provider', lambda *_: pytest.fail('unauthorized provider access'))
    event = {'body': json.dumps({'MESSAGE_AI_DEV_EXCEPTION_ENABLED': 'true'})}
    result = consumer.lambda_handler(event, SimpleNamespace(get_remaining_time_in_millis=lambda: 5000))
    body = json.loads(result['body'])
    assert result['statusCode'] == 503 and body['outcome'] is None
    assert body['accounting']['chargedChecks'] is None and not capsys.readouterr().out
