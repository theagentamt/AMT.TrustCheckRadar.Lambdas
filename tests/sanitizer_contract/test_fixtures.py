"""Shared curated expectations and existing backend boundary; no server raw sanitizer."""
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from shared_message_contract.validation import validate_intent,MessageError,TYPES
from shared_message_contract.validation_v2 import validate_intent as validate_v2_intent

DIR = ROOT/'contracts/sanitizer/v1'
PROFILE = json.loads((DIR/'profile.json').read_text())
CASES = json.loads((DIR/'fixtures.json').read_text())['cases']


def intent(text='Hello',entities=(),language='en',source='pasted_text'):
    return {'entryPoint':'message','language':language,'target':{'scope':'sanitized_message',
        'sourceType':source,'sanitizedText':text,'speakerRole':'other','entities':list(entities),
        'withheldLinks':any(e['type']=='url' for e in entities),'reviewedLinks':[]}}


@pytest.mark.parametrize('case',[c for c in CASES if c['expected']['status']=='ready'],ids=lambda c:c['id'])
def test_curated_expected_projection_obeys_both_existing_versions(case):
    expected=case['expected'];value=intent(expected['sanitizedText'],expected['entities'],case['language'],case['sourceType'])
    for validate in (validate_intent,validate_v2_intent):
        if expected['governedAccepts']:
            assert validate(value) == value
        else:
            with pytest.raises(MessageError):validate(value)
    assert all(set(e)=={'type','token'} for e in expected['entities'])
    assert len({e['token'] for e in expected['entities']}) == len(expected['entities'])


def test_nine_type_vocabulary_is_subset_not_change_to_governed_contract():
    assert PROFILE['detectedTypes'] == {k:v for k,v in TYPES.items() if k not in ('password','verification_code')}
    covered={e['type'] for c in CASES for e in c['expected']['entities']}
    assert covered == set(PROFILE['detectedTypes'])
    for version in ('1.0.0-candidate.1','1.0.0-candidate.2'):
        path=ROOT/'contracts/message-consumer'/version
        for line in (path/'SHA256SUMS').read_text().splitlines():
            expected,name=line.split('  ')
            assert hashlib.sha256((path/name).read_bytes()).hexdigest()==expected


@pytest.mark.parametrize('text,entities',[
    ('[PHONE_1] then [PHONE_1]',[{'type':'phone','token':'[PHONE_1]'}]),
    ('[EMAIL_1] [PHONE_1] [EMAIL_2] [EMAIL_1]',[{'type':'email','token':'[EMAIL_1]'},
        {'type':'phone','token':'[PHONE_1]'},{'type':'email','token':'[EMAIL_2]'}]),
])
def test_repeated_occurrences_need_one_declaration(text,entities):
    assert validate_intent(intent(text,entities))
    with pytest.raises(MessageError):validate_intent(intent(text,entities+[entities[0]]))


@pytest.mark.parametrize('text,entities',[
    ('[PHONE_0]',[{'type':'phone','token':'[PHONE_0]'}]),
    ('[PHONE_01]',[{'type':'phone','token':'[PHONE_01]'}]),
    ('[PHONE_2]',[{'type':'phone','token':'[PHONE_2]'}]),
    ('[PHONE_1]',[]),
    ('Hello',[{'type':'phone','token':'[PHONE_1]'}]),
    ('[PHONE_1]',[{'type':'email','token':'[PHONE_1]'}]),
    ('[PERSON_1]',[{'type':'phone','token':'[PERSON_1]'}]),
    ('[phone_1]',[{'type':'phone','token':'[phone_1]'}]),
    ('[PHONE_1]',[{'type':'phone','token':'[PHONE_1]','original':'synthetic private value'}]),
    ('[PHONE_1',[]),
])
def test_malformed_or_raw_entity_projection_is_rejected(text,entities):
    with pytest.raises(MessageError):validate_intent(intent(text,entities))


