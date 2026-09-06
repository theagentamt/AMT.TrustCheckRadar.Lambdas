import sys
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "conversation_analysis"
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))
for module_name in ["config", "errors", "abuse_controls"]:
    sys.modules.pop(module_name, None)


class FakeClientError(Exception):
    def __init__(self, response=None):
        super().__init__("client error")
        self.response = response or {}


def conditional_failure():
    return FakeClientError({"Error": {"Code": "ConditionalCheckFailedException"}})


class FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["PK"], Item["SK"])
        if key in self.items:
            raise conditional_failure()
        self.items[key] = dict(Item)

    def update_item(
        self,
        Key,
        UpdateExpression=None,
        ConditionExpression=None,
        ExpressionAttributeValues=None,
        ExpressionAttributeNames=None,
        ReturnValues=None,
    ):
        key = (Key["PK"], Key["SK"])
        item = self.items.get(key, {"PK": Key["PK"], "SK": Key["SK"], "requestCount": 0})
        values = ExpressionAttributeValues or {}
        if "ADD requestCount" in (UpdateExpression or ""):
            self._assert_ttl_alias(ExpressionAttributeNames)
            item["requestCount"] = int(item.get("requestCount", 0)) + int(values[":one"])
            item["expiresAt"] = values[":expires_at"]
            item["ttl"] = values[":expires_at"]
            item["updatedAt"] = values[":updated_at"]
            self.items[key] = item
            return {"Attributes": {"requestCount": item["requestCount"]}}

        if ":result_ready" in values:
            if (
                item.get("status") != values[":processing"]
                or item.get("payloadHash") != values[":payload_hash"]
                or item.get("leaseToken") != values[":lease_token"]
            ):
                raise conditional_failure()
            item["status"] = values[":result_ready"]
            item["response"] = values[":response"]
            item["resultReadyAt"] = values[":result_ready_at"]
            if ":statistics_event_id" in values:
                item["statisticsEventId"] = values[":statistics_event_id"]
            item.pop("leaseToken", None)
            item.pop("leaseExpiresAt", None)
        elif ":previous_lease_expires_at" in values:
            if (
                item.get("status") != values[":processing"]
                or item.get("payloadHash") != values[":payload_hash"]
                or item.get("leaseExpiresAt") != values[":previous_lease_expires_at"]
            ):
                raise conditional_failure()
            item["leaseToken"] = values[":lease_token"]
            item["leaseExpiresAt"] = values[":lease_expires_at"]
        elif ":previous_expires_at" in values:
            if item.get("expiresAt") != values[":previous_expires_at"]:
                raise conditional_failure()
            item["status"] = values[":processing"]
            item["payloadHash"] = values[":payload_hash"]
            item["leaseToken"] = values[":lease_token"]
            item["leaseExpiresAt"] = values[":lease_expires_at"]
            item["createdAt"] = values[":created_at"]
            item.pop("response", None)
            item.pop("resultReadyAt", None)
            item.pop("completedAt", None)

        self._assert_ttl_alias(ExpressionAttributeNames)
        item["updatedAt"] = values[":updated_at"]
        item["expiresAt"] = values[":expires_at"]
        item["ttl"] = values[":expires_at"]
        self.items[key] = item
        return {"Attributes": item}

    def delete_item(
        self,
        Key,
        ConditionExpression=None,
        ExpressionAttributeNames=None,
        ExpressionAttributeValues=None,
    ):
        key = (Key["PK"], Key["SK"])
        item = self.items.get(key)
        if ConditionExpression and (
            not item
            or item.get("status") != ExpressionAttributeValues[":processing"]
            or item.get("leaseToken") != ExpressionAttributeValues[":lease_token"]
        ):
            raise conditional_failure()
        self.items.pop(key, None)

    @staticmethod
    def _assert_ttl_alias(expression_attribute_names):
        if not expression_attribute_names or expression_attribute_names.get("#ttl") != "ttl":
            raise AssertionError("Expected DynamoDB reserved attribute alias for ttl")


fake_table = FakeTable()


class FakeResource:
    def Table(self, name):
        return fake_table


boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *args, **kwargs: FakeResource()
boto3_stub.client = lambda *args, **kwargs: object()
sys.modules["boto3"] = boto3_stub
botocore_ex = types.ModuleType("botocore.exceptions")
botocore_ex.ClientError = FakeClientError
sys.modules["botocore.exceptions"] = botocore_ex

import abuse_controls  # noqa: E402
from errors import AppError  # noqa: E402


def payload(text="Sanitized content"):
    return {
        "schemaVersion": "1.0",
        "requestId": "req-1",
        "sourceType": "pasted_text",
        "localSanitizationApplied": True,
        "sanitizedText": text,
        "entities": [],
    }


