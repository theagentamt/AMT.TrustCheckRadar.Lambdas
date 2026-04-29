import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "conversation_analysis"
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

boto3_stub = types.ModuleType("boto3")
boto3_stub.client = lambda *args, **kwargs: object()
sys.modules.setdefault("boto3", boto3_stub)

import app  # noqa: E402
from errors import AppError  # noqa: E402


class ConversationAnalysisHandlerTests(unittest.TestCase):
    def test_returns_success_contract(self):
        event = {
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

        with mock.patch.object(app, "handle_analysis_request", return_value={
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

        with mock.patch.object(
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


if __name__ == "__main__":
    unittest.main()
