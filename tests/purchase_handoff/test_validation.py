import json
import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
MODULE_DIR = SRC_DIR / "purchase_handoff"
for path in (SRC_DIR, MODULE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

for module_name in ["config", "errors", "validation"]:
    sys.modules.pop(module_name, None)

from errors import AppError  # noqa: E402
from validation import parse_and_validate_event  # noqa: E402


class PurchaseHandoffValidationTests(unittest.TestCase):
    def test_parses_valid_google_play_payload(self):
        event = {
            "body": json.dumps(
                {
                    "platform": "google_play",
                    "productId": "trustcheck_radar_pro_monthly",
                    "purchaseToken": "purchase-token-12345",
                    "purchaseState": "PURCHASED",
                    "packageName": "com.example.app",
                    "orderId": "GPA.1234-5678-9012-34567",
                    "purchaseTime": "2026-06-27T19:15:00Z",
                }
            )
        }

        payload = parse_and_validate_event(event)

        self.assertEqual(payload["platform"], "google_play")
        self.assertEqual(payload["productId"], "trustcheck_radar_pro_monthly")
        self.assertEqual(payload["purchaseState"], "PURCHASED")

    def test_rejects_non_google_play_platform(self):
        event = {
            "body": json.dumps(
                {
                    "platform": "ios",
                    "productId": "trustcheck_radar_pro_monthly",
                    "purchaseToken": "purchase-token-12345",
                    "packageName": "com.example.app",
                }
            )
        }

        with self.assertRaises(AppError) as context:
            parse_and_validate_event(event)

        self.assertEqual(context.exception.code, "INVALID_REQUEST")
        self.assertEqual(context.exception.details[0]["field"], "platform")

    def test_rejects_missing_purchase_token(self):
        event = {
            "body": json.dumps(
                {
                    "platform": "google_play",
                    "productId": "trustcheck_radar_pro_monthly",
                    "packageName": "com.example.app",
                }
            )
        }

        with self.assertRaises(AppError) as context:
            parse_and_validate_event(event)

        self.assertEqual(context.exception.code, "INVALID_REQUEST")
        self.assertEqual(context.exception.details[0]["field"], "purchaseToken")

    def test_rejects_malformed_purchase_time(self):
        event = {
            "body": json.dumps(
                {
                    "platform": "google_play",
                    "productId": "trustcheck_radar_pro_monthly",
                    "purchaseToken": "purchase-token-12345",
                    "purchaseTime": "yesterday",
                    "packageName": "com.example.app",
                }
            )
        }

        with self.assertRaises(AppError) as context:
            parse_and_validate_event(event)

        self.assertEqual(context.exception.code, "INVALID_REQUEST")
        self.assertEqual(context.exception.details[0]["field"], "purchaseTime")

    def test_rejects_invalid_purchase_state(self):
        event = {
            "body": json.dumps(
                {
                    "platform": "google_play",
                    "productId": "trustcheck_radar_pro_monthly",
                    "purchaseToken": "purchase-token-12345",
                    "purchaseState": "UNKNOWN",
                    "packageName": "com.example.app",
                }
            )
        }

        with self.assertRaises(AppError) as context:
            parse_and_validate_event(event)

        self.assertEqual(context.exception.code, "INVALID_REQUEST")
        self.assertEqual(context.exception.details[0]["field"], "purchaseState")


if __name__ == "__main__":
    unittest.main()
