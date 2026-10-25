"""Engineering contrasts for the permanent prompt/codec/policy, not model accuracy."""
from copy import deepcopy
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from message_evaluator import ai_provider as ai, proposer
from message_evaluator.policy_v3 import evaluate
from .test_ai_policy import intent, wire

PAYMENT = 'AI_PAYMENT_PRESSURE'
URGENCY = 'AI_CONSEQUENTIAL_URGENCY'
CREDENTIAL = 'AI_CREDENTIAL_REQUEST'
CASES = [
    ('gift-card-only','en',
     'Your bank account will be closed today unless you buy gift cards and send us the codes immediately.',
     'warning','clear',[PAYMENT,URGENCY]),
    ('gift-card-only','es',
     'Su cuenta bancaria se cerrará hoy a menos que compre tarjetas de regalo y nos envíe los códigos de inmediato.',
     'warning','clear',[PAYMENT,URGENCY]),
    ('authentication-only','en',
     'Please send your account password and login authentication code so I can finish this refund.',
     'warning','clear',[CREDENTIAL]),
    ('authentication-only','es',
     'Envíeme la contraseña de su cuenta y el código de autenticación de acceso para que pueda completar este reembolso.',
     'warning','clear',[CREDENTIAL]),
    ('mixed','en',
     'Buy gift cards and send the redemption PINs immediately or your account will close; also send your account password and login authentication code.',
     'warning','clear',[PAYMENT,URGENCY,CREDENTIAL]),
    ('mixed','es',
     'Compre tarjetas de regalo y envíe los PIN de canje de inmediato o se cerrará su cuenta; además envíe la contraseña de su cuenta y el código de autenticación de acceso.',
     'warning','clear',[PAYMENT,URGENCY,CREDENTIAL]),
    ('preventive','en',
     'Never share gift-card redemption PINs, account passwords or login authentication codes with a stranger.',
     'no_warning','clear',[]),
    ('preventive','es',
     'Nunca comparta los PIN de canje de tarjetas de regalo, contraseñas de cuenta ni códigos de autenticación de acceso con desconocidos.',
     'no_warning','clear',[]),
    ('ambiguous','en','Send me the code you received.','abstain','insufficient',[]),
    ('ambiguous','es','Envíeme el código que recibió.','abstain','insufficient',[]),
]


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*_,**__):
        pytest.fail('No model, credential or network call is allowed in contrast regressions')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(socket,'getaddrinfo',forbidden)
    monkeypatch.setattr(proposer,'propose',forbidden)
    monkeypatch.setattr(proposer,'secret',forbidden)


@pytest.mark.parametrize('family,language,text,assessment,context,codes',CASES,
                         ids=[f'{c[0]}-{c[1]}' for c in CASES])
def test_reference_contrasts_preserve_real_request_and_public_policy(family,language,text,assessment,context,codes):
    request=intent(text,language=language)
    body=ai.request_body(request,SimpleNamespace(model='fixture-model-2026-01-01',max_output_tokens=512))
    instructions=body['input'][0]['content'][0]['text']
    assert instructions==ai.INSTRUCTIONS and text not in instructions
    assert 'Classify codes by their stated function' in instructions
    assert 'A request for redemption value alone' in instructions
    assert 'retain this category when supported' in instructions
    assert 'if that\nmissing purpose is decisive' in instructions
    assert json.loads(body['input'][1]['content'][0]['text'])['untrustedReviewedMessage']=={
        'sanitizedText':text,'language':language,'speakerRole':'other'}
    assert body['tools']==[] and body['store'] is False
    reference={'assessment':assessment,'context':context,'reasons':[
        {'code':code,'quotes':[text]} for code in codes]}
    parsed=ai.parse(wire(reference),text)
    assert [r['code'] for r in parsed['reasons']]==codes
    calls=[]
    def reference_callback(*_):
        calls.append(True)
        return deepcopy(parsed)
    outcome=evaluate('code-purpose',request,ai=reference_callback,ai_primary=True)
    assert len(calls)==1 and outcome['schemaVersion']==3
    assert outcome['aiReasonCodes']==codes
    assert outcome['aiAssessmentStatus']==('abstained' if assessment=='abstain' else assessment)
    assert outcome['processingOutcome']==('inconclusive' if assessment=='abstain' else 'complete')
    assert outcome['verdict']=={'warning':'suspicious','no_warning':'no_known_threat_detected','abstain':'unknown'}[assessment]
    # The data specifies engineering expectations; no model was asked to produce it.
    assert CREDENTIAL in codes if family in ('authentication-only','mixed') else CREDENTIAL not in codes


def test_grounding_does_not_silently_remove_a_semantically_wrong_credential_reason():
    _,language,text,*_=CASES[1]
    response={'assessment':'warning','context':'clear','reasons':[
        {'code':code,'quotes':[text]} for code in [PAYMENT,URGENCY,CREDENTIAL]]}
    parsed=ai.parse(wire(response),text)
    outcome=evaluate('unchanged-failure',intent(text,language=language),ai=lambda *_:parsed,ai_primary=True)
    # Exact grounding is structural. A prompt clarification is not a new semantic
    # filter/refund or a reason to re-label the frozen nine-of-ten batch as passing.
    assert set(outcome['aiReasonCodes'])=={PAYMENT,URGENCY,CREDENTIAL}
    assert not set(outcome['aiReasonCodes'])<={PAYMENT,URGENCY,'AI_PRETEXT'}


def test_prompt_revision_invalidates_pr163_evidence_without_changing_schema_or_registry():
    assert ai.PROMPT_SHA256!='1e5a9ce98df0d9cc1c4b74a0847dff01641a614ef7a50683b972ba757d0cb5d9'
    assert ai.SCHEMA_SHA256=='17a424288447d92815d9fbcb0acc8763eee3c42edd0470a42772d472c1121830'
    root=Path(__file__).resolve().parents[2]
    for path in ('src/message_evaluator/ai_qualifications.json','evaluation/message_ai/controlled/approved_experiments.json'):
        assert json.loads((root/path).read_text())=={}
