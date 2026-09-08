import base64
import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
MODULE_DIR = SRC_DIR / "campaign_observation_publisher"
for path in (SRC_DIR, MODULE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

for module_name in ["app", "config", "contracts", "service"]:
    sys.modules.pop(module_name, None)


class FakeClientError(Exception):
    def __init__(self, response=None, operation_name=None):
        super().__init__(operation_name or "client error")
        self.response = response or {}


botocore_exceptions = types.ModuleType("botocore.exceptions")
botocore_exceptions.ClientError = FakeClientError
sys.modules["botocore.exceptions"] = botocore_exceptions


class FakeDynamo:
    def __init__(self):
        self.item = None
        self.participation_item = {
            "state": {"S": "enrolled"},
            "consentEpochId": {"S": "15c81ba4-2fa6-43c3-8895-889f08c931bf"},
            "environment": {"S": "dev"},
            "noticeVersion": {"S": "2026-09-07"},
        }
        self.transactions = []
        self.updates = []

    def get_item(self, TableName=None, **_kwargs):
        if TableName == "users":
            return {"Item": self.participation_item} if self.participation_item else {}
        return {"Item": self.item} if self.item else {}

    def transact_write_items(self, **kwargs):
        self.transactions.append(kwargs["TransactItems"])
        self.item = {"status": {"S": "PENDING"}}
        return {}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        self.item = {"status": {"S": "PUBLISHED"}}
        return {}


class FakeKms:
    def __init__(self):
        self.calls = []

    def generate_mac(self, **kwargs):
        self.calls.append(kwargs)
        return {"Mac": b"x" * 32}


class FakeSqs:
    def __init__(self):
        self.calls = []

    def send_message(self, **kwargs):
        self.calls.append(kwargs)
        return {}


class FakeCloudWatch:
    def __init__(self):
        self.calls = []

    def put_metric_data(self, **kwargs):
        self.calls.append(kwargs)
        return {}


fake_dynamo = FakeDynamo()
fake_kms = FakeKms()
fake_sqs = FakeSqs()
fake_cloudwatch = FakeCloudWatch()


def fake_client(name):
    return {
        "dynamodb": fake_dynamo,
        "kms": fake_kms,
        "sqs": fake_sqs,
        "cloudwatch": fake_cloudwatch,
    }[name]


boto3_stub = types.ModuleType("boto3")
boto3_stub.client = fake_client
sys.modules["boto3"] = boto3_stub

os.environ.update(
    {
        "APP_ENVIRONMENT": "dev",
        "CAMPAIGN_SCHEMA_VERSION": "1",
        "PIPELINE_TABLE_NAME": "campaign-pipeline",
        "USERS_TABLE_NAME": "users",
        "CLUSTER_QUEUE_URL": "https://sqs.example/cluster",
    }
)

import app  # noqa: E402
import contracts  # noqa: E402
import service  # noqa: E402

AGGREGATOR_DIR = SRC_DIR / "campaign_cluster_aggregator"
if str(AGGREGATOR_DIR) not in sys.path:
    sys.path.insert(0, str(AGGREGATOR_DIR))
aggregator_spec = importlib.util.spec_from_file_location(
    "campaign_cluster_contract_service",
    AGGREGATOR_DIR / "service.py",
)
aggregator_service = importlib.util.module_from_spec(aggregator_spec)
aggregator_spec.loader.exec_module(aggregator_service)


EVENT_ID = "7fbce2ac-bd2e-4d2e-9ec6-1f895a482abc"
CONSENT_EPOCH_ID = "15c81ba4-2fa6-43c3-8895-889f08c931bf"


def valid_features(**overrides):
    value = {
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
    value.update(overrides)
    return value


def valid_item(**overrides):
    item = {
        "PK": f"EVENT#{EVENT_ID}",
        "SK": "OBSERVATION_READY",
        "schemaVersion": 1,
        "recordVersion": 1,
        "eventType": "campaign.observation.ready",
        "environment": "dev",
        "statisticsEventId": EVENT_ID,
        "accountId": "account-123",
        "campaignConsentGranted": True,
        "consentEpochId": CONSENT_EPOCH_ID,
        "noticeVersion": "2026-09-07",
        "observedAtEpoch": 1_780_000_000,
        "sourceType": "pasted_text",
        "sanitizedText": "A caller requested payment using [PAYMENT_HANDLE_1].",
        "riskLevel": "high",
        "signalIds": ["payment_request"],
        "appFeatures": valid_features(),
        "expiresAt": 1_780_259_200,
    }
    item.update(overrides)
    return item


def dynamodb_value(value):
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, int):
        return {"N": str(value)}
    if isinstance(value, float):
        return {"N": str(value)}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, list):
        return {"L": [dynamodb_value(item) for item in value]}
    if isinstance(value, dict):
        return {"M": {key: dynamodb_value(item) for key, item in value.items()}}
    raise TypeError(type(value).__name__)


