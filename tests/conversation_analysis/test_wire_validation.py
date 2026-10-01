"""Legacy /analysis sizing: real wire bytes, strict API Gateway encoding."""
import base64
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src/conversation_analysis'), str(ROOT / 'src')]
from validation import parse_and_validate_event
from errors import AppError


def payload(text='Safe text'):
    return {'schemaVersion': '1.0', 'requestId': 'wire-example', 'sourceType': 'pasted_text',
            'localSanitizationApplied': True, 'sanitizedText': text, 'entities': []}


def wire(value, **kwargs):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), **kwargs)


@pytest.mark.parametrize('base64_encoded', [False, True])
@pytest.mark.parametrize('size', [65535, 65536, 65537])
def test_actual_wire_boundaries_include_whitespace(size, base64_encoded):
    raw = wire(payload('😀')).encode()
    raw += b' ' * (size - len(raw))
    body = base64.b64encode(raw).decode() if base64_encoded else raw.decode()
    event = {'body': body, 'isBase64Encoded': base64_encoded}
    if size <= 65536:
        assert parse_and_validate_event(event)['sanitizedText'] == '😀'
    else:
        with pytest.raises(AppError) as err:
            parse_and_validate_event(event)
        assert err.value.code == 'INVALID_REQUEST'


def test_six_thousand_emoji_uses_actual_encoding_not_reserialization():
    value = payload('😀' * 6000)
    assert len(wire(value).encode()) < 65536 < len(json.dumps(value).encode())
    assert parse_and_validate_event({'body': wire(value)})['sanitizedText'] == value['sanitizedText']
    assert parse_and_validate_event({'body': value})['sanitizedText'] == value['sanitizedText']
    with pytest.raises(AppError):
        parse_and_validate_event({'body': json.dumps(value)})


@pytest.mark.parametrize('symbol', ['a', '😀', '\u0301'])
@pytest.mark.parametrize('size', [7999, 8000, 8001])
def test_codepoint_boundary(symbol, size):
    value = payload(symbol * size)
    if size <= 8000:
        assert len(parse_and_validate_event({'body': wire(value)})['sanitizedText']) == size
    else:
        with pytest.raises(AppError):
            parse_and_validate_event({'body': wire(value)})


@pytest.mark.parametrize('text,expected', [(' \tá\n', 'á'), ('\u00a0x\u00a0', 'x'),
                                         ('e\u0301', 'e\u0301'), ('👩\u200d💻', '👩\u200d💻')])
def test_legacy_trim_does_not_normalize_or_apply_governed_controls(text, expected):
    assert parse_and_validate_event({'body': wire(payload(text))})['sanitizedText'] == expected


@pytest.mark.parametrize('event', [
    {'body': '!!!!', 'isBase64Encoded': True},
    {'body': 'e30=\n', 'isBase64Encoded': True},
    {'body': '/w==', 'isBase64Encoded': True},
    {'body': '{}', 'isBase64Encoded': 'true'},
    {'body': {}, 'isBase64Encoded': True},
    {'body': '['}, {'body': '\ud800'},
    {'body': json.dumps(payload('\ud800'))},
    {'body': payload('\ud800')},
    {'body': '[' * 2000},
])
def test_invalid_transport_is_closed_client_error(event, capsys):
    with pytest.raises(AppError) as err:
        parse_and_validate_event(event)
    assert err.value.code == 'INVALID_REQUEST' and err.value.retryable is False
    assert not capsys.readouterr().out


@pytest.mark.parametrize('text', ['', ' \t\r\n', '\u00a0'])
def test_blank_text_rejected(text):
    with pytest.raises(AppError):
        parse_and_validate_event({'body': wire(payload(text))})


@pytest.mark.parametrize('size', [0, 100, 101])
def test_legacy_entities_duplicate_entries_count(size):
    value = payload() | {'entities': [{'token': '[EMAIL_1]', 'type': 'email'}] * size}
    if size <= 100:
        assert len(parse_and_validate_event({'body': wire(value)})['entities']) == size
    else:
        with pytest.raises(AppError):
            parse_and_validate_event({'body': wire(value)})


@pytest.mark.parametrize('source', ['pasted_text', 'ocr', 'mixed', 'screenshot_ocr', 'combined'])
def test_source_values_preserved(source):
    value = payload() | {'sourceType': source}
    if source in ('pasted_text', 'ocr', 'mixed'):
        assert parse_and_validate_event({'body': wire(value)})['sourceType'] == source
    else:
        with pytest.raises(AppError):
            parse_and_validate_event({'body': wire(value)})


def test_published_legacy_examples_schema_and_runtime_agree():
    from jsonschema import Draft202012Validator
    directory=ROOT/'contracts/analysis/1.0-wire-v1'
    schema=json.loads((directory/'request.schema.json').read_text())
    Draft202012Validator.check_schema(schema)
    validator=Draft202012Validator(schema)
    for value in json.loads((directory/'request-fixtures.json').read_text()):
        validator.validate(value)
        parsed=parse_and_validate_event({'body':wire(value)})
        assert parsed['sanitizedText']==value['sanitizedText']
        assert parsed['sourceType']==value['sourceType']
    assert (directory/'app_features_reference.py').read_bytes()==(ROOT/'src/shared_campaign_contracts/app_features.py').read_bytes()


def test_wire_representation_preserves_logical_identity_and_source():
    value=payload('á 😀 e\u0301')|{'sourceType':'mixed'}
    raw=wire(value)
    projections=[parse_and_validate_event(event) for event in (
        {'body':raw},{'body':json.dumps(value)}, {'body':value},
        {'body':base64.b64encode(raw.encode()).decode(),'isBase64Encoded':True})]
    assert all(v==projections[0] for v in projections)


def test_published_error_examples_match_real_nonretryable_responses():
    from response_builders import build_error_response
    fixtures=json.loads((ROOT/'contracts/analysis/1.0-wire-v1/error-fixtures.json').read_text())
    events=[{'body':'!','isBase64Encoded':True},{'body':wire(payload()|{'schemaVersion':'9'})}]
    for event,expected in zip(events,fixtures,strict=True):
        with pytest.raises(AppError) as caught:parse_and_validate_event(event)
        assert {'httpStatus':caught.value.status_code,'body':build_error_response(request_id=None,err=caught.value)}==expected
