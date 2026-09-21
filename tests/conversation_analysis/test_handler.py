import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
MODULE_DIR = SRC_DIR / "conversation_analysis"
for path in (SRC_DIR, MODULE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

for module_name in ["config", "errors", "verification", "validation", "service", "app", "scan_access", "abuse_controls", "device_binding", "analysis_client", "response_builders", "safety", "shared_entitlements", "shared_entitlements.service", "shared_entitlements.config"]:
    sys.modules.pop(module_name, None)

os.environ.update({
    "ANALYSIS_ABUSE_TABLE_NAME": "abuse",
    "COGNITO_ISSUER": "https://cognito-idp.us-east-1.amazonaws.com/test",
    "COGNITO_APP_CLIENT_ID": "test-client",
    "COGNITO_REQUIRED_SCOPE": "aws.cognito.signin.user.admin",
    "USERS_TABLE_NAME": "users",
    "DELETION_LEDGER_TABLE_NAME": "ledger",
})


def _claims(sub):
    return {
        "sub": sub,
        "iss": os.environ["COGNITO_ISSUER"],
        "client_id": os.environ["COGNITO_APP_CLIENT_ID"],
        "token_use": "access",
        "exp": "4102444800",
        "scope": os.environ["COGNITO_REQUIRED_SCOPE"],
    }

class _FakeTable:
    def get_item(self, **kwargs):
        return {}
    def put_item(self, **kwargs):
        return {}
    def update_item(self, **kwargs):
        return {"Attributes": {"requestCount": 1}}
    def delete_item(self, **kwargs):
        return {}


class _FakeResource:
    def Table(self, name):
        if name == "users":
            class Users:
                def get_item(self, Key, **_kwargs):
                    sub = Key["PK"].removeprefix("USER#")
                    return {"Item": {"sub": sub, "status": "ACTIVE", "ageVerified": True}}
            return Users()
        if name == "ledger":
            class Ledger:
                def get_item(self, **_kwargs):
                    return {}
            return Ledger()
        return _FakeTable()


boto3_stub = types.ModuleType("boto3")
boto3_stub.client = lambda *args, **kwargs: object()
boto3_stub.resource = lambda *args, **kwargs: _FakeResource()
sys.modules["boto3"] = boto3_stub
botocore_ex = types.ModuleType("botocore.exceptions")
class _FakeClientError(Exception):
    def __init__(self, response=None):
        super().__init__("client error")
        self.response = response or {}
botocore_ex.ClientError = _FakeClientError
sys.modules["botocore.exceptions"] = botocore_ex

import app  # noqa: E402
from errors import AppError  # noqa: E402


class ConversationAnalysisHandlerTests(unittest.TestCase):
    def event(self):
        return {"requestContext": {"authorizer": {"jwt": {"claims": _claims("user-123")}}},
                "headers": {"X-Device-Binding-Fingerprint": "fp-1"},
                "body": json.dumps({"schemaVersion": "1.0", "requestId": "request-123",
                    "sourceType": "pasted_text", "localSanitizationApplied": True,
                    "sanitizedText": "private-marker", "entities": []})}

    def test_owned_replay_is_the_only_dispatch(self):
        expected = {"requestId": "request-123", "summary": "Previously completed"}
        with mock.patch.object(app, "LegacyReplay") as replay:
            replay.return_value.replay.return_value = expected
            response = app.lambda_handler(self.event(), None)
            replay.return_value.replay.assert_called_once()
            self.assertEqual(replay.return_value.replay.call_args.args[1], "user-123")
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(json.loads(response["body"]), expected)
        self.assertEqual(response["headers"]["Cache-Control"], "no-store")
        self.assertFalse(hasattr(app, "handle_analysis_request"))
        self.assertFalse(hasattr(app, "analyze_conversation"))

    def test_invalid_identity_and_input_never_read_replay(self):
        cases = [(dict(self.event(), requestContext={}), 401),
                 (dict(self.event(), body="private-marker"), 400),
                 (dict(self.event(), body="x" * 70000), 400)]
        for event, status in cases:
            with self.subTest(status=status), mock.patch.object(app, "LegacyReplay") as replay:
                response = app.lambda_handler(event, None)
                replay.assert_not_called()
                self.assertEqual(response["statusCode"], status)
                self.assertNotIn("private-marker", response["body"])

    def test_reconciliation_is_not_retryable_or_new_work(self):
        with mock.patch.object(app, "LegacyReplay") as replay:
            replay.return_value.replay.side_effect = AppError("LEGACY_RECONCILIATION_REQUIRED", "Reconciliation required.", retryable=False)
            response = app.lambda_handler(self.event(), None)
        self.assertEqual(response["statusCode"], 409)
        self.assertFalse(json.loads(response["body"])["error"]["retryable"])

    def test_sdk_failure_does_not_echo_or_log_details(self):
        with mock.patch.object(app, "LegacyReplay", side_effect=RuntimeError("private-marker secret")), mock.patch("builtins.print") as output:
            response = app.lambda_handler(self.event(), None)
        self.assertEqual(response["statusCode"], 503)
        self.assertNotIn("private-marker", response["body"])
        self.assertNotIn("secret", response["body"])
        output.assert_not_called()


if __name__ == "__main__":
    unittest.main()
