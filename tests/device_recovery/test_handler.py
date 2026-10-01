import importlib.util
import json
import time
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "device_recovery"
SRC_DIR = MODULE_DIR.parent
for path in (SRC_DIR, MODULE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


class _FakeTable:
    def get_item(self, **kwargs):
        return {}

    def put_item(self, **kwargs):
        return {}

    def query(self, **kwargs):
        return {"Items": []}


class _FakeResource:
    def Table(self, _name):
        return _FakeTable()


boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *args, **kwargs: _FakeResource()
boto3_stub.client = lambda *args, **kwargs: object()
sys.modules.setdefault("boto3", boto3_stub)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_load_module("errors", MODULE_DIR / "errors.py")
_load_module("config", MODULE_DIR / "config.py")
_load_module("validation", MODULE_DIR / "validation.py")
_load_module("service", MODULE_DIR / "service.py")
app = _load_module("app", MODULE_DIR / "app.py")


class DeviceRecoveryHandlerTests(unittest.TestCase):
    def test_returns_success_response(self):
        event = {
            "routeKey": "POST /device-recovery",
            "requestContext": {"authorizer": {"iam": {"userArn": "arn:aws:iam::123456789012:role/support"}}},
            "body": json.dumps(
                {
                    "action": "RESET_ACTIVE_BINDING",
                    "accountId": "user-123",
                }
            ),
        }

        with mock.patch.dict(
            "os.environ",
            {"DEVICE_RECOVERY_ALLOWED_PRINCIPAL_ARNS_JSON": '["arn:aws:iam::123456789012:role/support"]'},
        ), mock.patch.object(
            app,
            "process_recovery",
            return_value={
                "result": "CLEARED",
                "accountId": "user-123",
                "bindingFingerprint": "fp-1",
                "status": "INACTIVE",
                "operatorId": "support-1",
                "recoveredAt": "2026-05-10T00:00:00+00:00",
            },
        ):
            response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(body["result"], "CLEARED")

    def test_returns_validation_error(self):
        event = {
            "routeKey": "POST /device-recovery",
            "requestContext": {"authorizer": {"iam": {"userArn": "arn:aws:iam::123456789012:role/support"}}},
            "body": json.dumps(
                {
                    "action": "RECOVER_BINDING",
                    "accountId": "user-123",
                }
            ),
        }

        with mock.patch.dict(
            "os.environ",
            {"DEVICE_RECOVERY_ALLOWED_PRINCIPAL_ARNS_JSON": '["arn:aws:iam::123456789012:role/support"]'},
        ):
            response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertEqual(body["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(body["error"]["details"][0]["field"], "bindingFingerprint")

    def test_returns_unauthorized_without_trusted_identity(self):
        event = {
            "routeKey": "POST /device-recovery",
            "body": json.dumps(
                {
                    "action": "RESET_ACTIVE_BINDING",
                    "accountId": "user-123",
                }
            ),
        }

        response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 401)
        body = json.loads(response["body"])
        self.assertEqual(body["error"]["code"], "UNAUTHORIZED")

    def test_rejects_ordinary_jwt_and_legacy_principal_as_support_authority(self):
        body = json.dumps({"action": "RESET_ACTIVE_BINDING", "accountId": "user-123"})
        authorizers = [
            {"jwt": {"claims": {"sub": "user-123"}}},
            {"claims": {"sub": "support-1"}},
            {"principalId": "support-1"},
        ]
        for authorizer in authorizers:
            response = app.lambda_handler({
                "routeKey": "POST /device-recovery",
                "requestContext": {"authorizer": authorizer}, "body": body,
            }, None)
            self.assertEqual(response["statusCode"], 401)

    def test_rejects_signed_but_unapproved_iam_principal(self):
        event = {
            "routeKey": "POST /device-recovery",
            "requestContext": {"authorizer": {"iam": {"userArn": "arn:aws:iam::123456789012:role/ordinary"}}},
            "body": json.dumps({"action": "RESET_ACTIVE_BINDING", "accountId": "user-123"}),
        }
        with mock.patch.dict(
            "os.environ",
            {"DEVICE_RECOVERY_ALLOWED_PRINCIPAL_ARNS_JSON": '["arn:aws:iam::123456789012:role/support"]'},
        ):
            response = app.lambda_handler(event, None)
        self.assertEqual(response["statusCode"], 403)

    def test_self_recovery_uses_token_subject_and_recent_signed_auth_time(self):
        current = int(time.time())
        event = {
            "routeKey": "POST /v1/users/device-recovery",
            "requestContext": {"authorizer": {"jwt": {"claims": {
                "sub": "user-123", "iss": app.config.COGNITO_ISSUER,
                "client_id": app.config.COGNITO_APP_CLIENT_ID, "token_use": "access",
                "exp": str(current + 600), "scope": app.config.COGNITO_REQUIRED_SCOPE,
                "auth_time": str(current - 30), "iat": str(current - 30),
            }}}},
            "body": json.dumps({
                "schemaVersion": 1,
                "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
                "action": "REPLACE_ACTIVE_BINDING",
                "bindingFingerprint": "fp-new", "platform": "ios", "osVersion": "18.4",
            }),
        }
        with mock.patch.object(app.config, "validate_self_recovery_config"), \
                mock.patch.object(app, "assert_authoritative_account_active"), \
                mock.patch.object(app, "process_self_recovery", return_value={
                    "schemaVersion": 1, "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
                    "operation": "REPLACE_ACTIVE_BINDING", "result": "RECOVERED",
                    "status": "COMPLETE", "bindingFingerprint": "fp-new",
                    "completedAtEpoch": current,
                }) as process:
            response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(process.call_args.kwargs["account_id"], "user-123")

    def test_self_recovery_rejects_stale_auth_time(self):
        current = int(time.time())
        event = {
            "routeKey": "POST /v1/users/device-recovery",
            "requestContext": {"authorizer": {"jwt": {"claims": {
                "sub": "user-123", "iss": app.config.COGNITO_ISSUER,
                "client_id": app.config.COGNITO_APP_CLIENT_ID, "token_use": "access",
                "exp": str(current + 600), "scope": app.config.COGNITO_REQUIRED_SCOPE,
                "auth_time": str(current - 301), "iat": str(current - 301),
            }}}},
            "body": json.dumps({
                "schemaVersion": 1,
                "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
                "action": "REPLACE_ACTIVE_BINDING",
                "bindingFingerprint": "fp-new", "platform": "ios", "osVersion": "18.4",
            }),
        }
        with mock.patch.object(app.config, "validate_self_recovery_config"):
            response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 401)
        self.assertEqual(json.loads(response["body"])["error"]["code"], "REAUTHENTICATION_REQUIRED")

    def test_self_recovery_rejects_user_supplied_account_id(self):
        event = {
            "routeKey": "POST /v1/users/device-recovery",
            "body": json.dumps({
                "schemaVersion": 1,
                "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
                "action": "REPLACE_ACTIVE_BINDING", "accountId": "victim",
                "bindingFingerprint": "fp-new", "platform": "ios", "osVersion": "18.4",
            }),
        }
        with mock.patch.object(app.config, "validate_self_recovery_config"), \
                mock.patch.object(app, "_self_subject", return_value="user-123"):
            response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(json.loads(response["body"])["error"]["code"], "INVALID_REQUEST")

    def test_self_recovery_rejects_boolean_schema_version_without_service_call(self):
        event = {
            "routeKey": "POST /v1/users/device-recovery",
            "body": json.dumps({
                "schemaVersion": True,
                "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
                "action": "REPLACE_ACTIVE_BINDING",
                "bindingFingerprint": "installation-key-1",
                "platform": "ios",
                "osVersion": "18.5",
            }),
        }
        with mock.patch.object(app.config, "validate_self_recovery_config"), \
                mock.patch.object(app, "_self_subject", return_value="user-123"), \
                mock.patch.object(app, "process_self_recovery") as process:
            response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 400)
        self.assertEqual(json.loads(response["body"])["error"]["code"], "INVALID_REQUEST")
        process.assert_not_called()

    def test_expired_receipt_failure_matches_versioned_fixture(self):
        event = {
            "routeKey": "POST /v1/users/device-recovery",
            "body": json.dumps({
                "schemaVersion": 1,
                "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
                "action": "REPLACE_ACTIVE_BINDING",
                "bindingFingerprint": "installation-key-1",
                "platform": "ios",
                "osVersion": "18.5",
            }),
        }
        error = app.AppError(
            "SERVER_UNAVAILABLE",
            "The recovery receipt is no longer available for replay.",
            retryable=False,
        )
        with mock.patch.object(app.config, "validate_self_recovery_config"), \
                mock.patch.object(app, "_self_subject", return_value="user-123"), \
                mock.patch.object(app, "process_self_recovery", side_effect=error):
            response = app.lambda_handler(event, None)

        fixture = json.loads((
            Path(__file__).resolve().parents[2]
            / "contracts/device-recovery/v1/fixtures/expired-receipt.error.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(response["statusCode"], 503)
        self.assertEqual(json.loads(response["body"]), fixture)


if __name__ == "__main__":
    unittest.main()
