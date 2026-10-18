"""History dependency failures at the actual API boundary, using Moto only."""
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import sys
from unittest import mock

from botocore.exceptions import (
    ClientError, ConnectionClosedError, ConnectTimeoutError,
    EndpointConnectionError, ReadTimeoutError,
)
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_transaction_errors import ACCOUNT, OP, OTHER_OP, ROUTES, world
from history_mutation_api import app

CANARY = "SYNTHETIC_PRIVATE_SDK_TEXT_ENDPOINT_ITEM_345"
FP = "synthetic-fingerprint"


def sdk_error(code, operation="GetItem"):
    return ClientError({"Error": {"Code": code, "Message": CANARY},
        "Item": {"private": CANARY},
        "ResponseMetadata": {"HTTPStatusCode": 503, "RequestId": CANARY}}, operation)


@pytest.fixture
def api_world(world):
    service, control, resource, client, clock = world
    devices = resource.create_table(TableName="history-devices",
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": n, "AttributeType": "S"} for n in ("PK", "SK", "GSI1PK", "GSI1SK")],
        GlobalSecondaryIndexes=[{"IndexName": "GSI1", "KeySchema": [
            {"AttributeName": "GSI1PK", "KeyType": "HASH"}, {"AttributeName": "GSI1SK", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"}}], BillingMode="PAY_PER_REQUEST")
    devices.put_item(Item={"PK": "USER#"+ACCOUNT, "SK": "ACTIVE_BINDING", "recordType": "ACTIVE_BINDING_POINTER",
        "bindingFingerprint": FP, "stateVersion": 1})
    devices.put_item(Item={"PK": "USER#"+ACCOUNT, "SK": "DEVICE#"+FP, "accountId": ACCOUNT,
        "status": "ACTIVE", "bindingFingerprint": FP, "GSI1PK": "USER#"+ACCOUNT+"#ACTIVE", "GSI1SK": FP})
    names = ("history-control", "history-users", "history-ledger", "history-devices", "history-content", "history-abuse")
    tables = {n: resource.Table(n) for n in names}
    # Same client receives real local transaction conditions; no bypass of service.
    patches = ExitStack()
    patches.enter_context(mock.patch.object(app.HistorySettings, "from_env", return_value=service.settings))
    patches.enter_context(mock.patch.object(app, "jwt_subject", return_value=ACCOUNT))
    wrapped_resource = mock.Mock(); wrapped_resource.Table.side_effect = tables.__getitem__
    patches.enter_context(mock.patch.object(app.boto3, "resource", return_value=wrapped_resource))
    patches.enter_context(mock.patch.object(app.boto3, "client", return_value=client))
    with patches:
        yield service, tables, client, clock


def invoke(route="DELETE /v1/users/history", operation=OP):
    body = {"schemaVersion": 1}
    if not route.endswith("bootstrap"): body["operationId"] = operation
    return app.lambda_handler({"routeKey": route, "body": json.dumps(body),
        "pathParameters": {"requestId": OP},
        "headers": {"x-device-binding-fingerprint": FP}}, None)


def snapshot(tables):
    return {name: sorted(table.scan()["Items"], key=lambda x: (x["PK"], x["SK"])) for name, table in tables.items()}


def fail_read(monkeypatch, table, sk, error, *, occurrence=1):
    original = table.get_item; matching = []
    def get(**kwargs):
        if kwargs["Key"]["SK"] == sk:
            matching.append(1)
            if len(matching) == occurrence: raise error
        return original(**kwargs)
    monkeypatch.setattr(table, "get_item", get)
    return matching


def assert_failure(response, caplog, status, category):
    assert response["statusCode"] == status
    expected = {"code": "SERVER_UNAVAILABLE", "message": "The History dependency could not be read.", "retryable": True} if status == 503 else {
        "code": "INTERNAL_ERROR", "message": "An internal error occurred.", "retryable": False}
    assert json.loads(response["body"]) == {"error": expected}
    assert response["headers"] == {"Content-Type": "application/json", "Cache-Control": "private, no-store", "Pragma": "no-cache"}
    assert CANARY not in json.dumps(response) and CANARY not in caplog.text
    records = [x for x in caplog.records if x.message.startswith("History mutation failed")]
    assert len(records) == 1 and records[0].message == "History mutation failed | failureClass="+category
    assert records[0].exc_info is None and records[0].stack_info is None
    assert ACCOUNT not in records[0].message and FP not in records[0].message


@pytest.mark.parametrize("route,_method", ROUTES)
@pytest.mark.parametrize("code,category", [
    ("ProvisionedThroughputExceededException", "SDK_READ_THROTTLED"),
    ("RequestLimitExceeded", "SDK_READ_THROTTLED"),
    ("ThrottlingException", "SDK_READ_THROTTLED"),
    ("InternalServerError", "SDK_READ_INTERNAL"),
])
def test_real_guards_then_read_throttling_never_mutates(api_world, monkeypatch, caplog, route, _method, code, category):
    _, tables, client, _ = api_world
    before = snapshot(tables)
    target = "STATE" if route.endswith("bootstrap") else "MUTATION#"+OP
    calls = fail_read(monkeypatch, tables["history-control"], target, sdk_error(code))
    with mock.patch.object(client, "transact_write_items", wraps=client.transact_write_items) as write:
        response = invoke(route)
    assert_failure(response, caplog, 503, category)
    assert len(calls) == 1 and write.call_count == 0 and snapshot(tables) == before


@pytest.mark.parametrize("error", [
    EndpointConnectionError(endpoint_url=CANARY), ConnectionClosedError(endpoint_url=CANARY),
    ConnectTimeoutError(endpoint_url=CANARY), ReadTimeoutError(endpoint_url=CANARY),
])
def test_transport_at_receipt_read_is_retryable_fixed_and_one_call(api_world, monkeypatch, caplog, error):
    _, tables, client, _ = api_world
    before = snapshot(tables)
    calls = fail_read(monkeypatch, tables["history-control"], "MUTATION#"+OP, error)
    with mock.patch.object(client, "transact_write_items", wraps=client.transact_write_items) as write:
        response = invoke()
    assert_failure(response, caplog, 503, "SDK_TRANSPORT_UNAVAILABLE")
    assert len(calls) == 1 and write.call_count == 0 and snapshot(tables) == before


@pytest.mark.parametrize("name,sk,route,occurrence", [
    ("history-users", "PROFILE", "DELETE /v1/users/history", 1),
    ("history-ledger", "ACCOUNT_DELETION", "DELETE /v1/users/history", 1),
    ("history-devices", "ACTIVE_BINDING", "DELETE /v1/users/history", 1),
    ("history-devices", "DEVICE#"+FP, "DELETE /v1/users/history", 1),
    ("history-control", "STATE", "DELETE /v1/users/history", 1),
    ("history-control", "REQUEST#"+OP, "DELETE /v1/users/history/{requestId}", 1),
    ("history-users", "PROFILE", "POST /v1/users/history/bootstrap", 2),
    ("history-ledger", "ACCOUNT_DELETION", "POST /v1/users/history/bootstrap", 2),
])
def test_each_authority_binding_and_state_read_failure_fails_closed(api_world, monkeypatch, caplog, name, sk, route, occurrence):
    _, tables, client, _ = api_world
    before = snapshot(tables)
    calls = fail_read(monkeypatch, tables[name], sk, EndpointConnectionError(endpoint_url=CANARY), occurrence=occurrence)
    with mock.patch.object(client, "transact_write_items", wraps=client.transact_write_items) as write:
        response = invoke(route)
    assert_failure(response, caplog, 503, "SDK_TRANSPORT_UNAVAILABLE")
    assert len(calls) == occurrence and write.call_count == 0 and snapshot(tables) == before


def test_legacy_binding_query_failure_is_not_absent_binding(api_world, monkeypatch, caplog):
    _, tables, client, _ = api_world
    devices = tables["history-devices"]
    devices.delete_item(Key={"PK": "USER#"+ACCOUNT, "SK": "ACTIVE_BINDING"})
    before = snapshot(tables)
    query = mock.Mock(side_effect=sdk_error("ThrottlingException", "Query"))
    monkeypatch.setattr(devices, "query", query)
    with mock.patch.object(client, "transact_write_items", wraps=client.transact_write_items) as write:
        response = invoke()
    assert_failure(response, caplog, 503, "SDK_READ_THROTTLED")
    assert query.call_count == 1 and write.call_count == 0 and snapshot(tables) == before


@pytest.mark.parametrize("error,category", [
    (sdk_error("AccessDeniedException"), "SDK_READ_REJECTED"),
    (sdk_error("ResourceNotFoundException"), "SDK_READ_REJECTED"),
    (sdk_error("ValidationException"), "SDK_READ_REJECTED"),
    (sdk_error("ConditionalCheckFailedException"), "SDK_READ_REJECTED"),
    (sdk_error("TransactionCanceledException"), "SDK_READ_REJECTED"),
    (sdk_error("UnrecognizedClientException"), "SDK_READ_REJECTED"),
    (sdk_error("UnknownFailure"), "SDK_READ_REJECTED"),
    (sdk_error("ThrottlingException", "PutItem"), "SDK_FAILURE"),
    (ValueError(CANARY), "UNEXPECTED_FAILURE"),
    (AttributeError(CANARY), "UNEXPECTED_FAILURE"),
    (TimeoutError(CANARY), "UNEXPECTED_FAILURE"),
])
def test_defects_and_nontransient_errors_stay_safe_nonretryable_500(api_world, monkeypatch, caplog, error, category):
    _, tables, client, _ = api_world
    before = snapshot(tables)
    fail_read(monkeypatch, tables["history-control"], "MUTATION#"+OP, error)
    with mock.patch.object(client, "transact_write_items", wraps=client.transact_write_items) as write:
        response = invoke()
    assert_failure(response, caplog, 500, category)
    assert write.call_count == 0 and snapshot(tables) == before


@pytest.mark.parametrize("route,_method", ROUTES)
def test_read_recovery_same_action_commits_once_and_replays_original(api_world, monkeypatch, caplog, route, _method):
    _, tables, client, clock = api_world
    if route.endswith("bootstrap"):
        tables["history-control"].delete_item(Key={"PK": "USER#"+ACCOUNT, "SK": "STATE"})
    before = snapshot(tables)
    target = "STATE" if route.endswith("bootstrap") else "MUTATION#"+OP
    fail_read(monkeypatch, tables["history-control"], target, EndpointConnectionError(endpoint_url=CANARY))
    with mock.patch.object(client, "transact_write_items", wraps=client.transact_write_items) as write:
        failed = invoke(route)
        assert snapshot(tables) == before and write.call_count == 0
        recovered = invoke(route)
        assert recovered["statusCode"] in {200, 202}
        committed = deepcopy(snapshot(tables))
        clock[0] += 400
        replay = invoke(route)
        assert replay == recovered and snapshot(tables) == committed and write.call_count == 1
    assert_failure(failed, caplog, 503, "SDK_TRANSPORT_UNAVAILABLE")
    assert json.loads(recovered["body"])["operation"]


@pytest.mark.parametrize("denial", ["deleted", "minor", "inactive", "other-device", "missing-profile"])
def test_recovery_still_rechecks_authority_and_binding(api_world, monkeypatch, caplog, denial):
    _, tables, client, _ = api_world
    fail_read(monkeypatch, tables["history-control"], "MUTATION#"+OP, EndpointConnectionError(endpoint_url=CANARY))
    assert invoke()["statusCode"] == 503
    profile_key = {"PK": "USER#"+ACCOUNT, "SK": "PROFILE"}
    if denial == "deleted":
        tables["history-ledger"].put_item(Item={"PK": "ACCOUNT#"+ACCOUNT, "SK": "ACCOUNT_DELETION"})
    elif denial == "missing-profile": tables["history-users"].delete_item(Key=profile_key)
    elif denial in {"minor", "inactive"}:
        profile = tables["history-users"].get_item(Key=profile_key)["Item"]
        profile["ageVerified" if denial == "minor" else "status"] = False if denial == "minor" else "DISABLED"
        tables["history-users"].put_item(Item=profile)
    else:
        tables["history-devices"].update_item(Key={"PK": "USER#"+ACCOUNT, "SK": "ACTIVE_BINDING"},
            UpdateExpression="SET bindingFingerprint = :other", ExpressionAttributeValues={":other": "another-device"})
    before = snapshot(tables)
    with mock.patch.object(client, "transact_write_items", wraps=client.transact_write_items) as write:
        recovered = invoke()
    assert recovered["statusCode"] == 403 and write.call_count == 0 and snapshot(tables) == before
    assert json.loads(recovered["body"])["error"]["code"] == ("DEVICE_BINDING_MISMATCH" if denial == "other-device" else "FORBIDDEN")


@pytest.mark.parametrize("response,operation", [
    (None, "GetItem"), ([], "GetItem"), ({}, "GetItem"), ({"Error": None}, "GetItem"),
    ({"Error": {"Code": True}}, "GetItem"), ({"Error": {"Code": []}}, "GetItem"),
    ({"Error": {"Code": "ThrottlingException"}}, []),
])
def test_malformed_sdk_metadata_never_escapes_fixed_500(api_world, monkeypatch, caplog, response, operation):
    _, tables, _, _ = api_world
    error = sdk_error("InternalServerError"); error.response = response; error.operation_name = operation
    fail_read(monkeypatch, tables["history-control"], "MUTATION#"+OP, error)
    assert_failure(invoke(), caplog, 500, "SDK_FAILURE")


def test_receipt_read_failure_after_commit_cannot_duplicate_mutation(api_world, monkeypatch, caplog):
    _, tables, client, _ = api_world
    with mock.patch.object(client, "transact_write_items", wraps=client.transact_write_items) as write:
        committed = invoke()
        assert committed["statusCode"] == 200
        original = deepcopy(snapshot(tables))
        fail_read(monkeypatch, tables["history-control"], "MUTATION#"+OP, EndpointConnectionError(endpoint_url=CANARY))
        assert_failure(invoke(), caplog, 503, "SDK_TRANSPORT_UNAVAILABLE")
        assert snapshot(tables) == original and write.call_count == 1
        assert invoke() == committed and snapshot(tables) == original and write.call_count == 1


def test_conditional_bootstrap_followup_read_failure_is_not_success_or_conflict(api_world, monkeypatch, caplog):
    _, tables, client, _ = api_world
    tables["history-control"].delete_item(Key={"PK": "USER#"+ACCOUNT, "SK": "STATE"})
    before = snapshot(tables)
    # The response proves only a conditional race; failure reading its result is
    # still unavailable, never a fabricated peer STATE or successful bootstrap.
    error = ClientError({"Error": {"Code": "TransactionCanceledException", "Message": CANARY},
        "CancellationReasons": [{"Code": "ConditionalCheckFailed"}, {"Code": "None"},
            {"Code": "None"}, {"Code": "None"}]}, "TransactWriteItems")
    fail_read(monkeypatch, tables["history-control"], "STATE", EndpointConnectionError(endpoint_url=CANARY), occurrence=2)
    with mock.patch.object(client, "transact_write_items", side_effect=error) as write:
        response = invoke("POST /v1/users/history/bootstrap")
    assert_failure(response, caplog, 503, "SDK_TRANSPORT_UNAVAILABLE")
    assert write.call_count == 1 and snapshot(tables) == before


def test_unrelated_account_rows_unchanged_after_failure_recovery_and_replay(api_world, monkeypatch):
    _, tables, client, _ = api_world
    other = "synthetic-unrelated-account"
    for name, table in tables.items():
        table.put_item(Item={"PK": "USER#"+other, "SK": "SYNTHETIC_BASELINE", "marker": name})
    baseline = {name: table.get_item(Key={"PK": "USER#"+other, "SK": "SYNTHETIC_BASELINE"})["Item"] for name, table in tables.items()}
    fail_read(monkeypatch, tables["history-control"], "MUTATION#"+OP, sdk_error("InternalServerError"))
    assert invoke()["statusCode"] == 503
    result = invoke(); assert result["statusCode"] == 200
    assert invoke() == result
    assert {name: table.get_item(Key={"PK": "USER#"+other, "SK": "SYNTHETIC_BASELINE"})["Item"] for name, table in tables.items()} == baseline
