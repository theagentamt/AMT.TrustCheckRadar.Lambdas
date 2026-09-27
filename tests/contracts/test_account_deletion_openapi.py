"""HTTP documentation is bound to frozen wire data and actual pure handler helpers."""
import ast
import copy
import hashlib
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts/account-deletion/1.0.0-candidate.1"
SPEC = json.loads((ROOT / "docs/api/account-deletion.openapi.json").read_text())
FIXTURES = json.loads((CONTRACT / "fixtures.json").read_text())
SCHEMAS = SPEC["components"]["schemas"]
PATH = SPEC["paths"]["/v1/users/account-deletion"]
SOURCE = ROOT / "src/account_data_api"


def source_helpers(path, names, namespace):
    """Compile only pure helpers: no Lambda import, SDK client, environment or I/O."""
    tree = ast.parse(path.read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


ERROR_STATUS = ast.literal_eval(ast.parse((SOURCE / "errors.py").read_text()).body[0].value)
ERRORS = source_helpers(SOURCE / "errors.py", ["AppError"], {"HTTP_STATUS_BY_CODE": ERROR_STATUS})
AppError = ERRORS["AppError"]
HANDLER = source_helpers(
    SOURCE / "app.py",
    ["_error", "_response", "_route_key", "_operation", "_epoch", "_assert_recent_reauthentication"],
    {"json": json, "time": time, "AppError": AppError,
     "config": SimpleNamespace(ACCOUNT_DELETION_MAX_REAUTH_AGE_SECONDS=300)},
)
from uuid import UUID
VALIDATION = source_helpers(
    SOURCE / "validation.py",
    ["deletion_request", "validate_get", "_no_query", "_unique_object", "_reject_constant", "_invalid"],
    {"json": json, "UUID": UUID, "AppError": AppError, "MAX_REQUEST_BYTES": 1024},
)


def validator(name):
    return Draft202012Validator(SCHEMAS[name])


def test_frozen_contract_files_and_embedded_schemas_are_unchanged():
    expected = {
        "README.md": "633147bf6f5ab5fbc971b32f96ee2ebda87b14fb60327b824a2e0faa37ab8abf",
        "fixtures.json": "79091e62f4204a0e6272aba9d93d08a659652567416ba3d1dcd9ce8660c8db42",
        "request.schema.json": "999f7d4fb8842f6f3fc10aaec98122b90b6721a62ce1ca4f76f6ea4a0d3de1ca",
        "response.schema.json": "d7c037f5f63eb6736959f11673c206ce40acc53b463635b31a5684e566e5c825",
    }
    manifest = {line.split()[1]: line.split()[0] for line in (CONTRACT / "SHA256SUMS").read_text().splitlines()}
    assert manifest == expected
    for name, digest in expected.items():
        assert hashlib.sha256((CONTRACT / name).read_bytes()).hexdigest() == digest
    assert SCHEMAS["AccountDeletionRequest"] == json.loads((CONTRACT / "request.schema.json").read_text())
    response = json.loads((CONTRACT / "response.schema.json").read_text())
    assert SCHEMAS["AccountDeletionStatusResponse"] == response
    assert SCHEMAS["AccountDeletionAccepted"] == response["oneOf"][1]
    for schema in SCHEMAS.values():
        Draft202012Validator.check_schema(schema)


def test_only_approved_routes_success_status_and_no_identity_parameter():
    assert SPEC["openapi"] == "3.1.0"
    assert set(SPEC["paths"]) == {"/v1/users/account-deletion"}
    assert set(PATH) == {"post", "get"}
    for method, status in (("post", "202"), ("get", "200")):
        operation = PATH[method]
        assert [s for s in operation["responses"] if s.startswith("2")] == [status]
        assert operation["security"] == [{"CognitoAccessToken": []}]
        assert operation["parameters"] == []
        assert operation["x-query-parameters-allowed"] is False
        assert HANDLER["_operation"]({"routeKey": f"{method.upper()} /v1/users/account-deletion"}) in ("request", "status")
    assert HANDLER["_operation"]({"routeKey": "DELETE /v1/users/account-deletion"}) == "unknown"
    assert "requestBody" not in PATH["get"]
    assert PATH["post"]["requestBody"]["x-max-utf8-bytes"] == VALIDATION["MAX_REQUEST_BYTES"]
    assert set(SCHEMAS["AccountDeletionRequest"]["properties"]) == {"schemaVersion", "action", "operationId"}


def test_examples_are_exact_frozen_fixtures_and_fit_success_schemas():
    request = PATH["post"]["requestBody"]["content"]["application/json"]["examples"]["request"]["value"]
    assert request == FIXTURES["postRequest"]
    validator("AccountDeletionRequest").validate(request)
    assert VALIDATION["deletion_request"]({"body": json.dumps(request)}) == request
    for method, status, name in (("post", "202", "AccountDeletionAccepted"), ("get", "200", "AccountDeletionStatusResponse")):
        content = PATH[method]["responses"][status]["content"]["application/json"]
        assert content["schema"] == {"$ref": f"#/components/schemas/{name}"}
        for key, example in content["examples"].items():
            assert example["value"] == FIXTURES[key]
            validator(name).validate(example["value"])
    with pytest.raises(ValidationError):
        validator("AccountDeletionAccepted").validate(FIXTURES["notRequested"])


@pytest.mark.parametrize("change", [
    {"accountId": "untrusted"}, {"status": "COMPLETE"}, {"operationId": "not-a-uuid"},
    {"schemaVersion": True}, {"unexpected": "content"},
])
def test_status_schema_rejects_unknown_identity_completion_and_shape(change):
    body = copy.deepcopy(FIXTURES["accepted"])
    body.update(change)
    with pytest.raises(ValidationError):
        validator("AccountDeletionStatusResponse").validate(body)


@pytest.mark.parametrize("change", [
    {"accountId": "untrusted"}, {"operationId": "3FEFBF1A-CAF4-4E72-AB61-4FB36BF925B4"},
    {"operationId": "3fefbf1a-caf4-7e72-ab61-4fb36bf925b4"}, {"schemaVersion": True},
    {"action": "DELETE_HISTORY"},
])
def test_request_schema_and_source_reject_same_invalid_variants(change):
    body = dict(FIXTURES["postRequest"], **change)
    with pytest.raises(ValidationError):
        validator("AccountDeletionRequest").validate(body)
    with pytest.raises(AppError) as error:
        VALIDATION["deletion_request"]({"body": json.dumps(body)})
    assert error.value.status_code == 400


@pytest.mark.parametrize("event", [
    {"body": '{"schemaVersion":1,"schemaVersion":1}'},
    {"body": json.dumps(FIXTURES["postRequest"]).replace('"schemaVersion": 1', '"schemaVersion": 1.0')},
    {"body": " " * 1025}, {"body": "NaN"},
    {"body": json.dumps(FIXTURES["postRequest"]), "isBase64Encoded": True},
    {"body": json.dumps(FIXTURES["postRequest"]), "rawQueryString": "accountId=other"},
])
def test_documented_lexical_and_transport_restrictions_remain_source_enforced(event):
    with pytest.raises(AppError) as error:
        VALIDATION["deletion_request"](event)
    assert error.value.status_code == 400
    text = PATH["post"]["requestBody"]["description"]
    for required in ("1024", "UTF-8", "duplicate", "base64", "1.0", "NaN"):
        assert required in text


def test_get_rejects_body_and_query_without_implying_new_post():
    VALIDATION["validate_get"]({})
    for event in ({"body": "{}"}, {"rawQueryString": "cursor=x"}, {"isBase64Encoded": True}):
        with pytest.raises(AppError):
            VALIDATION["validate_get"](event)
    assert "NOT_REQUESTED" in PATH["get"]["description"]
    assert "racing" in PATH["get"]["description"]


def test_actual_error_mapping_envelope_and_cache_headers_match_document():
    assert set(SCHEMAS["ErrorEnvelope"]["properties"]["error"]["properties"]["code"]["enum"]) == set(ERROR_STATUS)
    for code, status in ERROR_STATUS.items():
        error = AppError(code, "Fixed safe message", details=[{"field": "body", "issue": "Invalid"}])
        body = HANDLER["_error"](error)
        validator("ErrorEnvelope").validate(body)
        assert code in SPEC["components"]["responses"][f"Http{status}"]["x-lambda-error-codes"]
        response = HANDLER["_response"](error.status_code, body)
        assert response["statusCode"] == status
        assert response["headers"]["Content-Type"] == "application/json"
        for name in ("Cache-Control", "Pragma"):
            assert response["headers"][name] == SPEC["components"]["responses"][f"Http{status}"]["headers"][name]["schema"]["const"]
    for example in FIXTURES["errors"]:
        validator("ErrorEnvelope").validate(example["body"])
        assert example["httpStatus"] == ERROR_STATUS[example["body"]["error"]["code"]]
    with pytest.raises(ValidationError):
        validator("ErrorEnvelope").validate({"error": {"code": "DELETE_DONE", "message": "Done", "retryable": False}})


def test_post_reauthentication_boundaries_match_source():
    policy = PATH["post"]["x-reauthentication"]
    assert policy == {"maximumAgeSeconds": 300, "futureToleranceSeconds": 60, "claims": ["auth_time", "iat"]}
    current = 1800000000
    for age, accepted in ((300, True), (301, False), (-60, True), (-61, False)):
        claims = {"auth_time": current-age, "iat": current-age}
        event = {"requestContext": {"authorizer": {"jwt": {"claims": claims}}}}
        if accepted:
            HANDLER["_assert_recent_reauthentication"](event, now=lambda: current)
        else:
            with pytest.raises(AppError) as error:
                HANDLER["_assert_recent_reauthentication"](event, now=lambda: current)
            assert error.value.code == "REAUTHENTICATION_REQUIRED"
    assert "Refresh" in PATH["post"]["description"]


def test_semantic_limits_and_dev_scope_are_documented_without_new_wire_fields():
    text = PATH["get"]["responses"]["200"]["description"]
    for term in ("unique", "completionEligible", "86400", "fail closed"):
        assert term in text
    assert "account-deletion-http-scope.md" in SPEC["info"]["description"]
    assert SPEC["x-release-qualification"] == "SECUR4ALL-329"
    assert "ALLOWLIST" not in json.dumps(SCHEMAS)
    assert "subjects" not in SCHEMAS["AccountDeletionRequest"]["properties"]


def test_bearer_scheme_matches_source_verified_access_token_checks():
    security = SPEC["components"]["securitySchemes"]["CognitoAccessToken"]
    assert security["type"] == "http" and security["scheme"] == "bearer"
    path = ROOT / "src/shared_history/security.py"
    tree = ast.parse(path.read_text())
    pattern = next(n.value.args[0].value for n in tree.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "SUBJECT_PATTERN" for t in n.targets))
    auth = source_helpers(path, ["jwt_subject", "_exact_epoch"], {
        "HistoryError": AppError, "time": time, "SUBJECT_PATTERN": re.compile(pattern),
    })
    current = 1800000000
    settings = SimpleNamespace(cognito_issuer="https://issuer.invalid/pool",
                               cognito_app_client_id="client", cognito_required_scope="aws.cognito.signin.user.admin")
    base = {"sub": "synthetic-account", "iss": settings.cognito_issuer,
            "client_id": "client", "token_use": "access", "exp": current+60,
            "scope": settings.cognito_required_scope}
    event = lambda claims: {"requestContext": {"authorizer": {"jwt": {"claims": claims}}}}
    assert auth["jwt_subject"](event(base), settings, now=lambda: current) == base["sub"]
    for field, value in (("iss", "https://foreign.invalid"), ("client_id", "other"),
                         ("token_use", "id"), ("exp", current), ("scope", ""), ("sub", "")):
        with pytest.raises(AppError) as error:
            auth["jwt_subject"](event(dict(base, **{field: value})), settings, now=lambda: current)
        assert error.value.code == "UNAUTHORIZED"
    for term in ("issuer", "client_id", "token_use=access", "exp", "aws.cognito.signin.user.admin", "sub"):
        assert term in security["description"]
