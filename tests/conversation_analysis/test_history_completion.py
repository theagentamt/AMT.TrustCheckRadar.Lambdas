import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[2] / "src"
MODULE = SRC / "conversation_analysis"
for path in (SRC, MODULE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


class ClientError(Exception):
    def __init__(self, response=None):
        self.response = response or {}


class Table:
    def __init__(self, state, progress=None):
        self.state = state
        self.progress = progress

    def get_item(self, Key, **_kwargs):
        if Key["SK"] == "STATE":
            return {"Item": self.state}
        if Key["SK"].startswith("PROGRESS#") and self.progress:
            return {"Item": self.progress}
        return {}


class Resource:
    def __init__(self, table):
        self.table = table

    def Table(self, _name):
        return self.table


class Client:
    def __init__(self):
        self.calls = []

    def transact_write_items(self, **kwargs):
        self.calls.append(kwargs["TransactItems"])


boto3 = types.ModuleType("boto3")
boto3.resource = lambda *_args, **_kwargs: Resource(Table({}))
boto3.client = lambda *_args, **_kwargs: Client()
sys.modules["boto3"] = boto3
botocore = types.ModuleType("botocore.exceptions")
botocore.ClientError = ClientError
sys.modules["botocore.exceptions"] = botocore

for name in ("history_completion", "errors"):
    sys.modules.pop(name, None)
import history_completion


ENV = {
    "APP_ENVIRONMENT": "dev",
    "HISTORY_CONTENT_TABLE_NAME": "content",
    "HISTORY_CONTROL_TABLE_NAME": "control",
    "ANALYSIS_ABUSE_TABLE_NAME": "abuse",
    "DEVICE_BINDINGS_TABLE_NAME": "bindings",
    "USERS_TABLE_NAME": "users",
    "DELETION_LEDGER_TABLE_NAME": "ledger",
    "HISTORY_WRITES_ENABLED": "true",
    "HISTORY_DURABLE_REPLAY_ENABLED": "true",
    "HISTORY_PITR_POLICY_APPROVED": "true",
    "HISTORY_CONTROL_RETENTION_POLICY_APPROVED": "true",
    "HISTORY_DEDUP_RETENTION_DAYS": "400",
    "HISTORY_MAX_SUMMARY_BYTES": "4096",
    "HISTORY_MAX_LIST_ITEMS": "20",
    "HISTORY_MAX_TEXT_FIELD_BYTES": "1024",
}
STATE = {"accountStatus": "ACTIVE", "historyGeneration": 2, "recognitionGeneration": 3, "acceptedSequence": 8}
AUTH = {"historyGeneration": 2, "recognitionGeneration": 3, "acceptedSequence": 8, "acceptedAtEpochMs": 99_000}
PAYLOAD = {"requestId": "request-1", "sourceType": "ocr", "sanitizedText": "private input", "entities": [{"value": "private"}]}
RESPONSE = {
    "schemaVersion": "1.0", "requestId": "request-1", "scamScore": 80,
    "riskLevel": "high", "confidence": 0.9, "summary": "Likely fraud.",
    "signals": ["payment_request"], "recommendedActions": ["Do not pay."],
}


class CompletionTests(unittest.TestCase):
    def test_acceptance_reservation_is_atomic_with_pending_completion_heartbeat(self):
        client = Client()
        history_completion.dynamodb = Resource(Table(STATE))
        history_completion.dynamodb_client = client
        with mock.patch.dict(os.environ, ENV, clear=True):
            authorization = history_completion.reserve_history_acceptance(
                "account-1", "request-1", "a" * 64, "lease-1", now_epoch=100
            )
        self.assertEqual(authorization["acceptedSequence"], 9)
        self.assertEqual(len(client.calls[0]), 5)
        self.assertEqual(client.calls[0][0]["ConditionCheck"]["TableName"], "users")
        self.assertEqual(client.calls[0][1]["ConditionCheck"]["TableName"], "ledger")
        encoded = repr(client.calls[0])
        self.assertIn("COMPLETION#request-1", encoded)
        self.assertIn("PENDING#", encoded)
        self.assertNotIn("assessment", encoded)

    def test_completion_appends_content_locator_and_state_fence_without_input(self):
        history_completion.dynamodb = Resource(Table(STATE))
        transaction = []
        with mock.patch.dict(os.environ, ENV, clear=True):
            redacted = history_completion.append_history_completion(
                transaction, account_id="account-1", payload_hash="a" * 64,
                payload=PAYLOAD, response=RESPONSE, authorization=AUTH, now_epoch=100,
            )
        self.assertFalse(redacted)
        self.assertEqual(len(transaction), 6)
        encoded = repr(transaction)
        self.assertIn("HISTORY#2", encoded)
        self.assertIn("REQUEST#request-1", encoded)
        self.assertNotIn("private input", encoded)
        self.assertNotIn("entities", encoded)

    def test_clear_generation_wins_and_only_content_free_tombstone_is_written(self):
        changed = STATE | {"historyGeneration": 3}
        history_completion.dynamodb = Resource(Table(changed))
        transaction = []
        with mock.patch.dict(os.environ, ENV, clear=True):
            redacted = history_completion.append_history_completion(
                transaction, account_id="account-1", payload_hash="a" * 64,
                payload=PAYLOAD, response=RESPONSE, authorization=AUTH, now_epoch=100,
            )
        self.assertTrue(redacted)
        encoded = repr(transaction)
        self.assertIn("CLEARED", encoded)
        self.assertNotIn("assessment", encoded)
        self.assertNotIn("Likely fraud", encoded)

    def test_recognition_is_not_updated_after_reset_generation_changes(self):
        env = ENV | {
            "RECOGNITION_ENABLED": "true",
            "HISTORY_RECOGNITION_CONTRACT_STATUS": "approved",
            "HISTORY_NEW_ID_RECOGNITION_POLICY": "count",
            "HISTORY_BADGE_QUALIFICATION_POLICY": "all_server_accepted_completed_assessments",
            "HISTORY_BADGE_CATALOG_JSON": '[{"id":"checks_1","threshold":1,"titleKey":"badges.checks_1.title","descriptionKey":"badges.checks_1.description"},{"id":"checks_5","threshold":5,"titleKey":"badges.checks_5.title","descriptionKey":"badges.checks_5.description"},{"id":"checks_20","threshold":20,"titleKey":"badges.checks_20.title","descriptionKey":"badges.checks_20.description"}]',
        }
        changed = STATE | {"recognitionGeneration": 4}
        history_completion.dynamodb = Resource(Table(changed))
        transaction = []
        with mock.patch.dict(os.environ, env, clear=True):
            history_completion.append_history_completion(
                transaction, account_id="account-1", payload_hash="a" * 64,
                payload=PAYLOAD, response=RESPONSE, authorization=AUTH, now_epoch=100,
            )
        self.assertNotIn("PROGRESS#", repr(transaction))


if __name__ == "__main__":
    unittest.main()
