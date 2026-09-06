import json
import sys
import unittest
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "campaign_feature_extractor"
sys.path.insert(0, str(MODULE_DIR))
sys.modules.pop("service", None)
import service  # noqa: E402


EVENT_ID = "7fbce2ac-bd2e-4d2e-9ec6-1f895a482abc"


def av(value):
    if isinstance(value, str): return {"S": value}
    if isinstance(value, bool): return {"BOOL": value}
    if isinstance(value, (int, float)): return {"N": str(value)}
    if isinstance(value, list): return {"L": [av(child) for child in value]}
    raise TypeError(type(value).__name__)


class Dynamo:
    def __init__(self, observation=None, feature=None):
        self.observation = observation
        self.feature = feature
        self.puts = []
        self.updates = []

    def get_item(self, Key, **_kwargs):
        item = self.observation if Key["SK"]["S"] == "OBSERVATION" else self.feature
        return {"Item": item} if item else {}

    def put_item(self, **kwargs):
        self.puts.append(kwargs)
        self.feature = kwargs["Item"]

    def update_item(self, **kwargs):
        self.updates.append(kwargs)


class Sqs:
    def __init__(self): self.calls = []
    def send_message(self, **kwargs): self.calls.append(kwargs)


class Encoder:
    def __init__(self): self.inputs = []
    def encode(self, text):
        self.inputs.append(text)
        return [0.1, 0.2, 0.3]


def envelope(**updates):
    value = {"schemaVersion": 1, "eventType": "campaign.feature.requested",
             "environment": "dev", "statisticsEventId": EVENT_ID, "recordVersion": 1}
    value.update(updates)
    return json.dumps(value)


def observation(**updates):
    value = {"PK": f"EVENT#{EVENT_ID}", "SK": "OBSERVATION", "schemaVersion": 1,
             "recordVersion": 1, "environment": "dev", "statisticsEventId": EVENT_ID,
             "periodId": 1471, "contributorToken": "opaque-token", "sourceType": "pasted_text",
             "sanitizedText": "Send money to [PAYMENT_HANDLE_1] today", "riskLevel": "high",
             "signalIds": ["payment_request"], "expiresAt": 2_000_000_000}
    value.update(updates)
    return {key: av(item) for key, item in value.items()}


class FeatureServiceTests(unittest.TestCase):
    def process(self, dynamo, sqs=None, encoder=None, body=None):
        return service.process_message(
            envelope() if body is None else body, environment="dev", schema_version=1,
            table_name="pipeline", cluster_queue_url="cluster", model_version="model@revision",
            retention_days=21, dynamodb=dynamo, sqs=sqs or Sqs(), encoder=encoder or Encoder(),
            now_epoch=1_780_000_100,
        )

    def test_writes_bounded_feature_without_content_and_sends_opaque_work(self):
        dynamo, sqs, encoder = Dynamo(observation()), Sqs(), Encoder()

        self.assertEqual(self.process(dynamo, sqs, encoder), "published")

        feature = dynamo.puts[0]["Item"]
        self.assertNotIn("sanitizedText", feature)
        self.assertNotIn("accountId", feature)
        self.assertEqual(feature["taxonomyBucket"], {"S": "advance_fee"})
        self.assertEqual(feature["vector"]["L"], [{"N": "0.1"}, {"N": "0.2"}, {"N": "0.3"}])
        message = json.loads(sqs.calls[0]["MessageBody"])
        self.assertEqual(set(message), service.ENVELOPE_FIELDS)
        self.assertEqual(message["eventType"], "campaign.cluster.requested")

    def test_missing_deleted_or_suppressed_observation_is_noop(self):
        self.assertEqual(self.process(Dynamo()), "missing")
        self.assertEqual(self.process(Dynamo(observation(suppressed=True))), "suppressed")
        self.assertEqual(self.process(Dynamo(observation(expiresAt=1))), "suppressed")

    def test_completed_duplicate_is_noop(self):
        dynamo = Dynamo(observation(), {"deliveryStatus": {"S": "PUBLISHED"}})
        sqs = Sqs()

        self.assertEqual(self.process(dynamo, sqs), "duplicate")
        self.assertEqual(sqs.calls, [])

    def test_rejects_extra_fields_and_cross_environment(self):
        for body in (envelope(extra="bad"), envelope(environment="prod")):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.process(Dynamo(observation()), body=body)

    def test_language_and_fingerprint_are_bounded(self):
        self.assertEqual(service._language_id("Envía el dinero para la cuenta"), "es")
        self.assertEqual(service._language_id("Send the money now"), "en")
        self.assertLessEqual(len(service._fingerprint("word " * 100 + "other")), 32)


if __name__ == "__main__":
    unittest.main()
