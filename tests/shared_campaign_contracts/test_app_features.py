import json
import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from shared_campaign_contracts import (  # noqa: E402
    APP_FEATURE_FIELDS,
    AppFeaturesContractError,
    canonical_app_features_json,
    validate_app_features,
)
from shared_campaign_contracts import app_features as app_features_contract  # noqa: E402


def valid_features(**overrides):
    value = {
        "schemaVersion": 1,
        "extractorVersion": "android-1.0.0",
        "languageId": "en",
        "taxonomyBucket": "advance_fee",
        "vector": [1.0, 0.0, -1.0],
        "lexicalFingerprint": ["0123456789abcdef"],
        "signalIds": ["payment_request"],
        "indicatorIds": ["payment.crypto"],
        "confidence": 0.9,
    }
    value.update(overrides)
    return value


class AppFeaturesContractTests(unittest.TestCase):
    def test_accepts_and_canonically_encodes_exact_v1_contract(self):
        result = validate_app_features(valid_features())

        self.assertEqual(set(result), APP_FEATURE_FIELDS)
        self.assertEqual(
            canonical_app_features_json(result),
            json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        )

    def test_rejects_missing_unknown_and_unsupported_version(self):
        cases = [
            {key: value for key, value in valid_features().items() if key != "vector"},
            valid_features(unknown="value"),
            valid_features(schemaVersion=2),
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(AppFeaturesContractError):
                validate_app_features(value)

    def test_rejects_malformed_identifiers_and_oversized_version(self):
        cases = [
            valid_features(extractorVersion="x" * 65),
            valid_features(extractorVersion="\ud800"),
            valid_features(languageId="EN"),
            valid_features(languageId="fr"),
            valid_features(taxonomyBucket="advance-fee"),
            valid_features(taxonomyBucket="unapproved_category"),
            valid_features(signalIds=["Bad Signal"]),
            valid_features(indicatorIds=["bad indicator"]),
            valid_features(lexicalFingerprint=["ABCDEF0123456789"]),
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(AppFeaturesContractError):
                validate_app_features(value)

    def test_rejects_vector_shape_nonfinite_boolean_and_range_errors(self):
        cases = [
            valid_features(vector=[]),
            valid_features(vector=[0.0] * 385),
            valid_features(vector=[True]),
            valid_features(vector=[float("nan")]),
            valid_features(vector=[float("inf")]),
            valid_features(vector=[-float("inf")]),
            valid_features(vector=[1.01]),
            valid_features(confidence=True),
            valid_features(confidence=float("nan")),
            valid_features(confidence=-0.01),
        ]
        for value in cases:
            with self.subTest(), self.assertRaises(AppFeaturesContractError):
                validate_app_features(value)

    def test_rejects_duplicate_and_oversized_lists(self):
        cases = [
            valid_features(lexicalFingerprint=["0123456789abcdef"] * 2),
            valid_features(signalIds=["payment_request"] * 2),
            valid_features(indicatorIds=["payment.crypto"] * 2),
            valid_features(lexicalFingerprint=[f"{index:016x}" for index in range(33)]),
            valid_features(signalIds=[f"signal_{index}" for index in range(17)]),
            valid_features(indicatorIds=[f"indicator_{index}" for index in range(17)]),
        ]
        for value in cases:
            with self.subTest(), self.assertRaises(AppFeaturesContractError):
                validate_app_features(value)

    def test_enforces_canonical_compact_json_size_limit(self):
        original = app_features_contract.APP_FEATURE_MAX_BYTES
        try:
            app_features_contract.APP_FEATURE_MAX_BYTES = 32
            with self.assertRaisesRegex(AppFeaturesContractError, "32768 bytes"):
                validate_app_features(valid_features())
        finally:
            app_features_contract.APP_FEATURE_MAX_BYTES = original


if __name__ == "__main__":
    unittest.main()
