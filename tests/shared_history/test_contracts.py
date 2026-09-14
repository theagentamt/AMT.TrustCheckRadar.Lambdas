import os
import sys
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from shared_history import HistoryError, HistorySettings, build_history_items


BASE_ENV = {
    "APP_ENVIRONMENT": "dev",
    "HISTORY_CONTENT_TABLE_NAME": "trustcheckradar-dev-history-content",
    "HISTORY_CONTROL_TABLE_NAME": "trustcheckradar-dev-history-control",
    "ANALYSIS_ABUSE_TABLE_NAME": "trustcheckradar-dev-analysis-abuse",
    "DEVICE_BINDINGS_TABLE_NAME": "trustcheckradar-dev-device-bindings",
    "HISTORY_SCHEMA_VERSION": "1",
    "HISTORY_RETENTION_DAYS": "90",
    "HISTORY_ERASURE_SLA_HOURS": "24",
    "HISTORY_PITR_POLICY_APPROVED": "true",
    "HISTORY_CONTROL_RETENTION_POLICY_APPROVED": "true",
    "HISTORY_DEDUP_RETENTION_DAYS": "400",
    "HISTORY_MUTATION_RETENTION_DAYS": "400",
    "HISTORY_MAX_SUMMARY_BYTES": "2048",
    "HISTORY_MAX_LIST_ITEMS": "20",
    "HISTORY_MAX_TEXT_FIELD_BYTES": "512",
}


class HistoryContractTests(unittest.TestCase):
    def test_all_features_default_disabled(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = HistorySettings.from_env()
        self.assertFalse(settings.reads_enabled)
        self.assertFalse(settings.writes_enabled)
        self.assertFalse(settings.mutations_enabled)
        self.assertFalse(settings.recognition_enabled)
        self.assertFalse(settings.lifecycle_enabled)
        self.assertFalse(settings.durable_replay_enabled)
        with self.assertRaisesRegex(HistoryError, "not enabled"):
            settings.validate_reads()

    def test_pending_api_contract_blocks_activation(self):
        env = BASE_ENV | {"HISTORY_READS_ENABLED": "true"}
        with mock.patch.dict(os.environ, env, clear=True):
            settings = HistorySettings.from_env()
        with self.assertRaisesRegex(HistoryError, "not approved"):
            settings.validate_reads()

    def test_history_record_has_exact_privacy_allowlist_and_90_day_expiry(self):
        with mock.patch.dict(os.environ, BASE_ENV | {"HISTORY_WRITES_ENABLED": "true", "HISTORY_DURABLE_REPLAY_ENABLED": "true"}, clear=True):
            settings = HistorySettings.from_env()
            settings.validate_writes()
        content, locator = build_history_items(
            account_id="account-1",
            payload_hash="a" * 64,
            accepted={
                "historyGeneration": 3,
                "recognitionGeneration": 2,
                "acceptedSequence": 9,
                "acceptedAtEpochMs": 1_700_000_000_000,
            },
            payload={
                "requestId": "request-1", "sourceType": "ocr",
                "sanitizedText": "must never be copied", "entities": [{"value": "secret"}],
            },
            response={
                "schemaVersion": "1.0", "requestId": "request-1", "scamScore": 88,
                "riskLevel": "high", "confidence": 0.9, "summary": "Likely fraud.",
                "signals": ["urgent_payment"], "recommendedActions": ["Do not pay."],
            },
            now_epoch=1_700_000_100,
            settings=settings,
        )
        self.assertEqual(content["expiresAt"], 1_700_000_100 + 90 * 86400)
        self.assertEqual(content["PK"], "USER#account-1#HISTORY#3")
        self.assertEqual(locator["SK"], "REQUEST#request-1")
        serialized = repr((content, locator))
        for forbidden in ("must never be copied", "secret", "sanitizedText", "entities"):
            self.assertNotIn(forbidden, serialized)

    def test_response_extra_field_is_rejected(self):
        with mock.patch.dict(os.environ, BASE_ENV | {"HISTORY_WRITES_ENABLED": "true", "HISTORY_DURABLE_REPLAY_ENABLED": "true"}, clear=True):
            settings = HistorySettings.from_env()
        response = {
            "schemaVersion": "1.0", "requestId": "request-1", "scamScore": 10,
            "riskLevel": "low", "confidence": 0.8, "summary": "Low risk.",
            "signals": [], "recommendedActions": ["Stay alert."], "rawText": "no",
        }
        with self.assertRaises(HistoryError):
            build_history_items(
                account_id="a", payload_hash="b" * 64,
                accepted={"historyGeneration": 0, "recognitionGeneration": 0, "acceptedSequence": 1, "acceptedAtEpochMs": 1},
                payload={"requestId": "request-1", "sourceType": "ocr"}, response=response,
                now_epoch=10, settings=settings,
            )


if __name__ == "__main__":
    unittest.main()
