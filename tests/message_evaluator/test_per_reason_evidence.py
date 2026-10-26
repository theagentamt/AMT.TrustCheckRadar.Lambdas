"""Retained semantic gaps stay failures; offline checks do not measure model compliance."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from message_evaluator import ai_provider as ai, proposer
from shared_message_contract.validation import MessageError
from .test_ai_policy import intent, wire
from .test_reasoning_profile import COST, qualified_fixture, settings

OLD_PROMPT_SHA256 = 'b85d182c0a2a81327b0629a7f7fdbeedc36e5b963bdb496f90c735f2751035f6'
SCHEMA_SHA256 = '17a424288447d92815d9fbcb0acc8763eee3c42edd0470a42772d472c1121830'

# Accepted normalized observations, not outputs invented to represent a model run.
# The exact frozen annotation intervals are checked below without changing scoring.
RETAINED = [
    ('dev-019-en', 'en',
     '🔐 Café notice: authorize the transfer today or your account will be frozen.',
     {'assessment': 'warning', 'context': 'clear', 'reasons': [
         {'code': 'AI_PAYMENT_PRESSURE', 'spans': [{'start': 15, 'end': 74}]},
         {'code': 'AI_CONSEQUENTIAL_URGENCY', 'spans': [{'start': 38, 'end': 74}]},
     ]}, 'AI_CONSEQUENTIAL_URGENCY', (15, 37), 'authorize the transfer'),
    ('bank-gift-es', 'es',
     'Su cuenta bancaria se cerrará hoy a menos que compre tarjetas de regalo y nos envíe los códigos de inmediato.',
     {'assessment': 'warning', 'context': 'clear', 'reasons': [
         {'code': 'AI_CONSEQUENTIAL_URGENCY', 'spans': [{'start': 0, 'end': 109}]},
         {'code': 'AI_PAYMENT_PRESSURE', 'spans': [{'start': 46, 'end': 108}]},
         {'code': 'AI_PRETEXT', 'spans': [{'start': 0, 'end': 33}]},
     ]}, 'AI_PRETEXT', (34, 71), 'a menos que compre tarjetas de regalo'),
]


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*_, **__):
        pytest.fail('Provider, credential and network work is forbidden in this regression')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(proposer, 'propose', forbidden)
    monkeypatch.setattr(proposer, 'secret', forbidden)


@pytest.mark.parametrize('case_id,language,text,assessment,code,anchor,anchor_text', RETAINED,
                         ids=[case[0] for case in RETAINED])
def test_request_preserves_retained_input_and_separates_system_instruction(
        case_id, language, text, assessment, code, anchor, anchor_text):
    request = intent(text, language=language)
    before = deepcopy(request)
    body = ai.request_body(request, settings('low'))
    assert request == before
    assert [message['role'] for message in body['input']] == ['system', 'user']
    assert body['input'][0]['content'][0]['text'] == ai.INSTRUCTIONS
    assert text not in ai.INSTRUCTIONS
    submitted = json.loads(body['input'][1]['content'][0]['text'])['untrustedReviewedMessage']
    assert submitted == {'sanitizedText': text, 'language': language, 'speakerRole': 'other'}
    assert submitted['sanitizedText'].encode('utf-8') == text.encode('utf-8')
    assert body['max_output_tokens'] == 512 and settings('low').timeout_ms == 8000
    assert body['reasoning'] == {'effort': 'low'}
    assert body['tools'] == [] and body['store'] is body['stream'] is body['background'] is False
    assert body['text']['format']['schema'] == ai.SCHEMA
    assert body['service_tier'] == 'default' and body['truncation'] == 'disabled'


@pytest.mark.parametrize('case_id,language,text,assessment,code,anchor,anchor_text', RETAINED,
                         ids=[case[0] for case in RETAINED])
def test_retained_literal_grounding_does_not_turn_incomplete_evidence_into_a_pass(
        case_id, language, text, assessment, code, anchor, anchor_text):
    before = deepcopy(assessment)
    assert ai.validate_assessment(assessment, text) == before
    own_spans = next(reason['spans'] for reason in assessment['reasons'] if reason['code'] == code)
    start, end = anchor
    assert text[start:end] == anchor_text
    # The frozen per-reason containment predicate: another reason cannot supply it.
    assert not any(span['start'] <= start and span['end'] >= end for span in own_spans)
    assert any(span['start'] <= start and span['end'] >= end
               for reason in assessment['reasons'] if reason['code'] != code for span in reason['spans'])
    assert assessment == before


def test_same_literal_quote_can_support_different_reasons_without_within_reason_overlap():
    text = 'Please transfer the payment today.'
    value = {'assessment': 'warning', 'context': 'clear', 'reasons': [
        {'code': code, 'quotes': [text]} for code in ('AI_PAYMENT_PRESSURE', 'AI_CONSEQUENTIAL_URGENCY')
    ]}
    assert ai.parse(wire(value), text) == {
        'assessment': 'warning', 'context': 'clear', 'reasons': [
            {'code': code, 'spans': [{'start': 0, 'end': len(text)}]}
            for code in ('AI_PAYMENT_PRESSURE', 'AI_CONSEQUENTIAL_URGENCY')
        ]}
    value['reasons'][0]['quotes'].append(text)
    with pytest.raises(MessageError) as error:
        ai.parse(wire(value), text)
    assert error.value.code == 'PROVIDER_RESPONSE_INVALID'


def test_previous_prompt_qualification_cannot_authorize_the_clarification(monkeypatch, tmp_path):
    path, record = qualified_fixture(monkeypatch, tmp_path, 'low')
    record['promptSha256'] = OLD_PROMPT_SHA256
    path.write_text(json.dumps({'fixture': record}))
    assert ai.PROMPT_SHA256 != OLD_PROMPT_SHA256
    assert ai.SCHEMA_SHA256 == SCHEMA_SHA256
    with pytest.raises(MessageError) as error:
        ai.qualified_settings(COST)
    assert error.value.code == 'PROVIDER_UNAVAILABLE'