def stream_event(item=None):
    item = valid_item() if item is None else item
    return {
        "Records": [
            {
                "eventName": "INSERT",
                "dynamodb": {
                    "NewImage": {key: dynamodb_value(value) for key, value in item.items()}
                },
            }
        ]
    }


class ContractTests(unittest.TestCase):
    def test_accepts_exact_environment_scoped_record(self):
        parsed = contracts.parse_stream_record(
            stream_event()["Records"][0],
            environment="dev",
            schema_version=1,
        )

        self.assertEqual(parsed, valid_item())

    def test_rejects_cross_environment_record(self):
        with self.assertRaisesRegex(contracts.ContractError, "another environment"):
            contracts.parse_stream_record(
                stream_event(valid_item(environment="prod"))["Records"][0],
                environment="dev",
                schema_version=1,
            )

    def test_rejects_unknown_and_prohibited_fields(self):
        for field in ("unexpected", "requestId", "email"):
            with self.subTest(field=field), self.assertRaises(contracts.ContractError):
                contracts.parse_stream_record(
                    stream_event(valid_item(**{field: "must-not-pass"}))["Records"][0],
                    environment="dev",
                    schema_version=1,
                )

    def test_rejects_non_v4_or_noncanonical_event_id(self):
        for event_id in (
            "00000000-0000-1000-8000-000000000000",
            "7FBCE2AC-BD2E-4D2E-9EC6-1F895A482ABC",
        ):
            with self.subTest(event_id=event_id), self.assertRaisesRegex(
                contracts.ContractError, "UUIDv4"
            ):
                contracts.parse_stream_record(
                    stream_event(
                        valid_item(
                            PK=f"EVENT#{event_id}",
                            statisticsEventId=event_id,
                        )
                    )["Records"][0],
                    environment="dev",
                    schema_version=1,
                )

    def test_cluster_envelope_contains_only_approved_fields(self):
        envelope = contracts.build_cluster_envelope(valid_item())

        self.assertEqual(
            set(envelope),
            {"schemaVersion", "eventType", "environment", "statisticsEventId", "recordVersion"},
        )
        self.assertNotIn("accountId", json.dumps(envelope))
        self.assertNotIn("sanitizedText", json.dumps(envelope))
        self.assertNotIn("appFeatures", json.dumps(envelope))

    def test_rejects_invalid_app_features(self):
        cases = [
            valid_features(schemaVersion=2),
            valid_features(vector=[float("nan")]),
            valid_features(confidence=True),
            valid_features(unknown="field"),
        ]
        for app_features in cases:
            with self.subTest(), self.assertRaisesRegex(
                contracts.ContractError, "appFeatures"
            ):
                contracts.parse_stream_record(
                    stream_event(valid_item(appFeatures=app_features))["Records"][0],
                    environment="dev",
                    schema_version=1,
                )

    def test_rejects_obvious_unsanitized_identifiers(self):
        for text in ("Email victim@example.com", "Call +1 (312) 555-0199"):
            with self.subTest(text=text), self.assertRaisesRegex(
                contracts.ContractError, "prohibited direct identifier"
            ):
                contracts.parse_stream_record(
                    stream_event(valid_item(sanitizedText=text))["Records"][0],
                    environment="dev",
                    schema_version=1,
                )


