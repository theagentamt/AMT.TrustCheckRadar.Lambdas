"""Synthetic SDK errors and actual local Moto conditions; never live faults."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy
import json
import hashlib
import threading
import traceback
from types import SimpleNamespace
from unittest import mock

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError
from moto import mock_aws
import pytest

from history_mutation_api import app
from history_mutation_api.service import HistoryMutationService, _conditional_transaction_failure
from shared_history.errors import HistoryError


ACCOUNT = "synthetic-history-account"
OP = "11111111-1111-4111-8111-111111111111"
OTHER_OP = "22222222-2222-4222-8222-222222222222"
CANARY = "PRIVATE_SDK_MESSAGE_ITEM_ENDPOINT_CANARY"
STATE = {"PK": "USER#"+ACCOUNT, "SK": "STATE", "schemaVersion": 1,
         "accountStatus": "ACTIVE", "historyGeneration": 2,
         "recognitionGeneration": 3, "acceptedSequence": 8, "stateVersion": 1}


def cancellation(codes):
    return ClientError({"Error": {"Code": "TransactionCanceledException", "Message": CANARY},
        "CancellationReasons": [{"Code": code, "Message": CANARY, "Item": {"private": CANARY}} for code in codes]},
        "TransactWriteItems")


@pytest.fixture
def world():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        tables = {}
        for name in ("history-control", "history-users", "history-ledger", "history-content", "history-abuse"):
            tables[name] = ddb.create_table(TableName=name,
                KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
                AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}, {"AttributeName": "SK", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST")
        control = tables["history-control"]
        control.put_item(Item=deepcopy(STATE))
        tables["history-users"].put_item(Item={"PK": "USER#"+ACCOUNT, "SK": "PROFILE",
            "sub": ACCOUNT, "status": "ACTIVE", "ageVerified": True})
        settings = SimpleNamespace(schema_version=1, control_table_name="history-control",
            users_table_name="history-users", deletion_ledger_table_name="history-ledger",
            content_table_name="history-content", analysis_abuse_table_name="history-abuse",
            device_bindings_table_name="history-devices", mutation_retention_days=400,
            dedup_retention_days=400, erasure_sla_hours=24,
            validate_mutations=lambda: None, validate_recognition=lambda: None)
        client = boto3.client("dynamodb", region_name="us-east-1")
        clock = [1700000000]
        service = HistoryMutationService(settings=settings, control_table=control,
            users_table=tables["history-users"], deletion_ledger_table=tables["history-ledger"],
            dynamodb_client=client, now=lambda: clock[0])
        yield service, control, ddb, client, clock


@pytest.mark.parametrize("codes,expected", [
    (["ConditionalCheckFailed", "None"], True),
    (["None", "ConditionalCheckFailed"], True),
    (["ConditionalCheckFailed", "ConditionalCheckFailed"], True),
    (["None", "None"], False),
    (["ProvisionedThroughputExceeded", "None"], False),
    (["ConditionalCheckFailed", "ThrottlingError"], False),
    (["TransactionConflict", "None"], False),
    (["ValidationError", "None"], False),
    (["ItemCollectionSizeLimitExceeded", "None"], False),
    (["ConditionalCheckFailed"], False),
    (["ConditionalCheckFailed", None], False),
    ([True, "ConditionalCheckFailed"], False),
    ([{}, "ConditionalCheckFailed"], False),
])
def test_only_exact_complete_conditional_vector_is_conflict(codes, expected):
    assert _conditional_transaction_failure(cancellation(codes), 2) is expected


@pytest.mark.parametrize("response", [None, [], {}, {"Error": None},
    {"Error": {"Code": "TransactionCanceledException"}},
    {"Error": {"Code": "TransactionCanceledException"}, "CancellationReasons": "ConditionalCheckFailed"},
    {"Error": {"Code": "TransactionCanceledException"}, "CancellationReasons": [None]},
    {"Error": {"Code": "Other"}, "CancellationReasons": [{"Code": "ConditionalCheckFailed"}]},
])
def test_missing_or_malformed_error_metadata_is_not_a_conditional_race(response):
    error = RuntimeError(CANARY); error.response = response
    assert _conditional_transaction_failure(error, 1) is False


@pytest.mark.parametrize("error", [cancellation(["ProvisionedThroughputExceeded"]),
    ClientError({"Error": {"Code": "ThrottlingException", "Message": CANARY}}, "TransactWriteItems"),
    ClientError({"Error": {"Code": "InternalServerError", "Message": CANARY}}, "TransactWriteItems"),
    EndpointConnectionError(endpoint_url=CANARY), TimeoutError(CANARY), ValueError(CANARY)])
def test_unavailable_write_has_fixed_error_without_retry_or_exception_chain(error):
    client = mock.Mock(); client.transact_write_items.side_effect = error
    service = HistoryMutationService(settings=object(), control_table=None, dynamodb_client=client)
    with pytest.raises(HistoryError) as raised:
        service._transact([{"Put": {}}])
    assert (raised.value.code, raised.value.status_code, raised.value.retryable) == ("SERVER_UNAVAILABLE", 503, True)
    assert CANARY not in "".join(traceback.format_exception(raised.type, raised.value, raised.tb))
    assert raised.value.__cause__ is None
    assert client.transact_write_items.call_count == 1


ROUTES = [
    ("POST /v1/users/history/bootstrap", "bootstrap"),
    ("DELETE /v1/users/history/{requestId}", "delete_one"),
    ("DELETE /v1/users/history", "clear_history"),
    ("POST /v1/users/progress/reset", "reset_progress"),
    ("DELETE /v1/users/history/account", "delete_account_history"),
]


@pytest.mark.parametrize("route,method", ROUTES)
@pytest.mark.parametrize("reason,code,status", [
    ("ProvisionedThroughputExceeded", "SERVER_UNAVAILABLE", 503),
    ("ConditionalCheckFailed", "CONFLICT", 409),
])
def test_actual_handler_contract_and_private_logs(world, caplog, route, method, reason, code, status):
    service, control, resource, _, _ = world
    if method == "bootstrap":
        control.delete_item(Key={"PK": "USER#"+ACCOUNT, "SK": "STATE"})
    client = mock.Mock()
    def fail(**kw):
        raise cancellation([reason] + ["None"]*(len(kw["TransactItems"])-1))
    client.transact_write_items.side_effect = fail
    body = {"schemaVersion": 1}
    if method != "bootstrap": body["operationId"] = OP
    event = {"routeKey": route, "body": json.dumps(body), "pathParameters": {"requestId": OP}}
    caplog.set_level("WARNING")
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(app.HistorySettings, "from_env", return_value=service.settings))
        jwt = stack.enter_context(mock.patch.object(app, "jwt_subject", return_value=ACCOUNT))
        active = stack.enter_context(mock.patch.object(app, "assert_authoritative_account_active"))
        binding = stack.enter_context(mock.patch.object(app, "assert_active_device_binding"))
        stack.enter_context(mock.patch.object(app.boto3, "resource", return_value=resource))
        stack.enter_context(mock.patch.object(app.boto3, "client", return_value=client))
        response = app.lambda_handler(event, None)
    assert response["statusCode"] == status
    error = json.loads(response["body"])["error"]
    assert error["code"] == code and error["retryable"] is True
    assert set(error) == {"code", "message", "retryable"}
    assert CANARY not in json.dumps(response) and CANARY not in caplog.text
    assert client.transact_write_items.call_count == 1
    jwt.assert_called_once(); active.assert_called_once(); binding.assert_called_once()


def test_bootstrap_service_cancellation_never_uses_conditional_race_readback(world, monkeypatch):
    service, control, _, _, _ = world
    control.delete_item(Key={"PK": "USER#"+ACCOUNT, "SK": "STATE"})
    original = control.get_item; reads = []
    def record(**kw): reads.append(kw); return original(**kw)
    monkeypatch.setattr(control, "get_item", record)
    client = mock.Mock(); client.transact_write_items.side_effect = cancellation(["None"]*3+["ProvisionedThroughputExceeded"])
    service.client = client
    with pytest.raises(HistoryError, match="could not be confirmed") as raised: service.bootstrap(ACCOUNT)
    assert raised.value.code == "SERVER_UNAVAILABLE" and len(reads) == 1
    assert control.scan()["Items"] == []


def synchronize_state_reads(service, control, monkeypatch):
    barrier = threading.Barrier(2); real = control.get_item; seen = [0]; read_lock = threading.Lock()
    def get(**kwargs):
        result = real(**kwargs)
        with read_lock:
            synchronize = kwargs["Key"]["SK"] == "STATE" and seen[0] < 2
            if synchronize: seen[0] += 1
        if synchronize: barrier.wait(timeout=10)
        return result
    monkeypatch.setattr(control, "get_item", get)
    lock = threading.Lock(); real_write = service.client.transact_write_items; calls = []
    def transact(**kwargs):
        # Serialize only the Moto SDK boundary; both callers have already read
        # the same old STATE. Production correctness is the real conditions.
        with lock:
            calls.append(1); return real_write(**kwargs)
    monkeypatch.setattr(service.client, "transact_write_items", transact)
    return calls


def test_real_same_operation_race_one_generation_then_exact_replay(world, monkeypatch):
    service, control, _, _, clock = world
    calls = synchronize_state_reads(service, control, monkeypatch)
    def attempt():
        try:return service.clear_history(ACCOUNT, OP)
        except HistoryError as err:return err.code
    with ThreadPoolExecutor(max_workers=2) as executor: results = list(executor.map(lambda _: attempt(), range(2)))
    assert sum(isinstance(x, dict) for x in results) == 1 and results.count("CONFLICT") == 1
    winner = next(x for x in results if isinstance(x, dict))
    before = deepcopy(control.scan()["Items"]); clock[0] += 100
    assert service.clear_history(ACCOUNT, OP) == winner
    assert control.scan()["Items"] == before and len(calls) == 2
    state = control.get_item(Key={"PK": "USER#"+ACCOUNT, "SK": "STATE"})["Item"]
    assert state["historyGeneration"] == 3 and state["recognitionGeneration"] == 3
    assert len([x for x in before if x["SK"].startswith("MUTATION#")]) == 1


def test_real_concurrent_clear_and_reset_keep_independent_generations(world, monkeypatch):
    service, control, _, _, _ = world
    calls = synchronize_state_reads(service, control, monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        clear = executor.submit(service.clear_history, ACCOUNT, OP)
        reset = executor.submit(service.reset_progress, ACCOUNT, OTHER_OP)
        assert clear.result()["historyGeneration"] == 3
        assert reset.result()["recognitionGeneration"] == 4
    state = control.get_item(Key={"PK": "USER#"+ACCOUNT, "SK": "STATE"})["Item"]
    assert state["historyGeneration"] == 3 and state["recognitionGeneration"] == 4 and len(calls) == 2


def test_real_bootstrap_race_returns_existing_authoritative_state_without_second_write(world, monkeypatch):
    service, control, _, _, _ = world
    control.delete_item(Key={"PK": "USER#"+ACCOUNT, "SK": "STATE"})
    calls = synchronize_state_reads(service, control, monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: service.bootstrap(ACCOUNT), range(2)))
    assert results[0] == results[1] and results[0]["status"] == "COMPLETE"
    assert len(calls) == 2 and len(control.scan()["Items"]) == 2


@pytest.mark.parametrize("method", ["bootstrap", "delete_one", "clear_history", "reset_progress", "delete_account_history"])
def test_response_unknown_commit_reconciles_same_operation_without_duplicate_effect(world, monkeypatch, method):
    service, control, resource, client, clock = world
    if method == "bootstrap":
        control.delete_item(Key={"PK": "USER#"+ACCOUNT, "SK": "STATE"})
    if method == "delete_one":
        control.put_item(Item={"PK": "USER#"+ACCOUNT, "SK": "REQUEST#"+OP,
            "status": "ACTIVE", "historyGeneration": 2, "contentSortKey": "COMPLETE#synthetic",
            "payloadHash": "a"*64, "lifecycleBucket": "PENDING#00", "lifecycleAt": clock[0]})
        resource.Table("history-content").put_item(Item={"PK": "USER#"+ACCOUNT+"#HISTORY#2",
            "SK": "COMPLETE#synthetic", "assessment": "SYNTHETIC_CONTENT"})
        resource.Table("history-abuse").put_item(Item={"PK": "ANALYSIS#REQUEST#"+hashlib.sha256(ACCOUNT.encode()).hexdigest(),
            "SK": OP, "payloadHash": "a"*64, "response": "SYNTHETIC_REPLAY"})
    args = [ACCOUNT]
    if method == "delete_one": args.append(OP)
    if method != "bootstrap": args.append(OP)
    invoke = lambda: getattr(service, method)(*args)
    real = client.transact_write_items; calls = []
    def lost(**kwargs):
        calls.append(1); real(**kwargs); raise TimeoutError(CANARY)
    monkeypatch.setattr(client, "transact_write_items", lost)
    with pytest.raises(HistoryError) as raised: invoke()
    assert raised.value.code == "SERVER_UNAVAILABLE"
    names = ("history-control", "history-content", "history-abuse")
    before = {name: deepcopy(resource.Table(name).scan()["Items"]) for name in names}
    clock[0] += 100
    replay = invoke()
    assert replay["status"] == ("PENDING" if method == "delete_account_history" else "COMPLETE")
    assert {name: resource.Table(name).scan()["Items"] for name in names} == before and len(calls) == 1
    if method == "delete_one":
        assert before["history-content"] == [] and "response" not in before["history-abuse"][0]
    if method == "clear_history": assert replay["historyGeneration"] == 3
    if method == "reset_progress": assert replay["recognitionGeneration"] == 4


def test_late_account_fence_remains_atomic_and_no_receipt_is_created(world):
    service, control, resource, _, _ = world
    resource.Table("history-users").update_item(Key={"PK": "USER#"+ACCOUNT, "SK": "PROFILE"},
        UpdateExpression="SET ageVerified = :no", ExpressionAttributeValues={":no": False})
    with pytest.raises(HistoryError) as raised: service.clear_history(ACCOUNT, OP)
    assert raised.value.code == "CONFLICT"
    assert control.scan()["Items"] == [STATE]
