"""Documented response metadata is separate from bounded model assessment text.

All HTTP is in memory. These constructed envelopes prove transport compatibility,
not model compliance or live qualification.
"""
import json
import socket

import pytest

from message_evaluator import ai_provider as ai, proposer as p
from message_evaluator.usage import usage_report
from shared_message_contract.validation import MessageError
from . import test_proposer as h


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*_, **__):
        pytest.fail('No real network or credentials in envelope regressions')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)


def envelope():
    return {
        'status': 'completed', 'model': ai.REASONING_MODEL,
        'error': None, 'incomplete_details': None,
        'reasoning': {'effort': 'low', 'summary': None},
        'text': {'format': {'type': 'json_schema', 'name': ai.FORMAT_NAME,
                            'strict': True, 'schema': ai.SCHEMA}},
        'max_output_tokens': 2048,
        'output': [{'type': 'message', 'role': 'assistant', 'status': 'completed',
                    'content': [{'type': 'output_text', 'text': json.dumps({'result': {
                        'assessment': 'abstain', 'context': 'insufficient', 'reasons': []}})}]}],
        'usage': {'input_tokens': 2000, 'output_tokens': 100, 'total_tokens': 2100,
                  'input_tokens_details': {'cached_tokens': 0},
                  'output_tokens_details': {'reasoning_tokens': 50}},
        'service_tier': 'default', 'tools': [], 'store': False, 'background': False,
    }


def deliver(payload, *, framing='length', declared=None, parser=lambda raw, text: raw):
    if framing == 'length':
        header = b'Content-Length: ' + (declared or str(len(payload)).encode()) + b'\r\n'
        body = payload
    elif framing == 'chunked':
        header = b'Transfer-Encoding: chunked\r\n'
        body = hex(len(payload))[2:].encode() + b'\r\n' + payload + b'\r\n0\r\n\r\n'
    else:
        header, body = b'', payload
    data = b'HTTP/1.1 200 Test\r\n' + header + b'\r\n' + body
    connections, stages = [], []
    def connection(*args, **kwargs):
        value = h.Connection(data, *args, **kwargs)
        connections.append(value)
        return value
    try:
        result = p.propose(h.intent(), h.settings(), 1000, clock=lambda: 0.,
            connection_factory=connection, secret_loader=lambda *_: 'unit-noncredential',
            response_parser=parser, on_transport_rejection=stages.append)
        return result, stages
    finally:
        assert len(connections) == 1 and connections[0].test_socket.closed


@pytest.mark.parametrize('formatting', [{}, {'indent': 2}])
def test_current_schema_echo_reaches_strict_parser_and_usage(formatting):
    raw = json.dumps(envelope(), **formatting).encode()
    if formatting:
        assert len(raw) == 32392 and len(raw) > 16384
    parsed, stages = deliver(raw, parser=lambda value, text: ai.parse(value, text,
        expected_model=ai.REASONING_MODEL, reasoning_effort='low'))
    assert parsed == {'assessment': 'abstain', 'context': 'insufficient', 'reasons': []}
    assert stages == []
    report = usage_report(raw, ai.REASONING_MODEL, (750, 4500))
    assert report['failureReason'] is None and report['usage']['outputTokens'] == 100


@pytest.mark.parametrize('framing', ['length', 'chunked', 'close'])
@pytest.mark.parametrize('length', [1, 16384, 16385, 65535, 65536])
def test_inclusive_body_bounds_with_every_supported_framing(framing, length):
    raw = b'x' * length
    actual, stages = deliver(raw, framing=framing)
    assert actual == raw and stages == []


@pytest.mark.parametrize('framing,stage', [('length', 'CONTENT_LENGTH_OVER_LIMIT'),
    ('chunked', 'BODY_BOUNDS'), ('close', 'BODY_BOUNDS')])
def test_one_byte_over_envelope_limit_never_reaches_callback(framing, stage):
    stages = []
    original = p._transport_rejected
    # Capture only a closed diagnostic, including when the call raises.
    def observe(observer, value):
        stages.append(value)
        original(observer, value)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(p, '_transport_rejected', observe)
        with pytest.raises(MessageError):
            deliver(b'x' * 65537, framing=framing,
                    parser=lambda *_: pytest.fail('Oversized body reached parser'))
    assert stages == [stage]


@pytest.mark.parametrize('declared,stage', [
    (b'0', 'CONTENT_LENGTH_ZERO'), (b'000', 'CONTENT_LENGTH_ZERO'),
    (b'-1', 'CONTENT_LENGTH_VALUE'), (b'+1', 'CONTENT_LENGTH_VALUE'),
    (b'1_0', 'CONTENT_LENGTH_VALUE'), (b'1, 1', 'CONTENT_LENGTH_VALUE'),
    (b'', 'CONTENT_LENGTH_VALUE'), (b'1.0', 'CONTENT_LENGTH_VALUE'),
    (b'65537', 'CONTENT_LENGTH_OVER_LIMIT'),
    (b'9' * 5000, 'CONTENT_LENGTH_OVER_LIMIT'),
], ids=['zero', 'leading-zero-only', 'negative', 'plus-sign', 'underscore',
        'comma-list', 'empty', 'decimal-point', 'over-limit', 'huge-decimal'])
