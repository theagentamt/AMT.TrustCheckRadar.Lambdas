import json
import sys
import unittest
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "conversation_analysis"
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from errors import AppError  # noqa: E402
from validation import parse_and_validate_event  # noqa: E402


class ConversationAnalysisValidationTests(unittest.TestCase):
    def test_parses_valid_payload(self):
        event = {
            "body": json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": "request-123",
                    "sourceType": "mixed",
                    "localSanitizationApplied": True,
                    "sanitizedText": "Sanitized content",
                    "entities": [{"token": "[TOKEN_1]", "type": "messenger_handle"}],
                }
            )
        }

        payload = parse_and_validate_event(event)

        self.assertEqual(payload["requestId"], "request-123")
        self.assertEqual(payload["entities"][0]["token"], "[TOKEN_1]")

    def test_rejects_unsanitized_payload(self):
        event = {
            "body": json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": "request-123",
                    "sourceType": "mixed",
                    "localSanitizationApplied": False,
                    "sanitizedText": "Sanitized content",
                    "entities": [],
                }
            )
        }

        with self.assertRaises(AppError) as context:
            parse_and_validate_event(event)

        self.assertEqual(context.exception.code, "INVALID_REQUEST")
        self.assertEqual(context.exception.details[0]["field"], "localSanitizationApplied")

    def test_rejects_too_many_entities(self):
        event = {
            "body": json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": "request-123",
                    "sourceType": "mixed",
                    "localSanitizationApplied": True,
                    "sanitizedText": "Sanitized content",
                    "entities": [{"token": f"[TOKEN_{index}]", "type": "payment_handle"} for index in range(101)],
                }
            )
        }

        with self.assertRaises(AppError) as context:
            parse_and_validate_event(event)

        self.assertEqual(context.exception.code, "INVALID_REQUEST")
        self.assertEqual(context.exception.details[0]["field"], "entities")

    def test_rejects_oversized_sanitized_text(self):
        event = {
            "body": json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": "request-123",
                    "sourceType": "mixed",
                    "localSanitizationApplied": True,
                    "sanitizedText": "a" * 8001,
                    "entities": [],
                }
            )
        }

        with self.assertRaises(AppError) as context:
            parse_and_validate_event(event)

        self.assertEqual(context.exception.code, "INVALID_REQUEST")
        self.assertEqual(context.exception.details[0]["field"], "sanitizedText")

    def test_rejects_oversized_request_body_dict(self):
        event = {
            "body": {
                "schemaVersion": "1.0",
                "requestId": "request-123",
                "sourceType": "mixed",
                "localSanitizationApplied": True,
                "sanitizedText": "a" * 70000,
                "entities": [],
            }
        }

        with self.assertRaises(AppError) as context:
            parse_and_validate_event(event)

        self.assertEqual(context.exception.code, "INVALID_REQUEST")
        self.assertEqual(context.exception.details[0]["field"], "body")


if __name__ == "__main__":
    unittest.main()
