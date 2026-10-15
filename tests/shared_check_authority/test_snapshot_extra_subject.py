"""Synthetic JWT contexts and Moto only; no real accounts, keys or provider calls."""
import json
import os
import sys
import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Isolated authority environment required', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_entitlements import writer_world, world
from shared_check_authority.core import AuthorityError
from v1_entitlements import app
from v1_entitlements.snapshot_gate import trial_access_for_snapshot

OLD = '00000000-0000-4000-8000-000000000001'
EXTRA = '00000000-0000-4000-8000-000000000002'
OTHER = '00000000-0000-4000-8000-000000000003'
ISSUER = 'https://cognito-idp.us-east-1.amazonaws.com/synthetic'
SCOPE = 'aws.cognito.signin.user.admin'


@pytest.fixture
def gate(monkeypatch):
    for key, value in {'STAGE': 'dev', 'V1_ENTITLEMENTS_ENABLED': 'true',
                       'DEV_SUBJECT_ALLOWLIST_JSON': json.dumps([OLD]),
                       'DEV_ACCESS_SNAPSHOT_EXTRA_SUBJECTS_JSON': json.dumps([EXTRA]),
                       'COGNITO_ISSUER': ISSUER, 'COGNITO_APP_CLIENT_ID': 'client',
                       'COGNITO_REQUIRED_SCOPE': SCOPE}.items():
        monkeypatch.setenv(key, value)
    return {'version': '2.0', 'routeKey': 'GET /v1/access', 'headers': {},
            'requestContext': {'http': {'method': 'GET'}, 'authorizer': {'jwt': {'claims': {
                'sub': EXTRA, 'iss': ISSUER, 'client_id': 'client', 'token_use': 'access',
                'scope': SCOPE, 'exp': str(int(time.time()) + 300)}}}}}


def test_original_selection_and_extra_have_different_trial_admission(gate):
    assert trial_access_for_snapshot(gate) is False
    gate['requestContext']['authorizer']['jwt']['claims']['sub'] = OLD
    assert trial_access_for_snapshot(gate) is True


@pytest.mark.parametrize('raw', [None, '', '[]', 'null', '{}', '["*"]', '[true]',
                                  json.dumps([EXTRA, OTHER]), json.dumps([EXTRA, EXTRA]),
                                  json.dumps(['ABCDEFAB-0000-4000-8000-000000000002']), json.dumps(['synthetic-account']),
                                  json.dumps([EXTRA.replace('-', '')]), ' ' * 129])
