"""Synthetic parser/policy regression only; these tests do not execute a model."""
import copy
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from message_evaluator import ai_provider, proposer
from message_evaluator.policy_v2 import evaluate
from shared_message_contract.validation import MessageError, validate_intent
from evaluation.message_ai.controlled import protocol
from evaluation.message_ai.profile import digest

PACKET = ROOT / 'evaluation/message_ai/playbook_refinement'
FIXTURE = json.loads((PACKET / 'fixtures.json').read_text())
MODEL = 'gpt-4.1-mini-2025-04-14'


def wire(value):
    return json.dumps({'model': MODEL, 'status': 'completed', 'output': [
        {'type': 'message', 'status': 'completed', 'role': 'assistant', 'content': [
            {'type': 'output_text', 'text': json.dumps(value)}]}]}).encode()


@pytest.fixture(autouse=True)
def forbid_provider_and_credentials(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('network/provider/credential work is forbidden in playbook regression')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(proposer, 'propose', forbidden)
    monkeypatch.setattr(proposer, 'secret', forbidden)


@pytest.mark.parametrize('case', FIXTURE['cases'], ids=lambda c:c['id'])
def test_proposed_bilingual_labels_are_structurally_valid_and_policy_bounded(case):
    assert case['split'] == 'development' and case['reviewStatus'] == 'engineering_only'
    intent = case['intent']; validate_intent(intent)
    value = case['proposedAssessment']; text = intent['target']['sanitizedText']
    assert ai_provider.parse(wire(value), text, expected_model=MODEL) == value
    calls = []
    def injected(*args):
        calls.append(True)
        return copy.deepcopy(value)
    outcome = evaluate('playbook-test', intent, ai=injected)
    assert bool(calls) is case['expectedCallbackReached']
    assert {k:outcome[k] for k in case['expectedInjectedPolicy']} == case['expectedInjectedPolicy']
    assert outcome['verdict'] != 'high_risk'  # These cases supply no independent threat.
    # No receipt or balance exists here: a policy output is not settled accounting.
    assert FIXTURE['billingSettlement'] == 'not_exercised'


def test_effective_request_projection_and_count_include_new_prompt_without_extra_data():
    case = next(c for c in FIXTURE['cases'] if c['id'] == 'playbook-05-es')
    body = ai_provider.request_body(case['intent'], SimpleNamespace(model=MODEL,max_output_tokens=512))
    assert body['input'][0]['role'] == 'system'
    assert body['input'][0]['content'][0]['text'] == ai_provider.INSTRUCTIONS
    assert case['intent']['target']['sanitizedText'] not in body['input'][0]['content'][0]['text']
    user = json.loads(body['input'][1]['content'][0]['text'])['untrustedReviewedMessage']
    assert set(user) == {'sanitizedText','language','speakerRole'}
    assert body['tools'] == [] and body['store'] is False
    generation,count,binding = protocol.request_binding(case['intent'],MODEL)
    assert count['input'] == generation['input'] and count['text'] == generation['text']
    assert count['input'][0]['content'][0]['text'] == ai_provider.INSTRUCTIONS
    assert len(binding) == 64


def test_unicode_span_indices_reject_utf16_overrun_and_placeholder_only_grounding():
    case = next(c for c in FIXTURE['cases'] if c['id'] == 'playbook-10-es')
    value = copy.deepcopy(case['proposedAssessment']); text = case['intent']['target']['sanitizedText']
    span = value['reasons'][0]['spans'][0]
    assert text[span['start']:span['end']].startswith('responda')
    assert len(text.encode('utf-16-le')) // 2 == len(text) + 1
    span['end'] += 1
    with pytest.raises(MessageError): ai_provider.parse(wire(value),text)
    value['reasons'][0]['spans'] = [{'start':0,'end':len('[PASSWORD_1]')}]
    with pytest.raises(MessageError): ai_provider.parse(wire(value),'[PASSWORD_1]')


def test_spans_are_ordered_per_category_but_may_overlap_across_categories():
    text = 'Pay immediately. Do not verify.'
    value = {'assessment':'warning','context':'clear','reasons':[
        {'code':'AI_PAYMENT_PRESSURE','spans':[{'start':0,'end':15}]},
        {'code':'AI_CONSEQUENTIAL_URGENCY','spans':[{'start':0,'end':15}]}]}
    assert ai_provider.parse(wire(value),text) == value
    value['reasons'][0]['spans'] = [{'start':4,'end':15},{'start':0,'end':3}]
    with pytest.raises(MessageError): ai_provider.parse(wire(value),text)


def test_historical_refinement_prompt_identity_and_empty_authorization_registries():
    pin = json.loads((PACKET/'profile-identities.json').read_text())
    assert pin['promptSha256'] == ai_provider.PROMPT_SHA256
    assert pin['schemaSha256'] == ai_provider.SCHEMA_SHA256
    # Full historical profiles are retained as evidence, not silently repinned
    # when a later privacy source changes. New scopes pin their own profiles.
    for model in pin['controlledProfiles']:
        assert protocol.profile(model)['promptSha256'] == pin['promptSha256']
        assert protocol.profile(model)['schemaSha256'] == pin['schemaSha256']
    assert ai_provider.PROMPT_SHA256 != pin['baselinePromptSha256']
    assert json.loads((ROOT/'src/message_evaluator/ai_qualifications.json').read_text()) == {}
    assert json.loads((ROOT/'evaluation/message_ai/controlled/approved_experiments.json').read_text()) == {}


def test_even_allowlisted_old_profile_grant_cannot_initialize_new_experiment(tmp_path,monkeypatch):
    from evaluation.message_ai import corpus
    from evaluation.message_ai.controlled import authority
    from evaluation.message_ai.profile import EvaluationError
    manifest = corpus.load(ROOT/'evaluation/message_ai/fixtures/engineering.json')
    grant = {key:None for key in ('authorityPathSha256','hostSha256','expiresAt',
             'generationAttemptCap','countAttemptCap','budgetNano','countMaxChargeNano','evidence','credentialSha256')}
    grant.update(schemaVersion=1,experimentId='old-profile-test',executionMode='fault_test',
                 corpusSha256=digest(manifest),profiles={MODEL:'8bf3ddc823d15213cf25ab35b5f4ae3547e161cda06141ec9c6f199345bcb1bf'})
    monkeypatch.setattr(authority,'approved_registry',lambda:{'old-profile-test':digest(grant)})
    with pytest.raises(EvaluationError,match='PROFILE_IDENTITY_MISMATCH'):
        authority.validate_authorization(grant,manifest,tmp_path,execution=True)
    assert list(tmp_path.iterdir()) == []
