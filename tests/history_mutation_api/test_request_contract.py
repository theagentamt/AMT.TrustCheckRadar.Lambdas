"""Exercise the actual handler against the retained JSON Schema boundary."""
from contextlib import ExitStack
import json
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator
import pytest

from history_mutation_api import app


SCHEMAS = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts/history/v1/api-schemas.json").read_text()
)["$defs"]
OPERATION_ID = "11111111-1111-4111-8111-111111111111"
ROUTES = [
    ("POST /v1/users/history/bootstrap", "bootstrap", "bootstrapRequest"),
    ("DELETE /v1/users/history/{requestId}", "delete_one", "mutationRequest"),
    ("DELETE /v1/users/history", "clear_history", "mutationRequest"),
    ("POST /v1/users/progress/reset", "reset_progress", "mutationRequest"),
    ("DELETE /v1/users/history/account", "delete_account_history", "mutationRequest"),
]


@pytest.mark.parametrize("route,method,schema_name", ROUTES)
@pytest.mark.parametrize("encoded", [True, False], ids=["json", "decoded"])
@pytest.mark.parametrize("version", [True, False, None, "1", 0, 2])
def test_invalid_version_returns_contract_error_without_mutation(
    route, method, schema_name, encoded, version
):
    body = {"schemaVersion": version}
    if schema_name == "mutationRequest":
        body["operationId"] = OPERATION_ID
    assert not Draft202012Validator(SCHEMAS[schema_name]).is_valid(body)
    service, response = invoke(route, body, encoded)
    assert response["statusCode"] == 400
    assert json.loads(response["body"]) == {"error": {
        "code": "INVALID_REQUEST",
        "message": ("The bootstrap body does not match the approved contract."
                    if method == "bootstrap" else
                    "The mutation body does not match the approved contract."),
        "retryable": False,
    }}
    assert service.mock_calls == []


@pytest.mark.parametrize("route,method,schema_name", ROUTES)
@pytest.mark.parametrize("version", [1, 1.0], ids=["integer", "json-number-equivalent"])
def test_contract_valid_number_keeps_existing_authenticated_dispatch(
    route, method, schema_name, version
):
    body = {"schemaVersion": version}
    if schema_name == "mutationRequest":
        body["operationId"] = OPERATION_ID
    assert Draft202012Validator(SCHEMAS[schema_name]).is_valid(body)
    service, response = invoke(route, body, True)
    assert response["statusCode"] == 200
    args = ["synthetic-account"]
    if method == "delete_one":
        args.append(OPERATION_ID)
    if method != "bootstrap":
        args.append(OPERATION_ID)
    assert service.mock_calls == [getattr(mock.call, method)(*args)]


def invoke(route, body, encoded):
    # No SDK transport is reachable. Keep the handler's route, body validation,
    # response mapping and service dispatch real; auth/binding are prior gates.
    settings = mock.Mock()
    service = mock.Mock()
    for _, method, _ in ROUTES:
        getattr(service, method).return_value = {"operation": method, "status": "COMPLETE"}
    event = {
        "routeKey": route,
        "body": json.dumps(body) if encoded else body,
        "pathParameters": {"requestId": OPERATION_ID},
        "queryStringParameters": None,
    }
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(app.HistorySettings, "from_env", return_value=settings))
        stack.enter_context(mock.patch.object(app, "jwt_subject", return_value="synthetic-account"))
        stack.enter_context(mock.patch.object(app, "assert_authoritative_account_active"))
        stack.enter_context(mock.patch.object(app, "assert_active_device_binding"))
        stack.enter_context(mock.patch.object(app.boto3, "resource"))
        stack.enter_context(mock.patch.object(app.boto3, "client"))
        stack.enter_context(mock.patch.object(app, "HistoryMutationService", return_value=service))
        response = app.lambda_handler(event, None)
    return service, response