def test_extra_missing_invalid_multiple_or_noncanonical_fails_closed(gate, monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv('DEV_ACCESS_SNAPSHOT_EXTRA_SUBJECTS_JSON')
    else:
        monkeypatch.setenv('DEV_ACCESS_SNAPSHOT_EXTRA_SUBJECTS_JSON', raw)
    with pytest.raises(AuthorityError, match='^ENGINEERING_ACCESS_UNAVAILABLE$'):
        trial_access_for_snapshot(gate)


def test_invalid_extra_selection_does_not_break_original_subject(gate, monkeypatch):
    monkeypatch.setenv('DEV_ACCESS_SNAPSHOT_EXTRA_SUBJECTS_JSON', 'invalid')
    gate['requestContext']['authorizer']['jwt']['claims']['sub'] = OLD
    assert trial_access_for_snapshot(gate) is True


def test_original_trial_route_keeps_admission_with_extra_config(gate, monkeypatch):
    from types import SimpleNamespace
    gate['requestContext']['authorizer']['jwt']['claims']['sub'] = OLD
    gate['requestContext']['http']['method'] = 'POST'
    gate.update(routeKey='POST /v1/access/trial', body='{"schemaVersion":1,"activate":true}')
    calls = []
    monkeypatch.setattr(app, '_writer', lambda: SimpleNamespace(activate_trial=lambda event: calls.append('trial')))
    monkeypatch.setattr(app, 'access_snapshot', lambda writer, event, **kwargs: {'trialPermitted': kwargs['trial_activation_permitted']})
    result = app.lambda_handler(gate, None)
    assert result['statusCode'] == 200 and calls == ['trial']
    assert json.loads(result['body']) == {'trialPermitted': True}


def test_extra_setting_never_changes_shared_engineering_admission(gate):
    from shared_check_authority.engineering import require_engineering_subject
    with pytest.raises(AuthorityError):
        require_engineering_subject(gate)


@pytest.mark.parametrize('field,value', [('sub', OTHER), ('exp', '1'), ('iss', 'https://attacker.invalid'),
                                        ('client_id', 'other'), ('token_use', 'id'), ('scope', 'other')])
def test_extra_retains_verified_jwt_requirements(gate, field, value):
    gate['requestContext']['authorizer']['jwt']['claims'][field] = value
    with pytest.raises(AuthorityError):
        trial_access_for_snapshot(gate)


@pytest.mark.parametrize('stage', ['uat', 'prod', 'production', ''])
def test_extra_never_admitted_outside_dev(gate, monkeypatch, stage):
    monkeypatch.setenv('STAGE', stage)
    with pytest.raises(AuthorityError):
        trial_access_for_snapshot(gate)


def test_extra_does_not_bypass_invalid_original_selection(gate, monkeypatch):
    monkeypatch.setenv('DEV_SUBJECT_ALLOWLIST_JSON', '[]')
    with pytest.raises(AuthorityError):
        trial_access_for_snapshot(gate)


@pytest.mark.parametrize('route,method', [('POST /v1/access/trial', 'POST'),
                                         ('GET /v1/access', 'POST'), ('POST /v1/access', 'GET'),
                                         ('GET /other', 'GET'), ('GET /v1/access', 'HEAD')])
def test_extra_never_admits_other_routes_or_methods_before_runtime(gate, monkeypatch, route, method):
    gate['routeKey'] = route
    gate['requestContext']['http']['method'] = method
    monkeypatch.setattr(app, '_writer', lambda: pytest.fail('Extra subject reached writer on non-GET route'))
    result = app.lambda_handler(gate, None)
    assert result['statusCode'] == 503
    assert json.loads(result['body'])['error']['code'] == 'ACCESS_SERVICE_UNAVAILABLE'
    with pytest.raises(AuthorityError):
        trial_access_for_snapshot(gate)


def test_client_body_or_header_cannot_select_extra_account(gate, monkeypatch):
    gate['requestContext']['authorizer']['jwt']['claims']['sub'] = OTHER
    gate['headers']['x-account-id'] = EXTRA
    gate['body'] = json.dumps({'accountId': EXTRA})
    monkeypatch.setattr(app, '_writer', lambda: pytest.fail('Client-selected account reached writer'))
    assert app.lambda_handler(gate, None)['statusCode'] == 503


@pytest.fixture
def extra_world(writer_world, gate, monkeypatch):
    writer, values, _, _ = writer_world
    authority = writer.a
    authority.s = replace(authority.s, cognito_issuer=ISSUER, cognito_required_scope=SCOPE)
    # Preserve every original account row; create only the synthetic extra profile/device.
    for table, sk in [('users', 'PROFILE'), ('devices', 'ACTIVE_BINDING'), ('devices', 'DEVICE#device-one')]:
        row = authority.ddb.Table(table).get_item(Key={'PK': 'USER#synthetic-account', 'SK': sk})['Item']
        row = row | {'PK': 'USER#' + EXTRA}
        if 'sub' in row:
            row['sub'] = EXTRA
        if 'accountId' in row:
            row['accountId'] = EXTRA
        authority.ddb.Table(table).put_item(Item=row)
    gate['headers']['x-device-binding-fingerprint'] = 'device-one'
    gate['requestContext']['authorizer']['jwt']['claims']['exp'] = str(values[5][0] + 300)
    monkeypatch.setattr(app, '_writer', lambda: writer)
    return writer, gate


def state(writer):
    return {table: sorted(writer.a.ddb.Table(table).scan()['Items'], key=lambda row: (row['PK'], row['SK']))
            for table in ('users', 'devices', 'deletion', 'authority')}


def test_extra_snapshot_no_grant_no_trial_or_account_device_write(extra_world, monkeypatch):
    writer, event = extra_world
    before = deepcopy(state(writer))
    monkeypatch.setattr(writer.a.client, 'transact_write_items', lambda **kwargs: pytest.fail('Unexpected write'))
    result = app.lambda_handler(event, None)
    body = json.loads(result['body'])
    assert result['statusCode'] == 200 and body['schemaVersion'] == 1
    assert body['activeDevice'] is True
    assert body['trial'] == {'activationAvailable': False, 'activatedAtEpoch': None, 'expiresAtEpoch': None}
    assert body['access']['basis'] == 'none' and body['access']['externalChecksAllowed'] is False
    assert body['allowance']['remaining'] is None
    assert state(writer) == before
    assert EXTRA not in result['body'] and OLD not in result['body']


def test_extra_snapshot_without_binding_returns_inactive_device_no_registration(extra_world):
    writer, event = extra_world
    before = deepcopy(state(writer))
    event['headers'].clear()
    result = app.lambda_handler(event, None)
    body = json.loads(result['body'])
    assert result['statusCode'] == 200 and body['activeDevice'] is False
    assert body['access']['reason'] == 'ACTIVE_DEVICE_REQUIRED'
    assert body['trial']['activationAvailable'] is False
    assert state(writer) == before


def test_existing_extra_trial_dates_and_usage_are_preserved(extra_world):
    writer, event = extra_world
    writer.activate_trial(event)  # Explicit synthetic fixture, never an HTTP extra-subject grant.
    partition = writer.a._partition(EXTRA, writer.a.s.active_key_id)
    access = writer.a.ddb.Table('authority').get_item(Key={'PK': partition, 'SK': 'ACCESS'})['Item']
    table = writer.a.ddb.Table('authority')
    period = table.get_item(Key={'PK': partition, 'SK': 'PERIOD#' + access['periodId']})['Item']
    table.put_item(Item=period | {'usedChecks': 4})
    before = deepcopy(state(writer))
    result = app.lambda_handler(event, None)
    body = json.loads(result['body'])
    assert result['statusCode'] == 200 and body['access']['basis'] == 'trial'
    assert body['allowance']['completedUsed'] == 4 and body['allowance']['remaining'] == 6
    assert body['trial']['activationAvailable'] is False
    assert body['trial']['activatedAtEpoch'] is not None
    assert state(writer) == before


@pytest.mark.parametrize('body,query', [(json.dumps({'accountId': EXTRA}), ''),
                                      ('', 'accountId=' + EXTRA)])
def test_extra_snapshot_strict_empty_request(extra_world, body, query):
    writer, event = extra_world
    before = deepcopy(state(writer))
    event.update(body=body, rawQueryString=query)
    assert app.lambda_handler(event, None)['statusCode'] == 400
    assert state(writer) == before


@pytest.mark.parametrize('fence', ['age', 'status', 'deletion'])
def test_extra_retains_account_and_deletion_fences(extra_world, fence):
    writer, event = extra_world
    if fence == 'deletion':
        writer.a.ddb.Table('deletion').put_item(Item={'PK': 'ACCOUNT#' + EXTRA, 'SK': 'ACCOUNT_DELETION'})
    else:
        table = writer.a.ddb.Table('users')
        row = table.get_item(Key={'PK': 'USER#' + EXTRA, 'SK': 'PROFILE'})['Item']
        row['ageVerified' if fence == 'age' else 'status'] = False if fence == 'age' else 'INACTIVE'
        table.put_item(Item=row)
    before = deepcopy(state(writer))
    assert app.lambda_handler(event, None)['statusCode'] == 403
    assert state(writer) == before
