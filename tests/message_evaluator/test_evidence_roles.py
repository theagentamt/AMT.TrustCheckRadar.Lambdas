"""Private role references are structural declarations, never semantic proof."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from message_evaluator import ai_provider as ai
from shared_message_contract.validation import MessageError
from .test_exact_evidence import provider, wire
from .test_per_reason_evidence import RETAINED
from .test_reasoning_profile import COST, qualified_fixture

ROLES = {
    'AI_CREDENTIAL_REQUEST': ('disclosureRequest', 'authenticationPurpose'),
    'AI_PAYMENT_PRESSURE': ('paymentRequest', 'pressure'),
    'AI_PRETEXT': ('claimOrSituation', 'consequentialConnection'),
    'AI_VERIFICATION_BYPASS': ('secrecyOrBypass', 'consequentialAction'),
    'AI_CONSEQUENTIAL_URGENCY': ('requestedAction', 'urgency'),
}


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    from message_evaluator import proposer
    import socket
    def forbidden(*_, **__):
        pytest.fail('No provider, credential or network access in role-reference regressions')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(proposer, 'secret', forbidden)
    monkeypatch.setattr(proposer, 'propose', forbidden)


@pytest.mark.parametrize('code', sorted(ROLES))
def test_each_category_requires_its_own_closed_roles_and_same_quote_can_fill_both(code):
    assert set(ai.EVIDENCE_ROLES) == set(ROLES) == set(ai.AI_REASONS)
    value = provider(['Send the requested payment today.'], code=code)
    value['reasons'][0]['support'] = {role: 0 for role in ROLES[code]}
    before = deepcopy(value)
    assert Draft202012Validator(ai.SCHEMA).is_valid({'result': value})
    parsed = ai.parse(wire(value), 'Send the requested payment today.')
    assert parsed == {'assessment': 'warning', 'context': 'clear', 'reasons': [
        {'code': code, 'spans': [{'start': 0, 'end': 33}]}]}
    assert value == before and 'support' not in json.dumps(parsed)
    # This synthetic shape test intentionally makes no semantic claim for code.


@pytest.mark.parametrize('code', sorted(ROLES))
@pytest.mark.parametrize('change', ['missing', 'extra', 'wrong-category', 'none', 'list', 'string'])
def test_missing_extra_or_cross_category_roles_fail_closed_without_private_diagnostics(code, change, capsys):
    value = provider(['Pay today.'], code=code)
    support = {role: 0 for role in ROLES[code]}
    if change == 'missing': support.pop(ROLES[code][0])
    if change == 'extra': support['private_untrusted_field'] = 'PRIVATE'
    if change == 'wrong-category': support = {'notThisCategory': 0}
    if change == 'none': support = None
    if change == 'list': support = [0, 0]
    if change == 'string': support = 'PRIVATE'
    value['reasons'][0]['support'] = support
    assert not Draft202012Validator(ai.SCHEMA).is_valid({'result': value})
    stages = []; details = []
    with pytest.raises(MessageError) as error:
        ai.parse(wire(value), 'Pay today.', on_rejection=stages.append, on_rejection_detail=details.append)
    assert error.value.code == 'PROVIDER_RESPONSE_INVALID'
    assert stages == ['EVIDENCE_SUPPORT_SHAPE'] and details == [] and capsys.readouterr().out == ''


@pytest.mark.parametrize('index', [-1, 1, 2, 3, True, False, 0.0, '0', None, {}, []])
def test_indices_are_genuine_in_range_integers_in_this_reason(index):
    value = provider(['Pay today.'])
    value['reasons'][0]['support']['paymentRequest'] = index
    stages = []
    with pytest.raises(MessageError):
        ai.parse(wire(value), 'Pay today.', on_rejection=stages.append)
    assert stages == ['EVIDENCE_SUPPORT_INDEX']


def test_indices_reference_only_the_same_reasons_quotes_and_do_not_reorder_them():
    text = 'Pay the invoice today.'
    value = provider(['Pay the invoice', 'today'])
    value['reasons'][0]['support'] = {'paymentRequest': 0, 'pressure': 1}
    value['reasons'].append({'code': 'AI_CONSEQUENTIAL_URGENCY', 'quotes': ['today'],
                             'support': {'requestedAction': 1, 'urgency': 0}})
    with pytest.raises(MessageError): ai.parse(wire(value), text)  # No cross-reason index 1.
    value['reasons'].pop()
    assert Draft202012Validator(ai.SCHEMA).is_valid({'result': value})
    assert ai.parse(wire(value), text)['reasons'][0]['spans'] == [
        {'start': 0, 'end': 15}, {'start': 16, 'end': 21}]
    value['reasons'][0]['quotes'].reverse()
    with pytest.raises(MessageError): ai.parse(wire(value), text)  # No sorting/repair.


def test_legacy_quote_only_shape_is_not_a_fallback():
    value = provider(['Pay today.']); del value['reasons'][0]['support']
    assert not Draft202012Validator(ai.SCHEMA).is_valid({'result': value})
    with pytest.raises(MessageError): ai.parse(wire(value), 'Pay today.')


@pytest.mark.parametrize('case_id,language,text,assessment,code,anchor,anchor_text', RETAINED,
                         ids=[case[0] for case in RETAINED])
def test_false_role_declaration_cannot_repair_retained_semantic_gap(
        case_id, language, text, assessment, code, anchor, anchor_text):
    private = {**assessment, 'reasons': [{
        'code': reason['code'],
        'quotes': [text[span['start']:span['end']] for span in reason['spans']],
        'support': {role: 0 for role in ROLES[reason['code']]},
    } for reason in assessment['reasons']]}
    before = deepcopy(private)
    assert Draft202012Validator(ai.SCHEMA).is_valid({'result': private})
    parsed = ai.parse(wire(private), text)
    assert parsed == assessment and private == before  # No widening/borrowing/semantic repair.
    own = next(reason['spans'] for reason in parsed['reasons'] if reason['code'] == code)
    start, end = anchor
    assert text[start:end] == anchor_text
    assert not any(span['start'] <= start and end <= span['end'] for span in own)
    # URGENCY_ACTION and the separate Spanish PRETEXT connection remain failures.


@pytest.mark.parametrize('field,old', [
    ('promptSha256', 'd83318dc527266f65ccc8e342f49ab790b27b2ba43dcd5c17dd90e78ea4a2137'),
    ('schemaSha256', '17a424288447d92815d9fbcb0acc8763eee3c42edd0470a42772d472c1121830'),
])
def test_pre_role_qualification_cannot_authorize_private_revision(monkeypatch, tmp_path, field, old):
    path, record = qualified_fixture(monkeypatch, tmp_path, 'low')
    assert record[field] != old
    record[field] = old; path.write_text(json.dumps({'fixture': record}))
    with pytest.raises(MessageError): ai.qualified_settings(COST)
    root = Path(__file__).resolve().parents[2]
    assert json.loads((root/'src/message_evaluator/ai_qualifications.json').read_text()) == {}


def test_category_definitions_remain_identical_to_frozen_historical_request():
    root = Path(__file__).resolve().parents[2]
    historical = json.loads((root/'tests/message_ai_diagnostic/fixtures/pr166-dev-019-en.request.json').read_text())
    old = historical['input'][0]['content'][0]['text']
    def definitions(prompt):
        return prompt.split('For clear context, use warning only with one or more supported AI categories:\n', 1)[1].split('Spelling, HTTP alone, names and demographic attributes must not establish warnings.', 1)[0]
    assert definitions(ai.INSTRUCTIONS) == definitions(old)
    assert ai.FORMAT_NAME == 'message_ai_role_evidence_v1'
