"""Bounded inference configuration/envelopes; offline protocol tests, not model quality."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from message_evaluator import ai_provider as ai, proposer
from shared_message_contract.provider_cost import CostPolicy
from shared_message_contract.validation import MessageError
from .test_normal_usage import INTENT, TEXT, response

MODEL = ai.REASONING_MODEL
ARN = 'arn:aws:secretsmanager:us-east-1:107827791950:secret:trustcheckradar/dev/openai-ABC123'
COST = CostPolicy(MODEL, 400000, 512, 750, 4500)


def settings(effort=None, **changes):
    return ai.AISettings(**({'model': MODEL, 'secret_arn': ARN, 'timeout_ms': 8000,
                            'max_output_tokens': 512, 'reasoning_effort': effort} | changes))


def raw_response(item=None):
    raw = response() | {'model': MODEL}
    raw['usage'].update(output_tokens=100, total_tokens=200,
                        output_tokens_details={'reasoning_tokens': 80})
    if item is not None:
        raw['output'].insert(0, item)
    return raw


def metadata(**changes):
    return {'type': 'reasoning', 'id': 'rs_fixture', 'summary': []} | changes


def parse(raw, effort='low', stages=None):
    return ai.parse(json.dumps(raw).encode(), TEXT, expected_model=MODEL,
                    reasoning_effort=effort, on_rejection=None if stages is None else stages.append)


def test_omitted_effort_preserves_entire_old_wire_body_and_legacy_settings():
    before = ai.request_body(INTENT, SimpleNamespace(model=MODEL, max_output_tokens=512))
    assert ai.request_body(INTENT, settings()) == before
    assert json.dumps(ai.request_body(INTENT, settings()), ensure_ascii=False).encode() == json.dumps(before, ensure_ascii=False).encode()
    assert 'reasoning' not in before
    assert ai.inference_profile(settings()) is None
    assert not hasattr(proposer.Settings(MODEL, ARN, 8000, 512), 'reasoning_effort')
    after = ai.request_body(INTENT, settings('low'))
    assert after.pop('reasoning') == {'effort': 'low'} and after == before
    assert before['tools'] == [] and before['store'] is False and before['truncation'] == 'disabled'


@pytest.mark.parametrize('effort', ['', 'none', 'minimal', 'medium', 'high', True, 0, {}, []])
def test_unapproved_efforts_fail_before_transport(effort):
    with pytest.raises(MessageError): settings(effort)
    with pytest.raises(MessageError):
        ai.request_body(INTENT, SimpleNamespace(model=MODEL, max_output_tokens=512, reasoning_effort=effort))


@pytest.mark.parametrize('changes', [
    {'model': 'gpt-5.4-mini'}, {'model': 'gpt-5.4-mini-2026-03-18'},
    {'model': 'gpt-4.1-mini-2025-04-14'}, {'max_output_tokens': 256},
    {'max_output_tokens': 513}, {'max_output_tokens': True}, {'timeout_ms': 8001}])
def test_low_is_bound_to_dated_model_total_output_and_existing_deadline(changes):
    with pytest.raises(MessageError): settings('low', **changes)


def qualified_fixture(monkeypatch, tmp_path, effort=None):
    contract = (Path(__file__).resolve().parents[2] / 'contracts/message-consumer/1.0.0-candidate.2/SHA256SUMS').read_bytes()
    (tmp_path / 'ai_contract').mkdir(); (tmp_path / 'ai_contract/SHA256SUMS').write_bytes(contract)
    (tmp_path / 'qualifications').mkdir()
    evidence = b'{"offlineUnitOnly":true}'
    (tmp_path / 'qualifications/fixture.json').write_bytes(evidence)
    record = {'model': MODEL, 'policyVersion': ai.POLICY, 'promptSha256': ai.PROMPT_SHA256,
              'schemaSha256': ai.SCHEMA_SHA256, 'contractSha256': hashlib.sha256(contract).hexdigest(),
              'evidenceSha256': hashlib.sha256(evidence).hexdigest(), 'status': 'approved', 'costPolicy': COST.as_dict()}
    if effort is not None: record['inferenceProfile'] = {'reasoningEffort': effort, 'maxOutputTokens': 512}
    path = tmp_path / 'ai_qualifications.json'
    path.write_text(json.dumps({'fixture': record}))
    monkeypatch.setattr(ai, '__file__', str(tmp_path / 'ai_provider.py'))
    for key, value in {'STAGE': 'dev', 'MESSAGE_AI_ENABLED': 'true', 'MESSAGE_AI_QUALIFIED': 'true',
        'MESSAGE_AI_POLICY_VERSION': ai.POLICY, 'MESSAGE_AI_POLICY_APPROVAL_SHA256': ai.APPROVAL_SHA,
        'MESSAGE_AI_QUALIFICATION_ID': 'fixture', 'MESSAGE_PROPOSER_MODEL': MODEL,
        'MESSAGE_PROPOSER_SECRET_ARN': ARN, 'MESSAGE_PROPOSER_TIMEOUT_MS': '8000',
        'MESSAGE_PROPOSER_MAX_OUTPUT_TOKENS': '512'}.items(): monkeypatch.setenv(key, value)
    monkeypatch.delenv('MESSAGE_AI_REASONING_EFFORT', raising=False)
    if effort is not None: monkeypatch.setenv('MESSAGE_AI_REASONING_EFFORT', effort)
    return path, record


def test_qualification_omission_cannot_authorize_explicit_effort_or_the_reverse(monkeypatch, tmp_path):
    path, record = qualified_fixture(monkeypatch, tmp_path)
    assert ai.qualified_settings(COST).reasoning_effort is None
    monkeypatch.setenv('MESSAGE_AI_REASONING_EFFORT', 'low')
    with pytest.raises(MessageError): ai.qualified_settings(COST)
    record['inferenceProfile'] = {'reasoningEffort': 'low', 'maxOutputTokens': 512}
    path.write_text(json.dumps({'fixture': record}))
    assert ai.qualified_settings(COST).reasoning_effort == 'low'
    monkeypatch.delenv('MESSAGE_AI_REASONING_EFFORT')
    with pytest.raises(MessageError): ai.qualified_settings(COST)


@pytest.mark.parametrize('profile', [None, {}, {'reasoningEffort': 'none', 'maxOutputTokens': 512},
    {'reasoningEffort': 'low', 'maxOutputTokens': 256},
    {'reasoningEffort': 'low', 'maxOutputTokens': '512'},
    {'reasoningEffort': 'low', 'maxOutputTokens': 512.0},
    {'reasoningEffort': 'low', 'maxOutputTokens': 512, 'extra': True}])
def test_profile_record_drift_fails_closed(monkeypatch, tmp_path, profile):
    path, record = qualified_fixture(monkeypatch, tmp_path, 'low')
    path.write_text(json.dumps({'fixture': record | {'inferenceProfile': profile}}))
    with pytest.raises(MessageError): ai.qualified_settings(COST)


@pytest.mark.parametrize('item', [None, metadata(), metadata(status='completed'),
    metadata(content=[], encrypted_content=None), metadata(encrypted_content='opaque-fixture-not-a-secret')])
def test_only_explicit_profile_accepts_optional_closed_reasoning_metadata(item, capsys):
    raw = raw_response(item)
    assert parse(raw) == {'assessment': 'no_warning', 'context': 'clear', 'reasons': []}
    assert capsys.readouterr().out == ''
    if item is not None:
        stages = []
        with pytest.raises(MessageError): parse(raw, None, stages)
        assert stages == ['OUTPUT_SHAPE']


@pytest.mark.parametrize('changes', [
    {'type': 'function_call'}, {'summary': [{'type': 'summary_text', 'text': 'PRIVATE'}]},
    {'summary': None}, {'summary': ''}, {'content': [{'type': 'reasoning_text', 'text': 'PRIVATE'}]},
    {'content': None}, {'status': 'incomplete'}, {'status': 'in_progress'}, {'status': None},
    {'id': ''}, {'id': True}, {'id': 'x'*129}, {'id': 'private id'},
    {'encrypted_content': True}, {'encrypted_content': ''}, {'encrypted_content': 'x'*16385},
    {'extra': 'PRIVATE'}])
def test_unrequested_reasoning_content_shape_or_unfinished_item_is_rejected(changes, capsys):
    stages = []
    with pytest.raises(MessageError): parse(raw_response(metadata(**changes)), stages=stages)
    assert stages in (['REASONING_ITEM_SHAPE'], ['RESPONSE_BOUNDS'])
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('mutation', [
    lambda r: r['output'].reverse(),
    lambda r: r['output'].insert(0, metadata()),
    lambda r: r['output'].append(copy.deepcopy(r['output'][-1])),
    lambda r: r['output'][-1]['content'][0].update(type='refusal', text='PRIVATE'),
    lambda r: r.update(status='incomplete', incomplete_details={'reason': 'max_output_tokens'}),
    lambda r: r['output'][-1].update(status='incomplete'),
    lambda r: r['output'][-1]['content'][0].update(text='not-json PRIVATE'),
    lambda r: r.update(model='different-model'),
])
def test_reasoning_does_not_relax_existing_message_status_refusal_or_json_guards(mutation):
    raw = raw_response(metadata()); mutation(raw)
    with pytest.raises(MessageError): parse(raw)


def test_usage_is_validated_before_parse_and_no_reasoning_material_enters_logs(monkeypatch, capsys):
    raw = raw_response(metadata(encrypted_content='opaque-private-fixture'))
    calls = []
    monkeypatch.setattr(ai, 'qualified_settings', lambda cost: settings('low'))
    def fake(intent, config, ms, **kwargs):
        calls.append(kwargs['body_builder'](intent, config))
        return kwargs['response_parser'](json.dumps(raw).encode(), TEXT)
    monkeypatch.setattr(proposer, 'propose', fake)
    assert ai.assess(INTENT, 8000, cost_policy=COST)['assessment'] == 'no_warning'
    logs = capsys.readouterr().out
    assert len(calls) == 1 and calls[0]['reasoning'] == {'effort': 'low'}
    usage = json.loads(logs)['usage']
    assert usage['reasoningTokens'] == 80 and usage['outputTokens'] == 100
    assert 'opaque-private-fixture' not in logs and 'rs_fixture' not in logs and TEXT not in logs
    raw['usage']['output_tokens'] = 513; raw['usage']['total_tokens'] = 613
    with pytest.raises(MessageError): ai.assess(INTENT, 8000, cost_policy=COST)
    assert len(calls) == 2 and json.loads(capsys.readouterr().out)['failureReason'] == 'COST_BOUND_EXCEEDED'


@pytest.mark.parametrize('field', ['id', 'type', 'summary'])
def test_missing_reasoning_required_field_fails_closed(field):
    item = metadata(); del item[field]
    with pytest.raises(MessageError): parse(raw_response(item))


@pytest.mark.parametrize('effort', ['', 'none', 'medium', True, {}, 0])
def test_parser_cannot_invent_an_unqualified_inference_profile(effort):
    stages = []
    with pytest.raises(MessageError): parse(raw_response(metadata()), effort=effort, stages=stages)
    assert stages == ['INFERENCE_PROFILE']


def test_reasoning_does_not_weaken_exact_grounding_or_log_rejected_material():
    raw = raw_response(metadata(encrypted_content='opaque-private'))
    raw['output'][-1]['content'][0]['text'] = json.dumps({'result': {
        'assessment': 'warning', 'context': 'clear',
        'reasons': [{'code': 'AI_PRETEXT', 'quotes': ['not present in reviewed text'], 'support': {'claimOrSituation':0,'consequentialConnection':0}}]}})
    stages = []
    with pytest.raises(MessageError): parse(raw, stages=stages)
    assert stages == ['EVIDENCE_NOT_FOUND']
