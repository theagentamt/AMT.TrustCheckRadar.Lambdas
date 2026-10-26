"""Engineering contrasts for the permanent prompt/codec/policy, not model accuracy."""
from copy import deepcopy
import hashlib
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
ROOT = Path(__file__).resolve().parents[2]
RUBRIC = ROOT/'evaluation/message_ai/product_rules_r2'
PACKET = json.loads((RUBRIC/'fixtures.json').read_text())
CASES = PACKET['cases']


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*_,**__):
        pytest.fail('No model, credential or network call is allowed in contrast regressions')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(socket,'getaddrinfo',forbidden)
    monkeypatch.setattr(proposer,'propose',forbidden)
    monkeypatch.setattr(proposer,'secret',forbidden)


@pytest.mark.parametrize('case',CASES,ids=[c['caseId'] for c in CASES])
def test_reference_contrasts_preserve_real_request_and_public_policy(case):
    family,language,text=(case[k] for k in ('family','language','sanitizedText'))
    assessment,context=case['assessment'],case['context']
    codes=[r['code'] for r in case['referenceReasons']]
    assert set(case['requiredReasons'])<=set(codes)<=set(case['allowedReasons'])
    request=intent(text,language=language)
    body=ai.request_body(request,SimpleNamespace(model='fixture-model-2026-01-01',max_output_tokens=512))
    instructions=body['input'][0]['content'][0]['text']
    assert instructions==ai.INSTRUCTIONS and text not in instructions
    assert 'Classify codes by their stated function' in instructions
    assert 'A request for redemption value alone' in instructions
    assert 'retain this category when supported' in instructions
    assert 'without its\npurpose or other supported risk evidence' in instructions
    assert 'not only the name' in instructions
    assert 'not proof of impersonation, verified identity or fraud' in instructions
    assert json.loads(body['input'][1]['content'][0]['text'])['untrustedReviewedMessage']=={
        'sanitizedText':text,'language':language,'speakerRole':'other'}
    assert body['tools']==[] and body['store'] is False
    reference={'assessment':assessment,'context':context,'reasons':[{**reason,'support':{role:0 for role in ai.EVIDENCE_ROLES[reason['code']]}} for reason in case['referenceReasons']]}
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
    language,text=CASES[1]['language'],CASES[1]['sanitizedText']
    response={'assessment':'warning','context':'clear','reasons':[
        {'code':code,'quotes':[text],'support':{role:0 for role in ai.EVIDENCE_ROLES[code]}} for code in [PAYMENT,URGENCY,CREDENTIAL]]}
    parsed=ai.parse(wire(response),text)
    outcome=evaluate('unchanged-failure',intent(text,language=language),ai=lambda *_:parsed,ai_primary=True)
    # Exact grounding is structural. A prompt clarification is not a new semantic
    # filter/refund or a reason to re-label the frozen nine-of-ten batch as passing.
    assert set(outcome['aiReasonCodes'])=={PAYMENT,URGENCY,CREDENTIAL}
    assert not set(outcome['aiReasonCodes'])<={PAYMENT,URGENCY,'AI_PRETEXT'}


def test_private_revision_invalidates_pr163_evidence_without_changing_registry():
    assert ai.PROMPT_SHA256!='1e5a9ce98df0d9cc1c4b74a0847dff01641a614ef7a50683b972ba757d0cb5d9'
    assert ai.SCHEMA_SHA256!='17a424288447d92815d9fbcb0acc8763eee3c42edd0470a42772d472c1121830'
    root=Path(__file__).resolve().parents[2]
    for path in ('src/message_evaluator/ai_qualifications.json','evaluation/message_ai/controlled/approved_experiments.json'):
        assert json.loads((root/path).read_text())=={}


def test_new_criteria_are_prospective_and_do_not_claim_model_measurement():
    assert PACKET['criteriaVersion']=='message-product-rules-2026-10-25-r2'
    assert PACKET['historicalBatchesRescored'] is False
    assert PACKET['modelQualityMeasured'] is False
    assert len(CASES)==16
    for family in {c['family'] for c in CASES}:
        assert {c['language'] for c in CASES if c['family']==family}=={'en','es'}
    rubric=(RUBRIC/'RUBRIC.en-es.md').read_text()
    assert PACKET['criteriaVersion'] in rubric
    assert 'insufficient' in rubric and 'contexto insuficiente' in rubric


@pytest.mark.parametrize('case_id,code',[
    ('ambiguous-en',CREDENTIAL),('ambiguous-es',CREDENTIAL),
    ('routine-business-en','AI_PRETEXT'),('routine-business-es','AI_PRETEXT')])
def test_parser_does_not_add_a_lexical_semantic_override(case_id,code):
    case=next(c for c in CASES if c['caseId']==case_id)
    text=case['sanitizedText']
    # These are structurally valid but semantically wrong reference responses.
    # Prompt/rubric changes do not filter categories to manufacture a pass.
    parsed=ai.parse(wire({'assessment':'warning','context':'clear','reasons':[
        {'code':code,'quotes':[text],'support':{role:0 for role in ai.EVIDENCE_ROLES[code]}}]}),text)
    assert parsed['reasons'][0]['code']==code


def test_historical_engineering_notes_keep_their_recorded_checksum():
    historical=ROOT/'evaluation/message_ai/development_packet'
    for line in (historical/'SHA256SUMS').read_text().splitlines():
        checksum,name=line.split('  ')
        if name=='engineering/review-notes.md':
            assert hashlib.sha256((historical/name).read_bytes()).hexdigest()==checksum
            break
    else:
        pytest.fail('Historical engineering-notes record missing')
