import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from shared_check_authority import core, prepare_auth_diagnostics as diag
from shared_history.errors import HistoryError

MARKER = 'private-claim-token-body-exception-identity-marker'


def event():
    return {'version': '2.0', 'routeKey': 'POST /v1/purchases/google-play/prepare',
        'requestContext': {'http': {'method': 'POST'}, 'authorizer': {'jwt': {'claims': {
            'sub': MARKER, 'iss': 'issuer', 'client_id': 'client', 'token_use': 'access',
            'exp': 200, 'scope': 'scope'}}}}, 'body': MARKER, 'headers': {'authorization': MARKER}}


@pytest.fixture(autouse=True)
def dev(monkeypatch):
    for key in ['PLAY_PREPARATION_ENABLED', 'AUTHORITY_ENABLED']:
        monkeypatch.setenv(key, 'true')
    monkeypatch.setenv('STAGE', 'dev')


@pytest.mark.parametrize('predicate', sorted(diag.PREDICATES))
def test_fixed_schema_no_private_values(predicate, caplog):
    assert diag.record_failure(event(), predicate)
    assert json.loads(caplog.records[-1].getMessage()) == {
        'event': 'dev_play_prepare_auth_failure', 'schemaVersion': 1,
        'stage': 'AUTHORITY_ACCOUNT_JWT', 'predicate': predicate}
    assert MARKER not in caplog.text


@pytest.mark.parametrize('key,value', [('STAGE', 'uat'), ('STAGE', 'production'), ('STAGE', ''),
    ('PLAY_PREPARATION_ENABLED', 'false'), ('AUTHORITY_ENABLED', 'false')])
def test_no_logging_outside_existing_gates(key, value, monkeypatch, caplog):
    monkeypatch.setenv(key, value)
    assert not diag.record_failure(event(), 'EXPIRY_NOT_FUTURE')
    assert not caplog.records


@pytest.mark.parametrize('change', [{'version': '1.0'}, {'routeKey': 'GET /v1/access'},
    {'routeKey': 'POST /v1/purchases/google-play/verify'}, {'requestContext': MARKER},
    {'requestContext': {'http': {'method': 'GET'}}}, {'requestContext': {'http': MARKER}}])
def test_only_exact_prepare_route(change, caplog):
    assert not diag.record_failure(event() | change, 'EXPIRY_NOT_FUTURE')
    assert not caplog.records


@pytest.mark.parametrize('value', [None, [], {}, True, MARKER])
def test_untrusted_predicate_becomes_only_unknown(value, caplog):
    assert diag.record_failure(event(), value)
    assert json.loads(caplog.records[-1].getMessage())['predicate'] == 'UNKNOWN'
    assert MARKER not in caplog.text


def authority():
    a = object.__new__(core.Authority)
    a.s = SimpleNamespace(cognito_issuer='issuer', cognito_app_client_id='client', cognito_required_scope='scope')
    a.now = Mock(return_value=100)
    a._assert_account = Mock()
    return a


def test_rejected_auth_never_runs_account_storage_and_records_one_predicate(caplog):
    a = authority();e = event();e['requestContext']['authorizer']['jwt']['claims']['exp'] = 100
    with pytest.raises(core.AuthorityError, match='AUTHENTICATION_REQUIRED'):
        a._account(e)
    a._assert_account.assert_not_called();a.now.assert_called_once_with()
    assert len(caplog.records) == 1
    assert json.loads(caplog.records[0].getMessage())['predicate'] == 'EXPIRY_NOT_FUTURE'
    assert MARKER not in caplog.text


def test_success_and_profile_failure_do_not_log_auth(caplog):
    a = authority();e = event()
    assert a._account(e) == MARKER
    a._assert_account.assert_called_once_with(MARKER)
    a._assert_account.side_effect = core.AuthorityError('ACCOUNT_UNAVAILABLE')
    with pytest.raises(core.AuthorityError, match='ACCOUNT_UNAVAILABLE'):
        a._account(e)
    assert not caplog.records