class ServiceTests(unittest.TestCase):
    def setUp(self):
        fake_dynamo.item = None
        fake_dynamo.participation_item = {
            "state": {"S": "enrolled"},
            "consentEpochId": {"S": CONSENT_EPOCH_ID},
            "environment": {"S": "dev"},
            "noticeVersion": {"S": "2026-09-07"},
        }
        fake_dynamo.transactions.clear()
        fake_dynamo.updates.clear()
        fake_kms.calls.clear()
        fake_sqs.calls.clear()

    def publish(self, item=None, now_epoch=1_780_000_100):
        return service.publish_observation(
            valid_item() if item is None else item,
            pipeline_table_name="campaign-pipeline",
            users_table_name="users",
            cluster_queue_url="https://sqs.example/cluster",
            hmac_key_id="alias/period-key",
            transient_retention_days=21,
            dynamodb_client=fake_dynamo,
            kms_client=fake_kms,
            sqs_client=fake_sqs,
            now_epoch=now_epoch,
        )

    def test_publishes_pseudonymous_app_feature_and_cluster_message(self):
        result = self.publish()

        self.assertEqual(result, "published")
        self.assertEqual(len(fake_kms.calls), 1)
        self.assertEqual(
            fake_kms.calls[0]["Message"],
            b"campaign-contributor:v1\0account-123",
        )
        self.assertEqual(fake_kms.calls[0]["MacAlgorithm"], "HMAC_SHA_256")
        condition = fake_dynamo.transactions[0][0]["ConditionCheck"]
        self.assertEqual(condition["TableName"], "users")
        self.assertEqual(condition["ExpressionAttributeValues"][":epoch"], {"S": CONSENT_EPOCH_ID})
        self.assertEqual(condition["ExpressionAttributeValues"][":environment"], {"S": "dev"})
        self.assertEqual(condition["ExpressionAttributeValues"][":notice"], {"S": "2026-09-07"})
        feature = fake_dynamo.transactions[0][1]["Put"]["Item"]
        self.assertEqual(feature["SK"], {"S": "FEATURE"})
        self.assertNotIn("accountId", feature)
        self.assertNotIn("sanitizedText", feature)
        self.assertEqual(
            feature["contributorToken"]["S"],
            base64.urlsafe_b64encode(b"x" * 32).decode("ascii").rstrip("="),
        )
        self.assertEqual(feature["extractorVersion"], {"S": "android-1.0.0"})
        self.assertEqual(feature["indicatorIds"], {"L": [{"S": "payment.crypto"}]})
        message = json.loads(fake_sqs.calls[0]["MessageBody"])
        self.assertEqual(set(message), {
            "schemaVersion", "eventType", "environment", "statisticsEventId", "recordVersion"
        })
        self.assertEqual(message["eventType"], "campaign.cluster.requested")
        self.assertEqual(fake_sqs.calls[0]["QueueUrl"], "https://sqs.example/cluster")

    def test_service_revalidates_app_features_before_any_side_effect(self):
        with self.assertRaises(ValueError):
            self.publish(valid_item(appFeatures=valid_features(vector=[float("inf")])))

        self.assertEqual(fake_kms.calls, [])
        self.assertEqual(fake_dynamo.transactions, [])
        self.assertEqual(fake_sqs.calls, [])

    def test_publisher_feature_record_satisfies_cluster_input_contract(self):
        self.publish()
        persisted = aggregator_service.deserialize(
            fake_dynamo.transactions[0][1]["Put"]["Item"]
        )

        validated = aggregator_service._validated_feature(
            persisted,
            EVENT_ID,
            "dev",
            1,
        )

        self.assertEqual(validated["extractorVersion"], "android-1.0.0")
        self.assertEqual(validated["SK"], "FEATURE")
        self.assertEqual(
            json.loads(fake_sqs.calls[0]["MessageBody"])["eventType"],
            "campaign.cluster.requested",
        )

    def test_declined_consent_is_successful_noop(self):
        result = self.publish(valid_item(campaignConsentGranted=False))

        self.assertEqual(result, "consent-suppressed")
        self.assertEqual(fake_kms.calls, [])
        self.assertEqual(fake_dynamo.transactions, [])
        self.assertEqual(fake_sqs.calls, [])

    def test_withdrawal_or_new_epoch_blocks_publication_before_side_effects(self):
        for participation in (
            {"state": {"S": "withdrawal_pending"}, "consentEpochId": {"S": CONSENT_EPOCH_ID},
             "environment": {"S": "dev"}, "noticeVersion": {"S": "2026-09-07"}},
            {"state": {"S": "enrolled"}, "consentEpochId": {"S": "new-epoch"},
             "environment": {"S": "dev"}, "noticeVersion": {"S": "2026-09-07"}},
            {"state": {"S": "enrolled"}, "consentEpochId": {"S": CONSENT_EPOCH_ID},
             "environment": {"S": "prod"}, "noticeVersion": {"S": "2026-09-07"}},
            None,
        ):
            with self.subTest(participation=participation):
                fake_dynamo.participation_item = participation
                self.assertEqual(self.publish(), "participation-suppressed")
                self.assertEqual(fake_kms.calls, [])
                self.assertEqual(fake_dynamo.transactions, [])
                self.assertEqual(fake_sqs.calls, [])
                fake_dynamo.participation_item = {
                    "state": {"S": "enrolled"},
                    "consentEpochId": {"S": CONSENT_EPOCH_ID},
                    "environment": {"S": "dev"},
                    "noticeVersion": {"S": "2026-09-07"},
                }

    def test_withdrawal_is_checked_before_period_key_resolution(self):
        fake_dynamo.participation_item = None
        resolver = mock.Mock(side_effect=AssertionError("period key must not be resolved"))

        result = service.publish_observation(
            valid_item(),
            pipeline_table_name="campaign-pipeline",
            users_table_name="users",
            cluster_queue_url="https://sqs.example/cluster",
            hmac_key_resolver=resolver,
            transient_retention_days=21,
            dynamodb_client=fake_dynamo,
            kms_client=fake_kms,
            sqs_client=fake_sqs,
            now_epoch=1_780_000_100,
        )

        self.assertEqual(result, "participation-suppressed")
        resolver.assert_not_called()

    def test_expired_observation_is_successful_noop(self):
        result = self.publish(valid_item(expiresAt=1_780_000_000))

        self.assertEqual(result, "expired")
        self.assertEqual(fake_kms.calls, [])
        self.assertEqual(fake_dynamo.transactions, [])

    def test_completed_duplicate_is_successful_noop(self):
        fake_dynamo.item = {"status": {"S": "PUBLISHED"}}

        result = self.publish()

        self.assertEqual(result, "duplicate")
        self.assertEqual(fake_dynamo.transactions, [])
        self.assertEqual(fake_sqs.calls, [])

    def test_pending_delivery_retries_without_rederiving_identity_token(self):
        fake_dynamo.item = {"status": {"S": "PENDING"}}

        result = self.publish()

        self.assertEqual(result, "published")
        self.assertEqual(fake_kms.calls, [])
        self.assertEqual(fake_dynamo.transactions, [])
        self.assertEqual(len(fake_sqs.calls), 1)
        self.assertEqual(len(fake_dynamo.updates), 1)

    def test_period_is_fixed_fourteen_day_utc_bucket(self):
        self.assertEqual(service.contributor_period_id(0), 0)
        self.assertEqual(service.contributor_period_id(1_209_599), 0)
        self.assertEqual(service.contributor_period_id(1_209_600), 1)


