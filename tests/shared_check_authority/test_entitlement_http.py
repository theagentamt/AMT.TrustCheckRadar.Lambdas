import os
import sys
from pathlib import Path
import json
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run isolated authority emulator suite', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_entitlements import writer_world, world, OPERATOR, ACCOUNT
from v1_entitlements import app

@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setenv('V1_ENTITLEMENTS_ENABLED', 'true')


def event_for(event, route='GET /v1/access', body=None):
    event.update(version='2.0', routeKey=route, rawQueryString='', isBase64Encoded=False)
    event['requestContext']['http'] = {'method': route.split()[0]}
    if body is not None:
        event['body'] = body
    return event


def test_trial_requires_explicit_boolean_not_string(writer_world, monkeypatch):
    writer, world, _, _ = writer_world
    monkeypatch.setattr(app, '_writer', lambda: writer)
    event = event_for(world[1], 'POST /v1/access/trial', '{"schemaVersion":1,"activate":"true"}')
    result = app.lambda_handler(event, None)
    assert result['statusCode'] == 400
    assert world[3]('TRIAL_HISTORY') is None


@pytest.mark.parametrize('body', ['{"schemaVersion":1,"activate":true,"activate":false}',
                                '{"schemaVersion":true,"activate":true}',
                                '{"schemaVersion":1,"activate":true,"paid":true}', '[]', 'x' * 257])
def test_trial_strict_request(writer_world, monkeypatch, body):
    writer, world, _, _ = writer_world
    monkeypatch.setattr(app, '_writer', lambda: writer)
    result = app.lambda_handler(event_for(world[1], 'POST /v1/access/trial', body), None)
    assert result['statusCode'] == 400 and world[3]('ACCESS') is None


def test_direct_invocation_without_verified_gateway_context_rejected():
    result = app.lambda_handler({'accountId': ACCOUNT, 'activate': True}, None)
    assert result['statusCode'] == 400


def test_get_snapshot_requires_real_verified_context(writer_world, monkeypatch):
    writer, world, _, _ = writer_world
    monkeypatch.setattr(app, '_writer', lambda: writer)
    event = event_for(world[1])
    del event['requestContext']['authorizer']
    result = app.lambda_handler(event, None)
    assert result['statusCode'] == 401


def test_get_empty_access_can_activate_trial(writer_world, monkeypatch):
    writer, world, _, _ = writer_world
    monkeypatch.setattr(app, '_writer', lambda: writer)
    result = app.lambda_handler(event_for(world[1]), None)
    assert result['statusCode'] == 200
    body = json.loads(result['body'])
    assert body['access'] == {'basis': 'none', 'externalChecksAllowed': False, 'reason': 'EXTERNAL_ACCESS_UNAVAILABLE'}
    assert body['trial']['activationAvailable'] is True and body['allowance']['limit'] is None
    assert result['headers']['Cache-Control'] == 'no-store'


def test_successful_trial_response_and_no_client_account_selection(writer_world, monkeypatch):
    writer, world, _, _ = writer_world
    monkeypatch.setattr(app, '_writer', lambda: writer)
    result = app.lambda_handler(event_for(world[1], 'POST /v1/access/trial', '{"schemaVersion":1,"activate":true}'), None)
    assert result['statusCode'] == 200
    body = json.loads(result['body'])
    assert body['access']['basis'] == 'trial' and body['access']['externalChecksAllowed'] is True
    assert body['allowance'] == {'limit': 10, 'completedUsed': 0, 'reserved': 0, 'remaining': 10,
                                 'periodEndsAtEpoch': world[5][0] + 7 * 86400}
    assert body['trial']['activationAvailable'] is False
    assert ACCOUNT not in result['body']


def test_snapshot_exhaustion_exposes_counters_and_denies_work(writer_world, monkeypatch):
    writer, world, _, _ = writer_world
    monkeypatch.setattr(app, '_writer', lambda: writer)
    writer.activate_trial(world[1])
    period = world[3]('ACCESS')['periodId']
    world[4]('authority', 'PERIOD#' + period, usedChecks=9, reservedChecks=1)
    result = app.lambda_handler(event_for(world[1]), None)
    assert result['statusCode'] == 200
    body = json.loads(result['body'])
    assert body['access']['reason'] == 'ALLOWANCE_EXHAUSTED' and not body['access']['externalChecksAllowed']
    assert body['allowance']['remaining'] == 0 and body['allowance']['reserved'] == 1


def test_snapshot_missing_active_device_does_not_switch(writer_world, monkeypatch):
    writer, world, _, _ = writer_world
    monkeypatch.setattr(app, '_writer', lambda: writer)
    world[1]['headers'].clear()
    result = app.lambda_handler(event_for(world[1]), None)
    assert result['statusCode'] == 200
    body = json.loads(result['body'])
    assert body['activeDevice'] is False and body['access']['reason'] == 'ACTIVE_DEVICE_REQUIRED'
    assert body['trial']['activationAvailable'] is False
    assert world[3]('ACCESS') is None


def test_service_failure_has_no_exception_details_or_retry_promise(monkeypatch):
    def fail():
        raise RuntimeError('synthetic-secret-value')
    monkeypatch.setattr(app, '_writer', fail)
    event = {'version': '2.0', 'routeKey': 'GET /v1/access', 'requestContext': {'http': {'method': 'GET'}}}
    result = app.lambda_handler(event, None)
    assert result['statusCode'] == 503 and 'synthetic-secret-value' not in result['body']
    assert json.loads(result['body'])['error']['retryable'] is False


def test_mobile_entitlement_gate_defaults_closed(monkeypatch):
    monkeypatch.delenv('V1_ENTITLEMENTS_ENABLED', raising=False)
    assert app.lambda_handler({}, None)['statusCode'] == 503


def test_trial_retention_gate_disables_snapshot_action_and_activation(writer_world, monkeypatch):
    writer, world, _, _ = writer_world
    writer.trial_retention_approved = False
    monkeypatch.setattr(app, '_writer', lambda: writer)
    result = app.lambda_handler(event_for(world[1]), None)
    assert result['statusCode'] == 200
    assert json.loads(result['body'])['trial']['activationAvailable'] is False
    result = app.lambda_handler(event_for(world[1], 'POST /v1/access/trial', '{"schemaVersion":1,"activate":true}'), None)
    assert result['statusCode'] == 503 and world[3]('TRIAL_HISTORY') is None
