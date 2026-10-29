"""Finite single-screenshot coverage; no extraction of arbitrary risky text."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from message_evaluator.coverage import QUALIFIED_PHRASES
from message_evaluator.policy_v3 import evaluate
from shared_message_contract.validation import MessageError


def intent(text, language='en', source='ocr', speaker='other'):
    return {'entryPoint': 'message', 'language': language, 'target': {
        'scope': 'sanitized_message', 'sourceType': source, 'sanitizedText': text,
        'speakerRole': speaker, 'entities': [], 'withheldLinks': False,
        'reviewedLinks': []}}


def assess(request):
    return evaluate('check', request, rules_only=True,
                    ai=lambda *_: pytest.fail('AI called'),
                    lookup=lambda *_: pytest.fail('URL provider called'))


def test_retained_mobile_gift_card_ocr_is_already_qualified_content():
    text = 'Screenshot 1:\nPay this tax fee by buying gift cards and\nsend me the numbers and PINs.'
    request = intent(text)
    original = deepcopy(request)
    assert assess(intent(text.split('\n', 1)[1]))['ruleIds'] == ['DEMAND_GIFT_CARD_PAYMENT']
    actual = assess(request)
    assert actual['ruleIds'] == ['DEMAND_GIFT_CARD_PAYMENT']
    assert actual['verdict'] == 'high_risk'
    assert actual['processingOutcome'] == 'complete'
    assert actual['assessmentBasis'] == ['qualified_rules']
    assert actual['aiAssessmentStatus'] == 'not_assessed'
    assert request == original  # Matching must not rewrite the reviewed/provider text.


@pytest.mark.parametrize('language,text,rule', [
    (language, text, rule)
    for language, rows in QUALIFIED_PHRASES.items() for text, rule in rows.items()
])
def test_single_screenshot_forms_preserve_all_finite_risky_families(language, text, rule):
    for body in (text, text.upper(), text.replace(' ', '\n')):
        actual = assess(intent('Screenshot 1:\n' + body, language))
        assert actual['ruleIds'] == [rule]
        assert actual['processingOutcome'] == 'complete'
        assert actual['verdict'] == ('suspicious' if rule == 'PAYMENT_WITH_SECRECY_PRESSURE' else 'high_risk')
    # A source declaration is not proof of meaning: context still participates
    # in the exact whole-message match, even inside a screenshot.
    for body in ('Never ' + text, 'Nunca ' + text, 'Example: ' + text,
                 'Ejemplo: ' + text, '"' + text + '"',
                 text + ' This is only a warning.', text + ' Esto es una advertencia.',
                 text + '\nScreenshot 2:\nNever follow this request.'):
        assert assess(intent('Screenshot 1:\n' + body, language))['ruleIds'] == []
    for source in ('pasted_text', 'mixed'):
        assert assess(intent('Screenshot 1:\n' + text, language, source))['ruleIds'] == []
    for speaker in ('self', 'mixed', 'unknown'):
        assert assess(intent('Screenshot 1:\n' + text, language, speaker=speaker))['ruleIds'] == []
    request = intent('Screenshot 1:\n' + text, language)
    request['target']['withheldLinks'] = True
    limited = assess(request)
    assert limited['processingOutcome'] == 'partial'
    assert limited['limitationCodes'] == ['WITHHELD_LINKS']


@pytest.mark.parametrize('text', [
    'Screenshot 1:\nI bought a gift card for your birthday.',
    'Screenshot 1:\nGift cards are accepted at our store.',
    'Screenshot 1:\nPay this tax fee by bank transfer and get a receipt.',
    'Screenshot 1:\nPay this tax fee by buying gift cards.',
    'Screenshot 1:\nSend me the numbers and PINs.',
    'Screenshot 1:\nPay this tax fee by buying gift cards and send me the numbers and PINs',
    'Screenshot 1:\nPay this tax fee by buying gift cards and send me the numbers and PINs. Extra context.',
    'Screenshot 2:\nPay this tax fee by buying gift cards and send me the numbers and PINs.',
    'Screenshot 01:\nPay this tax fee by buying gift cards and send me the numbers and PINs.',
    'Screenshot 1: Screenshot 1:\nPay this tax fee by buying gift cards and send me the numbers and PINs.',
    'Warning: Screenshot 1:\nPay this tax fee by buying gift cards and send me the numbers and PINs.',
    'Screenshot 1:\nPague esta multa por transferencia bancaria.',
])
def test_benign_incomplete_contextual_or_other_wrapper_does_not_gain_coverage(text):
    actual = assess(intent(text))
    assert actual['ruleIds'] == []
    assert actual['verdict'] == 'unknown'
    assert actual['processingOutcome'] == 'inconclusive'


def test_ocr_whitespace_is_normalized_without_repairing_recognition_errors():
    text = 'SCREENSHOT\t1:\n PAY this tax fee by buying gift cards and\n send me the numbers and PINs.'
    assert assess(intent(text))['ruleIds'] == ['DEMAND_GIFT_CARD_PAYMENT']
    for changed in (text.replace('gift cards', 'giftcards'), text.replace('PINs', 'P1Ns')):
        assert assess(intent(changed))['ruleIds'] == []
    for changed in (text.replace('\n', '\r\n'), text.replace('tax fee', 'tax​ fee')):
        with pytest.raises(MessageError, match='PRIVACY_REVIEW_REQUIRED'):
            assess(intent(changed))
    # Outer trimming remains a contract requirement, not a server rewrite.
    with pytest.raises(MessageError, match='INPUT_REJECTED'):
        assess(intent(' ' + text + ' '))
