import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from assessment_dependencies import Dependencies
from assessment_service import Budget, Unavailable
import assessment_dependencies as module

SECRET = 'arn:aws:secretsmanager:us-east-1:107827791950:secret:trustcheckradar/dev/web-risk-api-key-AbCd12'
ALIAS = 'arn:aws:lambda:us-east-1:107827791950:function:trustcheckradar-dev-url-resolver:live'
FAKE_KEY = 'synthetic_test_key_1234567890'


@pytest.fixture
def clients(monkeypatch):
    factory = Mock()
    monkeypatch.setitem(__import__('sys').modules, 'boto3', SimpleNamespace(client=factory))
    monkeypatch.setitem(__import__('sys').modules, 'botocore.config', SimpleNamespace(Config=lambda **kwargs: kwargs))
    monkeypatch.setenv('WEB_RISK_SECRET_ARN', SECRET)
    monkeypatch.setenv('URL_RESOLVER_FUNCTION_ARN', ALIAS)
    return factory


def test_exact_secret_current_only_key_never_in_request_body(clients, monkeypatch):
    clients.return_value.get_secret_value.return_value = {'SecretString': json.dumps({'apiKey': FAKE_KEY})}
    lookup = Mock(return_value=[])
    monkeypatch.setattr(module, 'lookup', lookup)
    deps = Dependencies()
    deps.lookup('https://example.com/', Budget(32))
    deps.lookup('https://example.com/next', Budget(32))
    clients.return_value.get_secret_value.assert_called_once_with(SecretId=SECRET, VersionStage='AWSCURRENT')
    assert deps.provider_call_count == 2
    assert lookup.call_args.args[1] == FAKE_KEY
    config = clients.call_args.kwargs['config']
    assert config['retries'] == {'total_max_attempts': 1}


@pytest.mark.parametrize('secret', [None, '', '{"apiKey":"bad\\r\\nheader"}', '{}', '[1]', 'x' * 9000])
def test_bad_secret_never_calls_provider(clients, monkeypatch, secret):
    clients.return_value.get_secret_value.return_value = {'SecretString': secret}
    lookup = Mock()
    monkeypatch.setattr(module, 'lookup', lookup)
    deps = Dependencies()
    with pytest.raises(Unavailable, match='SECRET_UNAVAILABLE'):
        deps.lookup('https://example.com/', Budget(32))
    assert deps.provider_call_count == 0
    lookup.assert_not_called()


def test_resolver_invocation_is_synchronous_qualified_no_log_tail(clients):
    stream = io.BytesIO(b'{"schemaVersion":1}')
    clients.return_value.invoke.return_value = {'StatusCode': 200, 'Payload': stream}
    assert Dependencies().resolve('test', 'https://example.com/', Budget(32)) == {'schemaVersion': 1}
    kwargs = clients.return_value.invoke.call_args.kwargs
    assert kwargs['FunctionName'] == ALIAS
    assert kwargs['InvocationType'] == 'RequestResponse'
    assert 'LogType' not in kwargs
    assert stream.closed


@pytest.mark.parametrize('payload', [b'x' * 32769, b'not-json'])
def test_invalid_resolver_transport_is_handled_without_raw_output(clients, payload):
    clients.return_value.invoke.return_value = {'StatusCode': 200, 'Payload': io.BytesIO(payload)}
    with pytest.raises(Unavailable, match='RESOLVER_UNAVAILABLE'):
        Dependencies().resolve('test', 'https://example.com/', Budget(32))


def test_wrong_resource_environment_cannot_invoke_or_read(clients, monkeypatch):
    monkeypatch.setenv('URL_RESOLVER_FUNCTION_ARN', ALIAS.replace(':live', ''))
    with pytest.raises(Unavailable, match='CONFIGURATION_UNAVAILABLE'):
        Dependencies().resolve('test', 'https://example.com/', Budget(32))
    monkeypatch.setenv('WEB_RISK_SECRET_ARN', SECRET.replace('/dev/', '/prod/'))
    with pytest.raises(Unavailable, match='CONFIGURATION_UNAVAILABLE'):
        Dependencies().lookup('https://example.com/', Budget(32))
    clients.assert_not_called()
