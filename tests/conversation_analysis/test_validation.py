import json
import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
MODULE_DIR = SRC_DIR / "conversation_analysis"
for path in (SRC_DIR, MODULE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

for module_name in ["config", "errors", "validation"]:
    sys.modules.pop(module_name, None)

from errors import AppError  # noqa: E402
from validation import parse_and_validate_event  # noqa: E402


APP_FEATURES = {
    "schemaVersion": 1,
    "extractorVersion": "android-1.0.0",
    "languageId": "en",
    "taxonomyBucket": "advance_fee",
    "vector": [1.0, 0.0],
    "lexicalFingerprint": ["0123456789abcdef"],
    "signalIds": ["payment_request"],
    "indicatorIds": ["payment.crypto"],
    "confidence": 0.9,
}


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
        self.assertFalse(payload["campaignConsentGranted"])

    def test_accepts_explicit_campaign_opt_in(self):
        event = {
            "body": json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": "request-123",
                    "sourceType": "mixed",
                    "localSanitizationApplied": True,
                    "sanitizedText": "Sanitized content",
                    "entities": [],
                    "campaignConsentGranted": True,
                    "appFeatures": APP_FEATURES,
                }
            )
        }

        payload = parse_and_validate_event(event)

        self.assertTrue(payload["campaignConsentGranted"])
        self.assertEqual(payload["appFeatures"], APP_FEATURES)

    def test_opt_in_requires_app_features(self):
        event = {
            "body": json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": "request-123",
                    "sourceType": "mixed",
                    "localSanitizationApplied": True,
                    "sanitizedText": "Sanitized content",
                    "entities": [],
                    "campaignConsentGranted": True,
                }
            )
        }

        with self.assertRaises(AppError) as context:
            parse_and_validate_event(event)

        self.assertEqual(context.exception.details[0]["field"], "appFeatures")

    def test_rejects_malformed_app_features(self):
        invalid_values = [
            APP_FEATURES | {"unknown": "value"},
            APP_FEATURES | {"vector": [float("nan")]},
            APP_FEATURES | {"confidence": True},
            APP_FEATURES | {"signalIds": ["payment_request", "payment_request"]},
        ]
        for app_features in invalid_values:
            with self.subTest(app_features=app_features):
                event = {
                    "body": {
                        "schemaVersion": "1.0",
                        "requestId": "request-123",
                        "sourceType": "mixed",
                        "localSanitizationApplied": True,
                        "sanitizedText": "Sanitized content",
                        "entities": [],
                        "campaignConsentGranted": True,
                        "appFeatures": app_features,
                    }
                }

                with self.assertRaises(AppError) as context:
                    parse_and_validate_event(event)

                self.assertEqual(context.exception.details[0]["field"], "appFeatures")

    def test_declined_consent_accepts_features_but_does_not_change_consent(self):
        event = {
            "body": {
                "schemaVersion": "1.0",
                "requestId": "request-123",
                "sourceType": "mixed",
                "localSanitizationApplied": True,
                "sanitizedText": "Sanitized content",
                "entities": [],
                "campaignConsentGranted": False,
                "appFeatures": APP_FEATURES,
            }
        }

        payload = parse_and_validate_event(event)

        self.assertFalse(payload["campaignConsentGranted"])
        self.assertEqual(payload["appFeatures"], APP_FEATURES)

    def test_rejects_non_boolean_campaign_consent(self):
        event = {
            "body": json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": "request-123",
                    "sourceType": "mixed",
                    "localSanitizationApplied": True,
                    "sanitizedText": "Sanitized content",
                    "entities": [],
                    "campaignConsentGranted": "yes",
                }
            )
        }

        with self.assertRaises(AppError) as context:
            parse_and_validate_event(event)

        self.assertEqual(context.exception.details[0]["field"], "campaignConsentGranted")

    def test_parses_valid_ocr_source_type(self):
        event = {
            "body": json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": "request-ocr-123",
                    "sourceType": "ocr",
                    "localSanitizationApplied": True,
                    "sanitizedText": "Sanitized OCR content",
                    "entities": [],
                }
            )
        }

        payload = parse_and_validate_event(event)

        self.assertEqual(payload["sourceType"], "ocr")

    def test_parses_valid_pasted_text_source_type(self):
        event = {
            "body": json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": "request-pasted-123",
                    "sourceType": "pasted_text",
                    "localSanitizationApplied": True,
                    "sanitizedText": "Sanitized pasted content",
                    "entities": [],
                }
            )
        }

        payload = parse_and_validate_event(event)

        self.assertEqual(payload["sourceType"], "pasted_text")

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

    def test_rejects_invalid_request_id_format(self):
        event = {
            "body": json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": "bad request id!",
                    "sourceType": "mixed",
                    "localSanitizationApplied": True,
                    "sanitizedText": "Sanitized content",
                    "entities": [],
                }
            )
        }

        with self.assertRaises(AppError) as context:
            parse_and_validate_event(event)

        self.assertEqual(context.exception.code, "INVALID_REQUEST")
        self.assertEqual(context.exception.details[0]["field"], "requestId")

    def test_rejects_unsupported_entity_type(self):
        event = {
            "body": json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": "request-123",
                    "sourceType": "mixed",
                    "localSanitizationApplied": True,
                    "sanitizedText": "Sanitized content",
                    "entities": [{"token": "[PERSON_NAME_1]", "type": "person_name"}],
                }
            )
        }

        with self.assertRaises(AppError) as context:
            parse_and_validate_event(event)

        self.assertEqual(context.exception.code, "INVALID_REQUEST")
        self.assertEqual(context.exception.details[0]["field"], "entities[0].type")


if __name__ == "__main__":
    unittest.main()
