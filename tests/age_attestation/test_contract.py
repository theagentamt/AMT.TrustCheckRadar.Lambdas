import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "age_attestation" / "app.py"
)
OPERATION_ID = "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4"


class _Serializer:
    def serialize(self, value):
        if value is None:
            return {"NULL": True}
        if isinstance(value, bool):
            return {"BOOL": value}
        if isinstance(value, str):
            return {"S": value}
        if isinstance(value, int):
            return {"N": str(value)}
        if isinstance(value, list):
            return {"L": [self.serialize(item) for item in value]}
        if isinstance(value, dict):
            return {"M": {key: self.serialize(item) for key, item in value.items()}}
        raise TypeError(type(value).__name__)


class _Deserializer:
    def deserialize(self, value):
        kind, raw = next(iter(value.items()))
        if kind == "NULL":
            return None
        if kind == "BOOL":
            return raw
        if kind == "S":
            return raw
        if kind == "N":
            return int(raw)
        if kind == "L":
            return [self.deserialize(item) for item in raw]
        if kind == "M":
            return {key: self.deserialize(item) for key, item in raw.items()}
        raise TypeError(kind)


class _ClientError(Exception):
    def __init__(self, response=None):
        self.response = response or {}


class _Cognito:
    def __init__(self):
        self.response = {
            "UserAttributes": [
                {"Name": "sub", "Value": "account-1"},
                {"Name": "phone_number", "Value": "+12025550123"},
                {"Name": "phone_number_verified", "Value": "true"},
            ]
        }
        self.error = None
        self.calls = []

    def admin_get_user(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class _PhoneMetadata:
    def __init__(self, region="US", number_type="FIXED_LINE_OR_MOBILE"):
        self.region = region
        self.number_type = number_type
        self.calls = []

    def classify(self, value):
        self.calls.append(value)
        return self.region, self.number_type


def _load_app():
    ddb = object()
    cognito = _Cognito()
    boto3_stub = types.ModuleType("boto3")
    boto3_stub.client = lambda name, **_kwargs: cognito if name == "cognito-idp" else ddb
    boto3_dynamodb_stub = types.ModuleType("boto3.dynamodb")
    boto3_types_stub = types.ModuleType("boto3.dynamodb.types")
    boto3_types_stub.TypeSerializer = _Serializer
    boto3_types_stub.TypeDeserializer = _Deserializer
    botocore_stub = types.ModuleType("botocore")
    botocore_exceptions_stub = types.ModuleType("botocore.exceptions")
    botocore_exceptions_stub.ClientError = _ClientError
    botocore_stub.exceptions = botocore_exceptions_stub
    spec = importlib.util.spec_from_file_location("age_attestation_contract_app", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    with mock.patch.dict(
        sys.modules,
        {
            "boto3": boto3_stub,
            "boto3.dynamodb": boto3_dynamodb_stub,
            "boto3.dynamodb.types": boto3_types_stub,
            "botocore": botocore_stub,
            "botocore.exceptions": botocore_exceptions_stub,
        },
    ), mock.patch.dict(
        os.environ,
        {
            "USERS_TABLE_NAME": "users",
            "DELETION_LEDGER_TABLE_NAME": "ledger",
            "AGE_ATTESTATION_USER_POOL_ID": "pool",
        },
        clear=True,
    ):
        spec.loader.exec_module(module)
    module.phone_metadata = _PhoneMetadata()
    return module, cognito


def _payload(**changes):
    return {
        "schemaVersion": 1,
        "operationId": OPERATION_ID,
        "over18Acknowledged": True,
        "agePolicyVersion": "v1.0",
    } | changes


def _event(*, payload=None, token_use="access", sub="account-1"):
    return {
        "version": "2.0",
        "routeKey": "POST /v1/users/age-attestation",
        "requestContext": {
            "http": {"method": "POST"},
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": sub,
                        "token_use": token_use,
                        "custom:over_18": "false",
                    }
                }
            },
        },
        "body": json.dumps(payload or _payload()),
        "isBase64Encoded": False,
    }


def _body(response):
    return json.loads(response["body"])


