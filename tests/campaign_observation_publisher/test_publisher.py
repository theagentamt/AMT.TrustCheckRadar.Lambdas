import base64
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
        self.transactions = []
        self.updates = []

    def get_item(self, **_kwargs):
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
        "FEATURE_QUEUE_URL": "https://sqs.example/feature",
    }
)

import app  # noqa: E402
import contracts  # noqa: E402
import service  # noqa: E402


EVENT_ID = "7fbce2ac-bd2e-4d2e-9ec6-1f895a482abc"


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
        "observedAtEpoch": 1_780_000_000,
        "sourceType": "pasted_text",
        "sanitizedText": "A caller requested payment using [PAYMENT_HANDLE_1].",
        "riskLevel": "high",
        "signalIds": ["payment_request"],
        "expiresAt": 1_780_259_200,
    }
    item.update(overrides)
    return item


def dynamodb_value(value):
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, int):
        return {"N": str(value)}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, list):
        return {"L": [dynamodb_value(item) for item in value]}
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

    def test_feature_envelope_contains_only_approved_fields(self):
        envelope = contracts.build_feature_envelope(valid_item())

        self.assertEqual(
            set(envelope),
            {"schemaVersion", "eventType", "environment", "statisticsEventId", "recordVersion"},
        )
        self.assertNotIn("accountId", json.dumps(envelope))
        self.assertNotIn("sanitizedText", json.dumps(envelope))

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
        fake_dynamo.transactions.clear()
        fake_dynamo.updates.clear()
        fake_kms.calls.clear()
        fake_sqs.calls.clear()

    def publish(self, item=None, now_epoch=1_780_000_100):
        return service.publish_observation(
            valid_item() if item is None else item,
            pipeline_table_name="campaign-pipeline",
            feature_queue_url="https://sqs.example/feature",
            hmac_key_id="alias/period-key",
            observation_retention_hours=72,
            transient_retention_days=21,
            dynamodb_client=fake_dynamo,
            kms_client=fake_kms,
            sqs_client=fake_sqs,
            now_epoch=now_epoch,
        )

    def test_publishes_pseudonymous_observation_and_opaque_message(self):
        result = self.publish()

        self.assertEqual(result, "published")
        self.assertEqual(len(fake_kms.calls), 1)
        self.assertEqual(
            fake_kms.calls[0]["Message"],
            b"campaign-contributor:v1\0account-123",
        )
        self.assertEqual(fake_kms.calls[0]["MacAlgorithm"], "HMAC_SHA_256")
        observation = fake_dynamo.transactions[0][0]["Put"]["Item"]
        self.assertNotIn("accountId", observation)
        self.assertEqual(
            observation["contributorToken"]["S"],
            base64.urlsafe_b64encode(b"x" * 32).decode("ascii").rstrip("="),
        )
        message = json.loads(fake_sqs.calls[0]["MessageBody"])
        self.assertEqual(set(message), {
            "schemaVersion", "eventType", "environment", "statisticsEventId", "recordVersion"
        })
        self.assertEqual(message["eventType"], "campaign.feature.requested")

    def test_declined_consent_is_successful_noop(self):
        result = self.publish(valid_item(campaignConsentGranted=False))

        self.assertEqual(result, "consent-suppressed")
        self.assertEqual(fake_kms.calls, [])
        self.assertEqual(fake_dynamo.transactions, [])
        self.assertEqual(fake_sqs.calls, [])

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
        self.assertEqual(len(fake_cloudwatch.calls), 1)

    def test_malformed_record_emits_only_content_free_metric(self):
        with self.assertRaises(contracts.ContractError):
            app.lambda_handler(stream_event(valid_item(environment="prod")), None)

        self.assertEqual(len(fake_cloudwatch.calls), 1)
        metric = fake_cloudwatch.calls[0]["MetricData"][0]
        self.assertEqual(metric["MetricName"], "Malformed")


if __name__ == "__main__":
    unittest.main()
