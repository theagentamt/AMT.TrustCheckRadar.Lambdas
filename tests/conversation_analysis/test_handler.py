import json
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
    def test_returns_success_contract(self):
        event = {
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-123"}}}},
            "headers": {"X-Device-Binding-Fingerprint": "fp-1"},
            "body": json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": "request-123",
                    "sourceType": "mixed",
                    "localSanitizationApplied": True,
                    "sanitizedText": "Sanitized content",
                    "entities": [{"token": "[PAYMENT_HANDLE_1]", "type": "payment_handle"}],
                }
            )
        }

        with mock.patch.object(app, "assert_active_device_binding"), \
             mock.patch.object(app, "handle_analysis_request", return_value={
                "schemaVersion": "1.0",
                "requestId": "request-123",
                "scamScore": 72,
                "riskLevel": "high",
                "confidence": 0.84,
                "summary": "Strong scam indicators detected.",
                "signals": ["payment_request"],
                "recommendedActions": ["Do not send money."],
            }):
            response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(body["requestId"], "request-123")
        self.assertEqual(body["scamScore"], 72)
        self.assertEqual(body["signals"], ["payment_request"])

    def test_returns_structured_validation_error(self):
        event = {
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-123"}}}},
            "headers": {"X-Device-Binding-Fingerprint": "fp-1"},
            "body": json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": "request-123",
                    "sourceType": "voice_note",
                    "localSanitizationApplied": True,
                    "sanitizedText": "Sanitized content",
                    "entities": [],
                }
            )
        }

        response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertEqual(body["requestId"], None)
        self.assertEqual(body["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(body["error"]["details"][0]["field"], "sourceType")

    def test_returns_unsupported_schema_error(self):
        event = {
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-123"}}}},
            "headers": {"X-Device-Binding-Fingerprint": "fp-1"},
            "body": json.dumps(
                {
                    "schemaVersion": "2.0",
                    "requestId": "request-123",
                    "sourceType": "mixed",
                    "localSanitizationApplied": True,
                    "sanitizedText": "Sanitized content",
                    "entities": [],
                }
            )
        }

        response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertEqual(body["error"]["code"], "UNSUPPORTED_SCHEMA_VERSION")

    def test_returns_service_error_with_request_id(self):
        event = {
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-123"}}}},
            "headers": {"X-Device-Binding-Fingerprint": "fp-1"},
            "body": json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": "request-123",
                    "sourceType": "mixed",
                    "localSanitizationApplied": True,
                    "sanitizedText": "Sanitized content",
                    "entities": [],
                }
            )
        }

        with mock.patch.object(app, "assert_active_device_binding"), \
             mock.patch.object(
                app,
                "handle_analysis_request",
                side_effect=AppError("ANALYSIS_TIMEOUT", "The analysis service timed out.", retryable=True),
            ):
            response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 504)
        body = json.loads(response["body"])
        self.assertEqual(body["requestId"], "request-123")
        self.assertEqual(body["error"]["code"], "ANALYSIS_TIMEOUT")
        self.assertTrue(body["error"]["retryable"])

    def test_returns_unauthorized_when_trusted_identity_is_missing(self):
        event = {
            "body": json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": "request-123",
                    "sourceType": "mixed",
                    "localSanitizationApplied": True,
                    "sanitizedText": "Sanitized content",
                    "entities": [],
                }
            )
        }

        response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 401)
        body = json.loads(response["body"])
        self.assertEqual(body["requestId"], "request-123")
        self.assertEqual(body["error"]["code"], "UNAUTHORIZED")
        self.assertFalse(body["error"]["retryable"])

    def test_returns_device_binding_required_when_header_missing(self):
        event = {
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-123"}}}},
            "body": json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": "request-123",
                    "sourceType": "mixed",
                    "localSanitizationApplied": True,
                    "sanitizedText": "Sanitized content",
                    "entities": [],
                }
            ),
        }

        response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 403)
        body = json.loads(response["body"])
        self.assertEqual(body["requestId"], "request-123")
        self.assertEqual(body["error"]["code"], "DEVICE_BINDING_REQUIRED")

    def test_returns_device_binding_mismatch_when_active_binding_differs(self):
        event = {
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-123"}}}},
            "headers": {"X-Device-Binding-Fingerprint": "fp-1"},
            "body": json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": "request-123",
                    "sourceType": "mixed",
                    "localSanitizationApplied": True,
                    "sanitizedText": "Sanitized content",
                    "entities": [],
                }
            ),
        }

        with mock.patch.object(
            app,
            "assert_active_device_binding",
            side_effect=AppError(
                "DEVICE_BINDING_MISMATCH",
                "The active device binding for this account does not match the presented device.",
                retryable=False,
            ),
        ):
            response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 403)
        body = json.loads(response["body"])
        self.assertEqual(body["requestId"], "request-123")
        self.assertEqual(body["error"]["code"], "DEVICE_BINDING_MISMATCH")

    def test_opted_in_request_logs_no_identity_text_or_app_features(self):
        app_features = {
            "schemaVersion": 1,
            "extractorVersion": "private-extractor-version",
            "languageId": "en",
            "taxonomyBucket": "advance_fee",
            "vector": [0.123456],
            "lexicalFingerprint": ["0123456789abcdef"],
            "signalIds": ["payment_request"],
            "indicatorIds": ["payment.crypto"],
            "confidence": 0.9,
        }
        event = {
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "private-account"}}}},
            "headers": {"X-Device-Binding-Fingerprint": "fp-1"},
            "body": {
                "schemaVersion": "1.0",
                "requestId": "request-123",
                "sourceType": "pasted_text",
                "localSanitizationApplied": True,
                "sanitizedText": "private sanitized text",
                "entities": [],
                "campaignConsentGranted": True,
                "appFeatures": app_features,
            },
        }

        with (
            mock.patch.object(app, "assert_active_device_binding"),
            mock.patch.object(
                app,
                "handle_analysis_request",
                return_value={"requestId": "request-123", "riskLevel": "high", "scamScore": 90},
            ),
            self.assertLogs(level="INFO") as captured,
        ):
            response = app.lambda_handler(event, None)

        self.assertEqual(response["statusCode"], 200)
        combined = " ".join(captured.output)
        for prohibited in (
            "private-account",
            "request-123",
            "private sanitized text",
            "private-extractor-version",
            "0.123456",
            "0123456789abcdef",
            "payment.crypto",
        ):
            self.assertNotIn(prohibited, combined)


if __name__ == "__main__":
    unittest.main()
