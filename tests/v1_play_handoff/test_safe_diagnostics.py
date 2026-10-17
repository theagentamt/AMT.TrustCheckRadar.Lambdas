import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from v1_play_handoff import app, diagnostics
from shared_play_verification.proof import PlayVerificationError

MARKER = 'private-proof-identity-body-exception-marker'
EVENT = {'routeKey': 'POST /v1/purchases/google-play/verify',
         'requestContext': {'http': {'method': 'POST'}}, 'body': MARKER}


@pytest.fixture(autouse=True)
def dev(monkeypatch):
    monkeypatch.setenv('STAGE', 'dev')
    monkeypatch.setenv('PLAY_HANDOFF_ENABLED', 'true')


@pytest.mark.parametrize('code', sorted(app.ERRORS))
def test_public_code_status_only_and_no_private_event_values(code, caplog):
    status = app.ERRORS[code][0]
    with caplog.at_level(logging.WARNING):
        assert diagnostics.record_failure(EVENT, code, status, 'VERIFICATION')
    value = json.loads(caplog.records[-1].getMessage())
    assert value == {'event': 'dev_play_verification_failure', 'schemaVersion': 1,
                     'errorCode': code, 'httpStatus': status, 'stage': 'VERIFICATION'}
    assert MARKER not in caplog.text
    assert diagnostics._STATUSES == {key: pair[0] for key, pair in app.ERRORS.items()}


@pytest.mark.parametrize('stage', ['ADMISSION', 'REQUEST_VALIDATION', 'RUNTIME_INITIALIZATION', 'VERIFICATION'])
def test_stage_is_fixed(stage, caplog):
    assert diagnostics.record_failure(EVENT, 'REQUEST_CONFLICT', 409, stage)
    assert json.loads(caplog.records[-1].getMessage())['stage'] == stage


@pytest.mark.parametrize('stage', ['uat', 'production', '', None])
def test_non_dev_has_no_new_log(stage, monkeypatch, caplog):
    if stage is None:
        monkeypatch.delenv('STAGE')
    else:
        monkeypatch.setenv('STAGE', stage)
    assert not diagnostics.record_failure(EVENT, 'REQUEST_CONFLICT', 409, 'VERIFICATION')
    assert not caplog.records


@pytest.mark.parametrize('event', [None, {}, {'routeKey': EVENT['routeKey'], 'requestContext': MARKER},
    {'routeKey': EVENT['routeKey'], 'requestContext': {'http': MARKER}},
    {'routeKey': 'POST /v1/purchases/google-play/prepare', 'requestContext': EVENT['requestContext']},
    {'routeKey': EVENT['routeKey'], 'requestContext': {'http': {'method': 'GET'}}}])
def test_other_or_malformed_routes_never_log(event, caplog):
    assert not diagnostics.record_failure(event, 'REQUEST_CONFLICT', 409, 'VERIFICATION')
    assert not caplog.records


def test_closed_gate_never_logs(monkeypatch, caplog):
    monkeypatch.setenv('PLAY_HANDOFF_ENABLED', 'false')
    assert not diagnostics.record_failure(EVENT, 'REQUEST_CONFLICT', 409, 'VERIFICATION')
    assert not caplog.records


@pytest.mark.parametrize('code,status,stage', [(MARKER, 409, MARKER), ([], [], []),
    ('REQUEST_CONFLICT', True, 'VERIFICATION'), ('REQUEST_CONFLICT', 503, 'VERIFICATION')])
def test_hostile_values_cannot_escape_fixed_schema(code, status, stage, caplog):
    assert diagnostics.record_failure(EVENT, code, status, stage)
    value = json.loads(caplog.records[-1].getMessage())
    assert value['httpStatus'] == 0
    assert MARKER not in json.dumps(value)
    assert set(value) == {'event', 'schemaVersion', 'errorCode', 'httpStatus', 'stage'}


def test_logging_failure_is_suppressed(monkeypatch):
    monkeypatch.setattr(logging.getLogger(diagnostics.__name__), 'warning', Mock(side_effect=RuntimeError(MARKER)))
    assert not diagnostics.record_failure(EVENT, 'REQUEST_CONFLICT', 409, 'VERIFICATION')


def request():
    return {**EVENT, 'version': '2.0', 'body': json.dumps({'schemaVersion': 1,
        'requestId': '00000000-0000-0000-0000-000000000001', 'intent': 'PURCHASE',
        'purchaseToken': 'synthetic-purchase-token'})}


@pytest.mark.parametrize('code', sorted(app.ERRORS))
def test_handler_response_and_single_service_call_unchanged_when_logging_fails(code, monkeypatch):
    monkeypatch.setattr('shared_check_authority.engineering.require_engineering_subject', lambda event: None)
    client = SimpleNamespace(close=Mock())
    handle = Mock(side_effect=PlayVerificationError(code))
    runtime = Mock(return_value=SimpleNamespace(client=client, token_cipher=None, handle=handle))
    monkeypatch.setattr(app, '_runtime', runtime)
    monkeypatch.setattr(diagnostics, 'record_failure', Mock(side_effect=RuntimeError(MARKER)))
    event = request()
    result = app.lambda_handler(event, None)
    assert result['statusCode'] == app.ERRORS[code][0]
    assert json.loads(result['body']) == {'schemaVersion': 1, 'contractVersion': app.CONTRACT,
        'requestId': json.loads(event['body'])['requestId'],
        'error': {'code': code, 'retryable': app.ERRORS[code][1]}}
    assert handle.call_count == runtime.call_count == client.close.call_count == 1


def test_handler_stage_and_no_additional_runtime_calls(monkeypatch, caplog):
    monkeypatch.setattr('shared_check_authority.engineering.require_engineering_subject', lambda event: None)
    runtime = Mock(side_effect=RuntimeError(MARKER))
    monkeypatch.setattr(app, '_runtime', runtime)
    result = app.lambda_handler(request(), None)
    assert result['statusCode'] == 503 and runtime.call_count == 1
    assert json.loads(caplog.records[-1].getMessage())['stage'] == 'RUNTIME_INITIALIZATION'
    assert MARKER not in caplog.text


def test_success_does_not_emit_failure(monkeypatch, caplog):
    monkeypatch.setattr('shared_check_authority.engineering.require_engineering_subject', lambda event: None)
    client = SimpleNamespace(close=Mock())
    result = {'fixed': 'success'}
    monkeypatch.setattr(app, '_runtime', lambda: SimpleNamespace(client=client, token_cipher=None, handle=lambda *args: result))
    assert json.loads(app.lambda_handler(request(), None)['body']) == result
    assert not caplog.records
