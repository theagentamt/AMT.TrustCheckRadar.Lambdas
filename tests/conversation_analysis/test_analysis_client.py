import sys
import types
import unittest
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "conversation_analysis"
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

boto3_stub = types.ModuleType("boto3")
boto3_stub.client = lambda *args, **kwargs: object()
boto3_stub.resource = lambda *args, **kwargs: object()
sys.modules.setdefault("boto3", boto3_stub)

from analysis_client import _validate_analysis  # noqa: E402
from errors import AppError  # noqa: E402


class ConversationAnalysisClientValidationTests(unittest.TestCase):
    def test_accepts_bounded_valid_analysis(self):
        result = _validate_analysis(
            {
                "scamScore": 72,
                "riskLevel": "high",
                "confidence": 0.84,
                "summary": "This message shows strong scam indicators, including payment pressure and off-platform escalation.",
                "signals": ["payment_pressure", "off_platform_contact"],
                "recommendedActions": [
                    "Do not send money or gift cards.",
                    "Verify the sender through a trusted contact method.",
                ],
            }
        )

        self.assertEqual(result["scamScore"], 72)
        self.assertEqual(result["riskLevel"], "high")

    def test_rejects_non_snake_case_signal(self):
        with self.assertRaises(AppError):
            _validate_analysis(
                {
                    "scamScore": 40,
                    "riskLevel": "medium",
                    "confidence": 0.6,
                    "summary": "Suspicious indicators are present.",
                    "signals": ["PaymentPressure"],
                    "recommendedActions": ["Verify the sender before responding."],
                }
            )

    def test_rejects_empty_or_oversized_recommended_actions(self):
        with self.assertRaises(AppError):
            _validate_analysis(
                {
                    "scamScore": 40,
                    "riskLevel": "medium",
                    "confidence": 0.6,
                    "summary": "Suspicious indicators are present.",
                    "signals": ["payment_pressure"],
                    "recommendedActions": ["   "],
                }
            )

        with self.assertRaises(AppError):
            _validate_analysis(
                {
                    "scamScore": 40,
                    "riskLevel": "medium",
                    "confidence": 0.6,
                    "summary": "Suspicious indicators are present.",
                    "signals": ["payment_pressure"],
                    "recommendedActions": ["a" * 181],
                }
            )


if __name__ == "__main__":
    unittest.main()
