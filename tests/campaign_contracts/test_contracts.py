import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts" / "campaign" / "v1"
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from shared_campaign_contracts import (  # noqa: E402
    APP_FEATURE_MAX_BYTES,
    LANGUAGE_IDS,
    TAXONOMY_BUCKETS,
    AppFeaturesContractError,
    canonical_app_features_json,
    validate_app_features,
)


def load(name):
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


SCHEMA_NAMES = (
    "app-features.schema.json",
    "outbox-record.schema.json",
    "queue-envelope.schema.json",
    "transient-feature-record.schema.json",
    "campaign-aggregate.schema.json",
    "review-transition.schema.json",
    "scam-trends-response.schema.json",
)
SCHEMAS = {name: load(name) for name in SCHEMA_NAMES}
REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema)) for schema in SCHEMAS.values()
)


def validate(schema_name, instance):
    Draft202012Validator(
        SCHEMAS[schema_name],
        registry=REGISTRY,
        format_checker=FormatChecker(),
    ).validate(instance)


class CampaignContractTests(unittest.TestCase):
    def test_every_schema_is_valid_draft_2020_12(self):
        for name, schema in SCHEMAS.items():
            with self.subTest(name=name):
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
                self.assertIn("/1.0.0/", schema["$id"])
                Draft202012Validator.check_schema(schema)

    def test_bilingual_taxonomy_has_identical_complete_machine_ids(self):
        english = load("taxonomy.en.json")
        spanish = load("taxonomy.es.json")
        expected_dimensions = {
            "scam_category",
            "claimed_organization",
            "requested_action",
            "payment_method",
            "emotional_tactic",
            "channel",
            "language",
            "risk",
            "confidence",
            "bounded_indicator",
        }
        self.assertEqual(set(english["dimensions"]), expected_dimensions)
        self.assertEqual(set(spanish["dimensions"]), expected_dimensions)
        for dimension in expected_dimensions:
            with self.subTest(dimension=dimension):
                english_ids = set(english["dimensions"][dimension]["values"])
                spanish_ids = set(spanish["dimensions"][dimension]["values"])
                self.assertEqual(english_ids, spanish_ids)
                self.assertIn("other", english_ids)
                self.assertIn("unknown", english_ids)
        self.assertEqual(
            TAXONOMY_BUCKETS,
            frozenset(english["dimensions"]["scam_category"]["values"]),
        )
        self.assertEqual(
            LANGUAGE_IDS,
            frozenset(english["dimensions"]["language"]["values"]),
        )

    def test_valid_english_and_spanish_app_fixtures_match_schema_and_runtime(self):
        for locale in ("en", "es"):
            value = load(f"fixtures/app-features.valid.{locale}.json")
            with self.subTest(locale=locale):
                validate("app-features.schema.json", value)
                self.assertEqual(validate_app_features(value), value)
                self.assertLessEqual(
                    len(canonical_app_features_json(value).encode("utf-8")),
                    APP_FEATURE_MAX_BYTES,
                )

    def test_invalid_app_fixtures_are_rejected_by_schema_and_runtime(self):
        for path in sorted((CONTRACTS / "fixtures").glob("app-features.invalid.*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            with self.subTest(path=path.name):
                self.assertFalse(Draft202012Validator(SCHEMAS["app-features.schema.json"]).is_valid(value))
                with self.assertRaises(AppFeaturesContractError):
                    validate_app_features(value)

    def test_app_feature_runtime_rejects_nonfinite_and_oversized_payloads(self):
        valid = load("fixtures/app-features.valid.en.json")
        for value in (valid | {"confidence": float("nan")}, valid | {"vector": [float("inf")]}) :
            with self.subTest(value=value), self.assertRaises(AppFeaturesContractError):
                validate_app_features(value)
        oversized = valid | {"extractorVersion": "x" * (APP_FEATURE_MAX_BYTES + 1)}
        with self.assertRaises(AppFeaturesContractError):
            validate_app_features(oversized)

    def test_transport_and_storage_contracts_accept_only_approved_shapes(self):
        app_features = load("fixtures/app-features.valid.en.json")
        event_id = "7fbce2ac-bd2e-4d2e-9ec6-1f895a482abc"
        epoch_id = "15c81ba4-2fa6-43c3-8895-889f08c931bf"
        outbox = {
            "PK": f"EVENT#{event_id}", "SK": "OBSERVATION_READY", "schemaVersion": 1,
            "recordVersion": 1, "eventType": "campaign.observation.ready", "environment": "dev",
            "statisticsEventId": event_id, "accountId": "protected-account", "campaignConsentGranted": True,
            "consentEpochId": epoch_id, "noticeVersion": "notice-1", "observedAtEpoch": 1_780_000_000,
            "sourceType": "pasted_text", "sanitizedText": "A sanitized payment request.",
            "riskLevel": "high", "signalIds": ["payment_request"], "appFeatures": app_features,
            "expiresAt": 1_780_259_200,
        }
        validate("outbox-record.schema.json", outbox)
        self.assertLessEqual(outbox["expiresAt"] - outbox["observedAtEpoch"], 72 * 3600)
        validate("queue-envelope.schema.json", {
            "schemaVersion": 1, "eventType": "campaign.cluster.requested", "environment": "dev",
            "statisticsEventId": event_id, "recordVersion": 1,
        })
        transient = {
            "PK": f"EVENT#{event_id}", "SK": "FEATURE", "schemaVersion": 1, "recordVersion": 1,
            "environment": "dev", "statisticsEventId": event_id, "periodId": 1471,
            "contributorToken": "a" * 43, "GSI1PK": "CONTRIB#1471#token", "GSI1SK": f"EVENT#{event_id}#FEATURE",
            "GSI3PK": "EXPIRY#dev", "GSI3SK": 1_781_814_400,
            **{key: value for key, value in app_features.items() if key != "schemaVersion"},
            "expiresAt": 1_781_814_400,
        }
        validate("transient-feature-record.schema.json", transient)

    def test_aggregate_review_and_response_contracts_exclude_identity_and_content(self):
        aggregate = {
            "PK": "CAMPAIGN#c1", "SK": "AGGREGATE", "campaignId": "c1", "schemaVersion": 1,
            "taxonomyVersion": 1, "categoryId": "advance_fee", "periodWeek": "2026-W35",
            "state": "PUBLISHED", "contributorCount": 10, "submissionCount": 12,
            "contributorCountBand": "10-24", "submissionCountBand": "10-24",
            "dimensionSchemaVersion": 1, "languageIds": ["en"], "tacticIds": ["urgency"],
            "channelIds": ["sms"], "riskBand": "high", "summaryKey": "campaign.advance_fee",
            "trendDirection": "new", "expiresAt": 2_000_000_000, "version": 3,
            "environment": "dev", "GSI1PK": "STATE#PUBLISHED",
            "GSI1SK": "2026-W35#CAMPAIGN#c1",
        }
        validate("campaign-aggregate.schema.json", aggregate)
        validate("review-transition.schema.json", {
            "schemaVersion": 1, "action": "publish", "reasonCode": "privacy_verified",
            "reason": "Threshold and privacy evidence were reviewed.",
        })
        response = {
            "schemaVersion": 1, "locale": "en", "nextToken": None,
            "trends": [{
                "campaignId": "c1", "categoryId": "advance_fee", "categoryLabel": "Advance-fee scam",
                "periodWeek": "2026-W35", "riskBand": "high", "contributorCountBand": "10-24",
                "submissionCountBand": "10-24", "languageIds": ["en"], "tacticIds": ["urgency"],
                "channelIds": ["sms"], "summaryKey": "campaign.advance_fee", "trendDirection": "new",
            }],
        }
        validate("scam-trends-response.schema.json", response)
        serialized = json.dumps(response).lower()
        for prohibited in ("accountid", "requestid", "deviceid", "contributortoken", "sanitizedtext", "vector"):
            self.assertNotIn(prohibited, serialized)


if __name__ == "__main__":
    unittest.main()