def test_declared_length_diagnostics_are_finite_and_decimal_only(monkeypatch, declared, stage):
    stages = []
    original = p._transport_rejected
    def observe(observer, value):
        stages.append(value)
        original(observer, value)
    monkeypatch.setattr(p, '_transport_rejected', observe)
    # An explicit empty field must not be replaced by a helper's default length.
    data = b'HTTP/1.1 200 Test\r\nContent-Length: ' + declared + b'\r\n\r\nx'
    def factory(*args, **kwargs): return h.Connection(data, *args, **kwargs)
    with pytest.raises(MessageError):
        p.propose(h.intent(), h.settings(), 1000, clock=lambda: 0.,
            connection_factory=factory, secret_loader=lambda *_: 'unit-noncredential',
            response_parser=lambda *_: pytest.fail('Invalid length reached parser'))
    assert stages == [stage]


@pytest.mark.parametrize('declared', [b'1', b'0001', b'\t0001 \t', b'0' * 5000 + b'1'],
                         ids=['one', 'leading-zeros', 'ows', 'many-leading-zeros'])
def test_decimal_leading_zeros_and_optional_http_whitespace(declared):
    assert deliver(b'x', declared=declared) == (b'x', [])


@pytest.mark.parametrize('length', [16384, 16385])
def test_model_text_keeps_its_original_byte_cap(length):
    value = envelope()
    text = value['output'][0]['content'][0]['text']
    value['output'][0]['content'][0]['text'] = text + ' ' * (length - len(text.encode()))
    raw = json.dumps(value).encode()
    if length == 16384:
        assert ai.parse(raw, h.intent()['target']['sanitizedText'])['assessment'] == 'abstain'
    else:
        stages = []
        with pytest.raises(MessageError): ai.parse(raw, 'text', on_rejection=stages.append)
        assert stages == ['ASSESSMENT_TEXT_BOUNDS']


def test_larger_metadata_does_not_accept_ungrounded_or_ambiguous_evidence():
    value = envelope()
    value['output'][0]['content'][0]['text'] = json.dumps({'result': {
        'assessment': 'warning', 'context': 'clear', 'reasons': [
            {'code': 'AI_PRETEXT', 'quotes': ['fabricated'],
             'support': {'claimOrSituation': 0, 'consequentialConnection': 0}}]}})
    stages = []
    with pytest.raises(MessageError):
        ai.parse(json.dumps(value, indent=2).encode(), 'Unrelated text', on_rejection=stages.append)
    assert stages == ['EVIDENCE_NOT_FOUND']


def test_usage_and_both_parsers_reject_one_byte_over_outer_limit():
    raw = b' ' * 65537
    assert usage_report(raw, ai.REASONING_MODEL, (750, 4500)) == {
        'usage': None, 'failureReason': 'INVALID_JSON'}
    for parser in (p.parse, ai.parse):
        with pytest.raises(MessageError): parser(raw, 'text')


@pytest.mark.parametrize('length', [65535, 65536])
def test_usage_and_both_parsers_share_inclusive_envelope_bound(length):
    value = json.dumps(envelope()).encode()
    raw = value + b' ' * (length - len(value))
    assert ai.parse(raw, 'text')['assessment'] == 'abstain'
    assert usage_report(raw, ai.REASONING_MODEL, (750, 4500))['failureReason'] is None
    legacy = json.dumps(h.response()).encode()
    assert p.parse(legacy + b' ' * (length - len(legacy)), 'text')['ruleId'] == 'UNRESOLVED'


@pytest.mark.parametrize('parser', [p.parse, ai.parse])
def test_empty_envelopes_still_reject(parser):
    with pytest.raises(MessageError): parser(b'', 'text')


@pytest.mark.parametrize('headers', [b'Content-Encoding: gzip\r\n',
    b'Content-Length: 1\r\n', b'Transfer-Encoding: chunked\r\n'])
def test_compression_and_ambiguous_framing_still_reject(headers):
    with pytest.raises(MessageError): h.call(h.wire(headers=headers))


def test_legacy_proposal_accepts_metadata_but_preserves_model_text_limit():
    value = h.response()
    value['text'] = envelope()['text']
    assert p.parse(json.dumps(value, indent=2).encode(), 'text') == {'ruleId': 'UNRESOLVED', 'spans': []}
    value['output'][0]['content'][0]['text'] += ' ' * 16384
    with pytest.raises(MessageError): p.parse(json.dumps(value).encode(), 'text')


def test_wire_cap_still_includes_headers_and_chunk_framing():
    assert p.MAX_ENVELOPE_BYTES == 65536 and p.MAX_RESPONSE_BYTES == 16384
    assert p.MAX_WIRE_BYTES == 81920
    headers = (b'X-Large: ' + b'x' * 4096 + b'\r\n') * 21
    raw = b'HTTP/1.1 200 Test\r\n' + headers + b'\r\n{}'
    with pytest.raises(MessageError): h.call(raw)
