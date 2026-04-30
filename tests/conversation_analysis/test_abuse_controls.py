import sys
import types
import unittest
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "conversation_analysis"
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))


class FakeClientError(Exception):
    def __init__(self, response=None):
        super().__init__("client error")
        self.response = response or {}


class FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["PK"], Item["SK"])
        if key in self.items:
            raise FakeClientError({"Error": {"Code": "ConditionalCheckFailedException"}})
        self.items[key] = Item

    def update_item(self, Key, UpdateExpression=None, ExpressionAttributeValues=None, ExpressionAttributeNames=None, ReturnValues=None):
        key = (Key["PK"], Key["SK"])
        item = self.items.get(key, {"PK": Key["PK"], "SK": Key["SK"], "requestCount": 0})
        if "ADD requestCount" in (UpdateExpression or ""):
            item["requestCount"] = int(item.get("requestCount", 0)) + int(ExpressionAttributeValues[":one"])
            item["expiresAt"] = ExpressionAttributeValues[":expires_at"]
            item["ttl"] = ExpressionAttributeValues[":expires_at"]
            item["updatedAt"] = ExpressionAttributeValues[":updated_at"]
            self.items[key] = item
            return {"Attributes": {"requestCount": item["requestCount"]}}

        item["status"] = ExpressionAttributeValues[":status"]
        if ":response" in ExpressionAttributeValues:
            item["response"] = ExpressionAttributeValues[":response"]
        if ":completed_at" in ExpressionAttributeValues:
            item["completedAt"] = ExpressionAttributeValues[":completed_at"]
        item["updatedAt"] = ExpressionAttributeValues[":updated_at"]
        item["expiresAt"] = ExpressionAttributeValues[":expires_at"]
        item["ttl"] = ExpressionAttributeValues[":expires_at"]
        self.items[key] = item
        return {"Attributes": item}

    def delete_item(self, Key):
        self.items.pop((Key["PK"], Key["SK"]), None)


fake_table = FakeTable()


class FakeResource:
    def Table(self, name):
        return fake_table


boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *args, **kwargs: FakeResource()
boto3_stub.client = lambda *args, **kwargs: object()
sys.modules.setdefault("boto3", boto3_stub)
botocore_ex = types.ModuleType("botocore.exceptions")
botocore_ex.ClientError = FakeClientError
sys.modules.setdefault("botocore.exceptions", botocore_ex)

import abuse_controls  # noqa: E402
from errors import AppError  # noqa: E402


class AbuseControlsTests(unittest.TestCase):
    def setUp(self):
        fake_table.items.clear()
        abuse_controls.table = fake_table

    def test_extract_identity_prefers_jwt_sub(self):
        event = {
            "requestContext": {
                "authorizer": {
                    "jwt": {
                        "claims": {
                            "sub": "user-123",
                        }
                    }
                }
            }
        }

        identity = abuse_controls.extract_identity(event)

        self.assertEqual(identity, "user-123")

    def test_extract_identity_rejects_missing_authorizer_identity(self):
        with self.assertRaises(AppError) as context:
            abuse_controls.extract_identity({})

        self.assertEqual(context.exception.code, "UNAUTHORIZED")

    def test_rejects_recently_completed_duplicate_request(self):
        identity_hash = abuse_controls._hashed_identity("user-123")
        fake_table.items[(f"ANALYSIS#REQUEST#{identity_hash}", "req-1")] = {
            "PK": f"ANALYSIS#REQUEST#{identity_hash}",
            "SK": "req-1",
            "status": "COMPLETED",
            "expiresAt": 9999999999,
        }

        with self.assertRaises(AppError) as context:
            abuse_controls.check_or_lock_request("user-123", "req-1", now_epoch=100)

        self.assertEqual(context.exception.code, "RATE_LIMITED")

    def test_rejects_duplicate_in_progress_request(self):
        identity_hash = abuse_controls._hashed_identity("user-123")
        fake_table.items[(f"ANALYSIS#REQUEST#{identity_hash}", "req-1")] = {
            "PK": f"ANALYSIS#REQUEST#{identity_hash}",
            "SK": "req-1",
            "status": "IN_PROGRESS",
            "expiresAt": 9999999999,
        }

        with self.assertRaises(AppError) as context:
            abuse_controls.check_or_lock_request("user-123", "req-1", now_epoch=100)

        self.assertEqual(context.exception.code, "RATE_LIMITED")

    def test_rate_limit_triggers_after_limit(self):
        for _ in range(10):
            abuse_controls.enforce_rate_limit("user-123", now_epoch=300)

        with self.assertRaises(AppError) as context:
            abuse_controls.enforce_rate_limit("user-123", now_epoch=300)

        self.assertEqual(context.exception.code, "RATE_LIMITED")

    def test_complete_request_stores_minimal_metadata_only(self):
        abuse_controls.check_or_lock_request("user-123", "req-1", now_epoch=100)
        abuse_controls.complete_request("user-123", "req-1", now_epoch=100)

        identity_hash = abuse_controls._hashed_identity("user-123")
        item = fake_table.items[(f"ANALYSIS#REQUEST#{identity_hash}", "req-1")]
        self.assertEqual(item["status"], "COMPLETED")
        self.assertIn("completedAt", item)
        self.assertNotIn("response", item)


if __name__ == "__main__":
    unittest.main()