@pytest.mark.parametrize('size',[0,100,101])
def test_declaration_boundaries(size):
    entities=[{'type':'phone','token':f'[PHONE_{i}]'} for i in range(1,size+1)]
    value=intent(' '.join(x['token'] for x in entities) or 'Hello',entities)
    if size<=100:assert validate_intent(value)
    else:
        with pytest.raises(MessageError):validate_intent(value)


@pytest.mark.parametrize('size',[7999,8000,8001])
@pytest.mark.parametrize('symbol',['a','😀'])
def test_governed_codepoints_not_utf16_or_graphemes(size,symbol):
    value=intent(symbol*size)
    if size<=8000:assert validate_intent(value)
    else:
        with pytest.raises(MessageError):validate_intent(value)


@pytest.mark.parametrize('text',['e\u0301','👩\u200d💻','a\ud800','a\r\nb','a\u202eb',' Hello '])
def test_governed_requires_already_reviewed_nfc_trim_and_controls(text):
    with pytest.raises(MessageError):validate_intent(intent(text))


@pytest.mark.parametrize('source',['pasted_text','ocr','mixed','screenshot_ocr','combined'])
def test_exact_source_provenance(source):
    if source in ('pasted_text','ocr','mixed'):assert validate_intent(intent(source=source))
    else:
        with pytest.raises(MessageError):validate_intent(intent(source=source))


@pytest.mark.parametrize('text',[
    'Address 2001:db8::1','Address fe80::1%eth0','Dirección fe80::1%25eth0',
    'Mapped ::ffff:192.0.2.1','Address [2001:db8::1]:443','Address 2001:db8::1.',
    'Address 2001:0db8:0:0:0:0:0:0001','Local ::1',
])
def test_additive_ipv6_guard_prevents_direct_evaluator_and_vendor_projection(text):
    from shared_message_contract.privacy import reject_residual_ipv6,validate_runtime_intent
    from message_evaluator.policy import evaluate
    from message_evaluator.policy_v2 import evaluate as evaluate_v2
    from message_evaluator.proposer import request_body
    from types import SimpleNamespace
    value=intent(text)
    def forbidden(*args):pytest.fail('provider or lookup called for residual private address')
    for call in (lambda:reject_residual_ipv6(text),lambda:validate_runtime_intent(value),
                 lambda:evaluate('check',value,lookup=forbidden,proposer=forbidden),
                 lambda:evaluate_v2('check',value,lookup=forbidden,ai=forbidden),
                 lambda:request_body(value,SimpleNamespace(model='fixture',max_output_tokens=512))):
        with pytest.raises(MessageError,match='PRIVACY_REVIEW_REQUIRED'):call()


@pytest.mark.parametrize('text',['Meet at 12:30','Time 12:30:00','word::namespace','Pause :: continue',
                                'dead beef','Label: value','Cita a las 12:30','[IP_ADDRESS_1]'])
def test_ipv6_guard_negative_controls(text):
    from shared_message_contract.privacy import reject_residual_ipv6
    assert reject_residual_ipv6(text)==text


def test_published_supplement_checksums_and_current_evaluation_source_pins():
    sys.path.insert(0,str(ROOT))
    from evaluation.message_ai.controlled.protocol import profile
    from evaluation.message_ai.profile import digest
    for directory in (DIR,ROOT/'contracts/analysis/1.0-wire-v1'):
        for line in (directory/'SHA256SUMS').read_text().splitlines():
            checksum,name=line.split('  ')
            assert hashlib.sha256((directory/name).read_bytes()).hexdigest()==checksum
    pins=json.loads((DIR/'evaluation-profile-identities.json').read_text())
    for model,checksum in pins['controlledProfiles'].items():
        current=profile(model)
        assert digest(current)==checksum
        assert current['promptSha256']==pins['promptSha256']
        assert current['schemaSha256']==pins['schemaSha256']
        assert 'src/shared_message_contract/privacy.py' in current['sourceSha256']
    assert pins['qualification']=='none' and pins['paidExecutionAuthorized'] is False