class HandlerTests(unittest.TestCase):
    def setUp(self):
        fake_cloudwatch.calls.clear()
        fake_dynamo.item = {"status": {"S": "ENABLED"}, "keyArn": {"S": "arn:period-key"}}
        fake_dynamo.participation_item = {
            "state": {"S": "enrolled"}, "consentEpochId": {"S": CONSENT_EPOCH_ID},
            "environment": {"S": "dev"}, "noticeVersion": {"S": "2026-09-07"},
        }

    def test_handler_uses_content_free_completion_log(self):
        with mock.patch.object(app, "publish_observation", return_value="published") as publish, \
             self.assertLogs(level="INFO") as captured:
            result = app.lambda_handler(stream_event(), None)

        self.assertEqual(result, {"processed": 1, "results": {"published": 1}})
        self.assertEqual(publish.call_count, 1)
        combined = " ".join(captured.output)
        self.assertNotIn(EVENT_ID, combined)
        self.assertNotIn("account-123", combined)
        self.assertNotIn("PAYMENT_HANDLE", combined)
        self.assertNotIn("android-1.0.0", combined)
        self.assertNotIn("0123456789abcdef", combined)
        self.assertEqual(len(fake_cloudwatch.calls), 1)

    def test_malformed_record_emits_only_content_free_metric(self):
        with self.assertRaises(contracts.ContractError):
            app.lambda_handler(stream_event(valid_item(environment="prod")), None)

        self.assertEqual(len(fake_cloudwatch.calls), 1)
        metric = fake_cloudwatch.calls[0]["MetricData"][0]
        self.assertEqual(metric["MetricName"], "Malformed")


if __name__ == "__main__":
    unittest.main()