def test_logger_and_hook_exceptions_never_change_auth_failure(monkeypatch):
    a = authority();e = event();e['requestContext']['authorizer']['jwt']['claims']['exp'] = 100
    original = diag.record_failure
    monkeypatch.setattr(diag, 'record_failure', Mock(side_effect=RuntimeError(MARKER)))
    with pytest.raises(core.AuthorityError, match='AUTHENTICATION_REQUIRED'):
        a._account(e)
    monkeypatch.setattr(diag, 'record_failure', original)
    monkeypatch.setattr(logging.getLogger(diag.__name__), 'warning', Mock(side_effect=RuntimeError(MARKER)))
    assert not diag.record_failure(event(), 'EXPIRY_NOT_FUTURE')
    with pytest.raises(core.AuthorityError, match='AUTHENTICATION_REQUIRED'):
        a._account(e)
    a._assert_account.assert_not_called()


def test_unknown_parser_exception_does_not_inspect_exception_text(monkeypatch, caplog):
    monkeypatch.setattr(core, 'jwt_subject', Mock(side_effect=TypeError(MARKER)))
    with pytest.raises(core.AuthorityError, match='AUTHENTICATION_REQUIRED'):
        authority()._account(event())
    assert json.loads(caplog.records[-1].getMessage())['predicate'] == 'UNKNOWN'
    assert MARKER not in caplog.text


def test_prepare_expiry_between_gates_preserves_401_without_storage_or_retry(monkeypatch, caplog):
    from shared_play_lifecycle import preparation
    from shared_check_authority import engineering, runtime
    e = event();e['body'] = '{"schemaVersion":1}'
    claims = e['requestContext']['authorizer']['jwt']['claims']
    claims['sub'] = '00000000-0000-0000-0000-000000000001'
    claims['scope'] = 'aws.cognito.signin.user.admin';claims['exp'] = 101
    a = authority();a.s.cognito_required_scope = claims['scope'];a.s.authority_table = 'authority'
    a.now.return_value = 101
    monkeypatch.setattr(engineering.time, 'time', lambda: 100)
    for key, value in {'COGNITO_ISSUER':'issuer', 'COGNITO_APP_CLIENT_ID':'client',
        'COGNITO_REQUIRED_SCOPE':claims['scope'], 'DEV_SUBJECT_ALLOWLIST_JSON':json.dumps([claims['sub']]),
        'PLAY_TOKEN_TABLE_NAME':'tokens', 'AUTHORITY_TABLE_NAME':'authority'}.items():
        monkeypatch.setenv(key, value)
    # Engineering's issuer format guard is retained. Use its valid synthetic shape.
    claims['iss'] = 'https://cognito-idp.synthetic/pool';a.s.cognito_issuer = claims['iss']
    monkeypatch.setenv('COGNITO_ISSUER', claims['iss'])
    load = Mock(return_value=a);monkeypatch.setattr(runtime, 'load_authority', load)
    result = preparation.handle(e)
    assert result['statusCode'] == 401
    assert json.loads(result['body']) == {'schemaVersion':1, 'contractVersion':preparation.CONTRACT,
        'error':{'code':'AUTHENTICATION_REQUIRED', 'retryable':False}}
    assert load.call_count == 1
    a._assert_account.assert_not_called();a.now.assert_called_once_with()
    assert len(caplog.records) == 1
    assert json.loads(caplog.records[0].getMessage())['predicate'] == 'EXPIRY_NOT_FUTURE'
    assert MARKER not in caplog.text


def test_prepare_engineering_reject_remains_403_without_account_diagnostic(monkeypatch, caplog):
    from shared_play_lifecycle import preparation
    from shared_check_authority import engineering, runtime
    monkeypatch.setattr(engineering, 'require_engineering_subject', Mock(side_effect=core.AuthorityError('ENGINEERING_ACCESS_UNAVAILABLE')))
    load = Mock();monkeypatch.setattr(runtime, 'load_authority', load)
    result = preparation.handle(event())
    assert result['statusCode'] == 403
    assert json.loads(result['body'])['error'] == {'code':'ACCESS_UNAVAILABLE','retryable':False}
    load.assert_not_called();assert not caplog.records