class AgeAttestationContractTests(unittest.TestCase):
    def test_uses_access_token_subject_and_body_decision_only(self):
        app, cognito = _load_app()
        result = app._success_body(OPERATION_ID, "2026-09-29T12:00:00+00:00", replayed=False)
        with mock.patch.object(app, "_process_attestation", return_value=result) as process:
            response = app.lambda_handler(_event(), types.SimpleNamespace(aws_request_id="request-1"))
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(cognito.calls, [{"UserPoolId": "pool", "Username": "account-1"}])
        process.assert_called_once_with("account-1", _payload())
        self.assertNotIn("sub", _body(response))

    def test_rejects_claimless_legacy_id_token_and_forged_trigger_events(self):
        app, _cognito = _load_app()
        values = [
            {"body": json.dumps(_payload() | {"sub": "victim"})},
            _event(token_use="id"),
            {
                "version": "2.0",
                "routeKey": "POST /v1/users/age-attestation",
                "requestContext": {
                    "http": {"method": "POST"},
                    "authorizer": {"claims": {"sub": "account-1", "token_use": "access"}},
                },
                "body": json.dumps(_payload()),
            },
            {
                "triggerSource": "PostConfirmation_ConfirmSignUp",
                "request": {"userAttributes": {"sub": "victim", "custom:over_18": "true"}},
            },
        ]
        for value in values:
            with self.subTest(value=value):
                response = app.lambda_handler(value, None)
                self.assertEqual(response["statusCode"], 401)
                self.assertEqual(_body(response)["error"]["code"], "AUTHENTICATION_REQUIRED")

    def test_request_contract_is_strict_and_positive_only(self):
        app, _cognito = _load_app()
        invalid = [
            _payload(sub="victim"),
            _payload(over18Acknowledged=False),
            _payload(agePolicyVersion="v2"),
            _payload(operationId="not-a-uuid"),
            _payload(schemaVersion=True),
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                response = app.lambda_handler(_event(payload=payload), None)
                self.assertEqual(response["statusCode"], 400)
                self.assertEqual(_body(response)["error"]["code"], "INVALID_REQUEST")

    def test_duplicate_json_fields_and_base64_are_rejected(self):
        app, _cognito = _load_app()
        duplicate = _event()
        duplicate["body"] = (
            '{"schemaVersion":1,"operationId":"' + OPERATION_ID + '",'
            '"over18Acknowledged":true,"over18Acknowledged":false,'
            '"agePolicyVersion":"v1.0"}'
        )
        encoded = _event()
        encoded["isBase64Encoded"] = True
        for value in (duplicate, encoded):
            response = app.lambda_handler(value, None)
            self.assertEqual(response["statusCode"], 400)

    def test_phone_verification_subject_and_region_are_authoritative(self):
        app, cognito = _load_app()
        cases = [
            ({"phone_number_verified": "false"}, "US", "PHONE_NOT_VERIFIED"),
            ({"phone_number": None}, "US", "PHONE_NOT_VERIFIED"),
            ({"sub": "other"}, "US", "AUTHENTICATION_REQUIRED"),
            ({}, "BS", "PHONE_REGION_NOT_ALLOWED"),
        ]
        base = {item["Name"]: item["Value"] for item in cognito.response["UserAttributes"]}
        for changes, region, code in cases:
            attrs = base | changes
            cognito.response = {
                "UserAttributes": [
                    {"Name": name, "Value": value}
                    for name, value in attrs.items()
                    if value is not None
                ]
            }
            app.phone_metadata = _PhoneMetadata(region=region)
            response = app.lambda_handler(_event(), None)
            self.assertEqual(_body(response)["error"]["code"], code)

    def test_cognito_throttle_returns_typed_retryable_error(self):
        app, cognito = _load_app()
        cognito.error = _ClientError({"Error": {"Code": "TooManyRequestsException"}})
        response = app.lambda_handler(_event(), types.SimpleNamespace(aws_request_id="request-1"))
        self.assertEqual(response["statusCode"], 429)
        self.assertEqual(response["headers"]["Retry-After"], "1")
        self.assertEqual(
            _body(response),
            {
                "schemaVersion": 1,
                "error": {
                    "code": "RATE_LIMITED",
                    "message": "Age attestation is temporarily rate limited.",
                    "retryable": True,
                },
                "requestId": "request-1",
            },
        )

    def test_allowed_region_configuration_is_validated_at_import(self):
        app, _cognito = _load_app()
        self.assertEqual(app.ALLOWED_REGION_CODES, {"US", "PR", "VI", "GU", "AS", "MP"})


if __name__ == "__main__":
    unittest.main()