def response():
    return {
        "schemaVersion": "1.0",
        "requestId": "req-1",
        "scamScore": 12,
        "riskLevel": "low",
        "confidence": 0.6,
        "summary": "No strong scam indicators detected.",
        "signals": [],
        "recommendedActions": ["Proceed carefully."],
    }


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

        self.assertEqual(abuse_controls.extract_identity(event), "user-123")

    def test_extract_identity_rejects_missing_authorizer_identity(self):
        with self.assertRaises(AppError) as context:
            abuse_controls.extract_identity({})

        self.assertEqual(context.exception.code, "UNAUTHORIZED")

    @mock.patch.object(abuse_controls.uuid, "uuid4", return_value="lease-1")
    def test_new_request_binds_payload_and_processing_lease(self, _uuid):
        result = abuse_controls.check_or_lock_request("user-123", "req-1", payload(), now_epoch=100)

        self.assertEqual(result["state"], "processing")
        self.assertEqual(result["leaseToken"], "lease-1")
        identity_hash = abuse_controls._hashed_identity("user-123")
        item = fake_table.items[(f"ANALYSIS#REQUEST#{identity_hash}", "req-1")]
        self.assertEqual(item["status"], "PROCESSING")
        self.assertEqual(item["payloadHash"], abuse_controls._payload_hash(payload()))
        self.assertEqual(item["leaseExpiresAt"], 160)

    def test_same_request_id_with_different_payload_is_permanent_conflict(self):
        lock = abuse_controls.check_or_lock_request("user-123", "req-1", payload(), now_epoch=100)

        with self.assertRaises(AppError) as context:
            abuse_controls.check_or_lock_request("user-123", "req-1", payload("Different"), now_epoch=101)

        self.assertEqual(context.exception.code, "IDEMPOTENCY_CONFLICT")
        self.assertFalse(context.exception.retryable)
        self.assertIsNotNone(lock["leaseToken"])

    def test_active_processing_lease_returns_retry_guidance(self):
        abuse_controls.check_or_lock_request("user-123", "req-1", payload(), now_epoch=100)

        with self.assertRaises(AppError) as context:
            abuse_controls.check_or_lock_request("user-123", "req-1", payload(), now_epoch=120)

        self.assertEqual(context.exception.code, "REQUEST_IN_PROGRESS")
        self.assertEqual(context.exception.details, [{"retryAfterSeconds": 40}])

    @mock.patch.object(abuse_controls.uuid, "uuid4", side_effect=["lease-1", "lease-2"])
    def test_expired_processing_lease_is_taken_over_once(self, _uuid):
        abuse_controls.check_or_lock_request("user-123", "req-1", payload(), now_epoch=100)

        result = abuse_controls.check_or_lock_request("user-123", "req-1", payload(), now_epoch=161)

        self.assertTrue(result["takeover"])
        self.assertEqual(result["leaseToken"], "lease-2")
        identity_hash = abuse_controls._hashed_identity("user-123")
        item = fake_table.items[(f"ANALYSIS#REQUEST#{identity_hash}", "req-1")]
        self.assertEqual(item["leaseExpiresAt"], 221)

    def test_expired_ttl_record_is_conditionally_replaced(self):
        identity_hash = abuse_controls._hashed_identity("user-123")
        key = (f"ANALYSIS#REQUEST#{identity_hash}", "req-1")
        fake_table.items[key] = {
            "PK": key[0],
            "SK": key[1],
            "status": "COMPLETED",
            "payloadHash": abuse_controls._payload_hash(payload("Old content")),
            "response": response(),
            "expiresAt": 99,
        }

        result = abuse_controls.check_or_lock_request("user-123", "req-1", payload(), now_epoch=100)

        self.assertTrue(result["replacedExpired"])
        self.assertEqual(fake_table.items[key]["status"], "PROCESSING")
        self.assertEqual(fake_table.items[key]["payloadHash"], abuse_controls._payload_hash(payload()))
        self.assertNotIn("response", fake_table.items[key])

    def test_result_ready_is_replayed_for_atomic_commit_recovery(self):
        lock = abuse_controls.check_or_lock_request("user-123", "req-1", payload(), now_epoch=100)
        abuse_controls.store_result(
            "user-123",
            "req-1",
            lock["payloadHash"],
            lock["leaseToken"],
            response(),
            statistics_event_id="7fbce2ac-bd2e-4d2e-9ec6-1f895a482abc",
            now_epoch=101,
        )

        result = abuse_controls.check_or_lock_request("user-123", "req-1", payload(), now_epoch=102)

        self.assertEqual(result["state"], "result_ready")
        self.assertEqual(result["response"], response())
        self.assertEqual(
            result["statisticsEventId"],
            "7fbce2ac-bd2e-4d2e-9ec6-1f895a482abc",
        )

    def test_completed_response_replays_without_reprocessing(self):
        identity_hash = abuse_controls._hashed_identity("user-123")
        stored_response = response() | {"confidence": Decimal("0.6")}
        fake_table.items[(f"ANALYSIS#REQUEST#{identity_hash}", "req-1")] = {
            "PK": f"ANALYSIS#REQUEST#{identity_hash}",
            "SK": "req-1",
            "status": "COMPLETED",
            "payloadHash": abuse_controls._payload_hash(payload()),
            "response": stored_response,
            "expiresAt": 9999999999,
        }

        result = abuse_controls.check_or_lock_request("user-123", "req-1", payload(), now_epoch=100)

        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["response"], response())

    def test_release_requires_the_current_processing_lease(self):
        lock = abuse_controls.check_or_lock_request("user-123", "req-1", payload(), now_epoch=100)

        abuse_controls.release_request("user-123", "req-1", "another-lease")
        identity_hash = abuse_controls._hashed_identity("user-123")
        key = (f"ANALYSIS#REQUEST#{identity_hash}", "req-1")
        self.assertIn(key, fake_table.items)

        abuse_controls.release_request("user-123", "req-1", lock["leaseToken"])
        self.assertNotIn(key, fake_table.items)

    def test_rate_limit_triggers_after_limit(self):
        for _ in range(10):
            abuse_controls.enforce_rate_limit("user-123", now_epoch=300)

        with self.assertRaises(AppError) as context:
            abuse_controls.enforce_rate_limit("user-123", now_epoch=300)

        self.assertEqual(context.exception.code, "RATE_LIMITED")


if __name__ == "__main__":
    unittest.main()
