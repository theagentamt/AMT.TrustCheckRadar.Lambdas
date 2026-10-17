"""Authentication differential checks; synthetic claims only, never a token."""
import copy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from shared_history import security
from shared_history.errors import HistoryError

SETTINGS = SimpleNamespace(cognito_issuer='synthetic-issuer', cognito_app_client_id='synthetic-client',
                           cognito_required_scope='synthetic-scope')


def event(**overrides):
    claims = {'sub': 'synthetic-subject', 'iss': SETTINGS.cognito_issuer,
              'client_id': SETTINGS.cognito_app_client_id, 'token_use': 'access',
              'exp': '200', 'scope': SETTINGS.cognito_required_scope}
    claims.update(overrides)
    return {'requestContext': {'authorizer': {'jwt': {'claims': claims}}}}


@pytest.mark.parametrize('changes,predicate', [
    ({'sub': ''}, 'SUBJECT_INVALID'), ({'sub': []}, 'SUBJECT_INVALID'),
    ({'iss': 'other'}, 'ISSUER_MISMATCH'), ({'client_id': 'other'}, 'CLIENT_MISMATCH'),
    ({'token_use': 'id'}, 'TOKEN_USE_MISMATCH'), ({'exp': True}, 'EXPIRY_INVALID'),
    ({'exp': 'not-an-epoch'}, 'EXPIRY_INVALID'), ({'exp': 0}, 'EXPIRY_INVALID'),
    ({'exp': 100}, 'EXPIRY_NOT_FUTURE'), ({'exp': 99}, 'EXPIRY_NOT_FUTURE'),
    ({'scope': 'other'}, 'SCOPE_MISSING'), ({'scope': []}, 'SCOPE_MISSING'),
])
def test_first_failed_predicate_attached_without_response_change(changes, predicate):
    with pytest.raises(HistoryError) as raised:
        security.jwt_subject(event(**changes), SETTINGS, now=lambda: 100)
    error = raised.value
    assert error._jwt_auth_predicate == predicate
    assert (error.code, error.message, error.retryable, error.details, error.status_code) == (
        'UNAUTHORIZED', 'A verified Cognito access token is required.', False, [], 401)
    assert not any(value in str(error) for value in ['synthetic-subject', 'synthetic-client', predicate])


def test_unavailable_context_and_short_circuit_clock_order():
    clock = Mock(return_value=100)
    with pytest.raises(HistoryError) as raised:
        security.jwt_subject({}, SETTINGS, now=clock)
    assert raised.value._jwt_auth_predicate == 'CLAIMS_UNAVAILABLE'
    clock.assert_not_called()
    with pytest.raises(HistoryError) as raised:
        security.jwt_subject(event(sub='', iss='other', exp=99), SETTINGS, now=clock)
    assert raised.value._jwt_auth_predicate == 'SUBJECT_INVALID'
    clock.assert_not_called()
    assert security.jwt_subject(event(), SETTINGS, now=clock) == 'synthetic-subject'
    clock.assert_called_once_with()


def load_baseline():
    spec = importlib.util.spec_from_file_location('shared_history.baseline_auth',
        ROOT / 'tests/shared_history/fixtures/dev-live8-security.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('key', ['sub', 'iss', 'client_id', 'token_use', 'exp', 'scope'])
def test_differential_original_value_cases_and_clock_calls(key):
    baseline = load_baseline()
    values = [None, True, False, 0, 100, 101, 200, 1.5, '', '100', '200', '\u00b2',
              [], {}, '  synthetic-subject  ', 'synthetic-issuer', 'synthetic-client',
              'access', 'synthetic-scope', 'other']
    for value in values:
        results = []
        for module in [baseline, security]:
            clock = Mock(return_value=100)
            try:
                result = ('ok', module.jwt_subject(copy.deepcopy(event(**{key: value})), SETTINGS, now=clock))
            except HistoryError as error:
                result = ('error', type(error), error.code, error.message, error.retryable, error.details)
            except Exception as error:
                result = ('other-error', type(error), str(error))
            results.append((result, clock.call_count))
        assert results[0] == results[1], (key, type(value).__name__)


@pytest.mark.parametrize('value', [None, [], {}, {'requestContext': []},
    {'requestContext': {'authorizer': 'malformed'}},
    {'requestContext': {'authorizer': {'jwt': {'claims': []}}}}])
def test_malformed_context_differential(value):
    results = []
    for module in [load_baseline(), security]:
        try:
            results.append(('ok', module.jwt_subject(copy.deepcopy(value), SETTINGS, now=lambda: 100)))
        except Exception as error:
            results.append((type(error), str(error)))
    assert results[0] == results[1]
