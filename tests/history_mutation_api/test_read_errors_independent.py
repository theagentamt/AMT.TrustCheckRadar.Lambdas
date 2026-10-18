"""Independent malformed-metadata, fixed logging and existing-error isolation."""
import json
from pathlib import Path
import sys

from botocore.exceptions import ClientError
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_read_errors import api_world, invoke, fail_read, assert_failure, CANARY, OP
from test_transaction_errors import world
from history_mutation_api import app
from shared_history.errors import HistoryError


def sdk(code='ThrottlingException', operation='GetItem'):
    return ClientError({'Error':{'Code':code, 'Message':CANARY},
                        'ResponseMetadata':{'HTTPStatusCode':503, 'RequestId':CANARY}}, operation)


class NonStringOperation:
    def __hash__(self):
        return hash('GetItem')
    def __eq__(self, other):
        return other == 'GetItem'


@pytest.mark.parametrize('operation', [NonStringOperation(), [], {}, None, 1])
def test_nonstring_operation_metadata_cannot_spoof_approved_read(operation):
    error = sdk(); error.operation_name = operation
    assert app._dependency_failure(error) == ('SDK_FAILURE', False)


def test_operation_name_is_captured_once_before_exact_type_and_value_checks():
    class ReadOnceClientError(ClientError):
        def __getattribute__(self, name):
            if name == 'operation_name':
                count = object.__getattribute__(self, '__dict__').get('operation_reads', 0) + 1
                object.__getattribute__(self, '__dict__')['operation_reads'] = count
                return 'GetItem' if count == 1 else 'PutItem'
            return super().__getattribute__(name)
    error = ReadOnceClientError({'Error':{'Code':'ThrottlingException'}}, 'GetItem')
    assert app._dependency_failure(error) == ('SDK_READ_THROTTLED', True)
    assert error.operation_reads == 1


def test_sdk_response_property_failure_remains_finite():
    class BrokenClientError(ClientError):
        def __getattribute__(self, name):
            if name == 'response':
                raise ValueError(CANARY)
            return super().__getattribute__(name)
    error = BrokenClientError({'Error':{'Code':'InternalServerError'}}, 'GetItem')
    assert app._dependency_failure(error) == ('SDK_FAILURE', False)


def test_ducktyped_response_is_not_typed_sdk_authority():
    error = RuntimeError(CANARY)
    error.response = {'Error':{'Code':'InternalServerError'}}
    error.operation_name = 'GetItem'
    assert app._dependency_failure(error) == ('UNEXPECTED_FAILURE', False)


@pytest.mark.parametrize('operation', ['PutItem', 'TransactWriteItems', 'BatchGetItem', 'get_item'])
def test_transient_code_from_unapproved_operation_does_not_mask_unknown_failure(operation):
    assert app._dependency_failure(sdk('InternalServerError', operation)) == ('SDK_FAILURE', False)


@pytest.mark.parametrize('code', ['AccessDeniedException', 'ConditionalCheckFailedException', CANARY])
def test_http503_metadata_alone_does_not_make_read_retryable(code):
    assert app._dependency_failure(sdk(code)) == ('SDK_READ_REJECTED', False)


def test_hostile_exception_string_is_never_formatted_by_handler(api_world, monkeypatch, caplog):
    _, tables, client, _ = api_world
    formats = []
    class ToxicProgrammingError(Exception):
        def __str__(self):
            formats.append(1)
            raise AssertionError('private exception was formatted')
    calls = fail_read(monkeypatch, tables['history-control'], 'MUTATION#'+OP, ToxicProgrammingError())
    response = invoke()
    assert_failure(response, caplog, 500, 'UNEXPECTED_FAILURE')
    assert formats == [] and len(calls) == 1


def test_spoofed_operation_at_actual_handler_is_safe500_without_write(api_world, monkeypatch, caplog):
    _, tables, client, _ = api_world
    error = sdk(); error.operation_name = NonStringOperation()
    calls = fail_read(monkeypatch, tables['history-control'], 'MUTATION#'+OP, error)
    original = client.transact_write_items
    writes = []
    def record(**kw):
        writes.append(kw); return original(**kw)
    monkeypatch.setattr(client, 'transact_write_items', record)
    assert_failure(invoke(), caplog, 500, 'SDK_FAILURE')
    assert len(calls) == 1 and writes == []


@pytest.mark.parametrize('code,status,retryable', [
    ('CONFLICT',409,True), ('SERVER_UNAVAILABLE',503,True), ('FORBIDDEN',403,False),
])
def test_existing_history_errors_keep_prior_first_catch_contract(api_world, monkeypatch, caplog, code, status, retryable):
    _, tables, _, _ = api_world
    message = 'Existing fixed public History error.'
    fail_read(monkeypatch, tables['history-control'], 'MUTATION#'+OP,
              HistoryError(code, message, retryable=retryable))
    response = invoke()
    assert response['statusCode'] == status
    assert json.loads(response['body']) == {'error':{'code':code,'message':message,'retryable':retryable}}
    assert not any(r.getMessage().startswith('History mutation failed') for r in caplog.records)
