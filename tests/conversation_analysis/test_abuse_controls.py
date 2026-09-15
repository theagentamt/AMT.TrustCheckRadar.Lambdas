import sys
import os
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
MODULE_DIR = SRC_DIR / "conversation_analysis"
for path in (SRC_DIR, MODULE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
for module_name in ["config", "errors", "account_fence", "abuse_controls"]:
    sys.modules.pop(module_name, None)

os.environ.update({
    "COGNITO_ISSUER": "https://cognito-idp.us-east-1.amazonaws.com/test",
    "COGNITO_APP_CLIENT_ID": "test-client",
    "COGNITO_REQUIRED_SCOPE": "aws.cognito.signin.user.admin",
    "USERS_TABLE_NAME": "users",
    "DELETION_LEDGER_TABLE_NAME": "ledger",
})


class FakeClientError(Exception):
    def __init__(self, response=None):
        super().__init__("client error")
        self.response = response or {}


def conditional_failure():
    return FakeClientError({"Error": {"Code": "ConditionalCheckFailedException"}})


class FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key, **_kwargs):
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
            if (
                ":maximum" in values
                and int(item.get("requestCount", 0)) >= int(values[":maximum"])
            ):
                raise conditional_failure()
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
            if ":campaign_authorization" in values:
                item["campaignAuthorization"] = values[":campaign_authorization"]
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


def _deserialize_item(item):
    result = {}
    for key, value in item.items():
        kind, raw = next(iter(value.items()))
        if kind == "S":
            result[key] = raw
        elif kind == "N":
            result[key] = Decimal(raw)
            if result[key] == result[key].to_integral_value():
                result[key] = int(result[key])
        elif kind == "BOOL":
            result[key] = raw
        elif kind == "NULL":
            result[key] = None
        elif kind == "M":
            result[key] = _deserialize_item(raw)
        elif kind == "L":
            result[key] = [
                _deserialize_item({"value": nested})["value"] for nested in raw
            ]
    return result


class FakeDynamoClient:
    def __init__(self):
        self.transactions = []
        self.error = None

    def transact_write_items(self, TransactItems):
        if self.error:
            raise self.error
        self.transactions.append(TransactItems)
        write = TransactItems[-1]
        try:
            if "Put" in write:
                operation = write["Put"]
                fake_table.put_item(
                    _deserialize_item(operation["Item"]),
                    ConditionExpression=operation.get("ConditionExpression"),
                )
            elif "Update" in write:
                operation = write["Update"]
                fake_table.update_item(
                    _deserialize_item(operation["Key"]),
                    UpdateExpression=operation.get("UpdateExpression"),
                    ConditionExpression=operation.get("ConditionExpression"),
                    ExpressionAttributeNames=operation.get("ExpressionAttributeNames"),
                    ExpressionAttributeValues=_deserialize_item(
                        operation.get("ExpressionAttributeValues") or {}
                    ),
                )
        except FakeClientError as err:
            if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise FakeClientError({
                    "Error": {"Code": "TransactionCanceledException"},
                }) from err
            raise


fake_client = FakeDynamoClient()


class FakeResource:
    def Table(self, name):
        if name in {"users", "test-users"}:
            class Users:
                def get_item(self, Key, **_kwargs):
                    sub = Key["PK"].removeprefix("USER#")
                    return {"Item": {"sub": sub, "status": "ACTIVE", "ageVerified": True}}
            return Users()
        if name in {"ledger", "test-ledger"}:
            class Ledger:
                def get_item(self, **_kwargs):
                    return {}
            return Ledger()
        return fake_table


class ActiveUsersTable:
    def get_item(self, Key, **_kwargs):
        sub = Key["PK"].removeprefix("USER#")
        return {"Item": {"sub": sub, "status": "ACTIVE", "ageVerified": True}}


class EmptyLedgerTable:
    def get_item(self, **_kwargs):
        return {}


boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *args, **kwargs: FakeResource()
boto3_stub.client = lambda *args, **kwargs: fake_client
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
        fake_client.transactions.clear()
        fake_client.error = None
        abuse_controls.table = fake_table
        abuse_controls.dynamodb_client = fake_client

    def test_extract_identity_prefers_jwt_sub(self):
        event = {
            "requestContext": {
                "authorizer": {
                    "jwt": {
                        "claims": {
                            "sub": "user-123",
                            "iss": os.environ["COGNITO_ISSUER"],
                            "client_id": os.environ["COGNITO_APP_CLIENT_ID"],
                            "token_use": "access",
                            "exp": "4102444800",
                            "scope": os.environ["COGNITO_REQUIRED_SCOPE"],
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
        transaction = fake_client.transactions[0]
        self.assertEqual(transaction[0]["ConditionCheck"]["TableName"], "users")
        self.assertEqual(transaction[1]["ConditionCheck"]["TableName"], "ledger")
        self.assertEqual(
            transaction[2]["Put"]["TableName"],
            abuse_controls.ANALYSIS_ABUSE_TABLE_NAME,
        )

    def test_account_deletion_fence_blocks_late_request_creation(self):
        fake_client.error = FakeClientError({
            "Error": {"Code": "TransactionCanceledException"},
        })
        forbidden = AppError(
            "FORBIDDEN", "The account is not active.", retryable=False
        )

        with mock.patch.object(
            abuse_controls, "assert_account_active", side_effect=forbidden
        ), self.assertRaises(AppError) as context:
            abuse_controls.check_or_lock_request(
                "user-123", "req-1", payload(), now_epoch=100
            )

        self.assertEqual(context.exception.code, "FORBIDDEN")
        identity_hash = abuse_controls._hashed_identity("user-123")
        self.assertNotIn(
            (f"ANALYSIS#REQUEST#{identity_hash}", "req-1"), fake_table.items
        )

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
            campaign_authorization={
                "consentEpochId": "15c81ba4-2fa6-43c3-8895-889f08c931bf",
                "noticeVersion": "notice-2026-09",
                "stateVersion": 2,
            },
            now_epoch=101,
        )

        result = abuse_controls.check_or_lock_request("user-123", "req-1", payload(), now_epoch=102)

        self.assertEqual(result["state"], "result_ready")
        self.assertEqual(result["response"], response())
        self.assertEqual(
            result["statisticsEventId"],
            "7fbce2ac-bd2e-4d2e-9ec6-1f895a482abc",
        )
        self.assertEqual(
            result["campaignAuthorization"],
            {
                "consentEpochId": "15c81ba4-2fa6-43c3-8895-889f08c931bf",
                "noticeVersion": "notice-2026-09",
                "stateVersion": 2,
            },
        )
        result_store_transaction = fake_client.transactions[1]
        self.assertEqual(
            result_store_transaction[0]["ConditionCheck"]["TableName"], "users"
        )
        self.assertEqual(
            result_store_transaction[1]["ConditionCheck"]["TableName"], "ledger"
        )
        self.assertEqual(
            result_store_transaction[2]["Update"]["TableName"],
            abuse_controls.ANALYSIS_ABUSE_TABLE_NAME,
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

    def test_durable_replay_survives_short_ttl_without_second_processing_lock(self):
        request_payload = payload()
        payload_hash = abuse_controls._payload_hash(request_payload)
        identity_hash = abuse_controls._hashed_identity("user-123")
        short_key = (f"ANALYSIS#REQUEST#{identity_hash}", "req-1")
        fake_table.items[short_key] = {
            "PK": short_key[0], "SK": short_key[1], "status": "COMPLETED",
            "payloadHash": payload_hash, "response": response(), "expiresAt": 99,
        }
        content_key = ("USER#user-123#HISTORY#0", "COMPLETE#0000000100000#req-1")
        content = {
            "PK": content_key[0], "SK": content_key[1], "recordType": "HISTORY",
            "schemaVersion": 1, "recordVersion": 1, "requestId": "req-1",
            "historyGeneration": 0, "recognitionGeneration": 0, "acceptedSequence": 1,
            "acceptedAtEpochMs": 90_000, "completedAtEpochMs": 100_000,
            "sourceType": "pasted_text", "assessment": {
                "schemaVersion": "1.0", "scamScore": 72, "riskLevel": "high",
                "confidence": Decimal("0.84"), "summary": "Strong scam indicators detected.",
                "signals": ["payment_request"], "recommendedActions": ["Do not send money."],
            },
            "expiresAt": 1000, "expiryBucket": "HISTORY#1970010100#00",
        }

        class RoutedTable:
            def __init__(self, items):
                self.items = items

            def get_item(self, Key, **_kwargs):
                item = self.items.get((Key["PK"], Key["SK"]))
                return {"Item": item} if item else {}

        control = RoutedTable({
            ("USER#user-123", "STATE"): {"accountStatus": "ACTIVE", "historyGeneration": 0},
            ("USER#user-123", "REQUEST#req-1"): {
                "status": "ACTIVE", "payloadHash": payload_hash, "historyGeneration": 0,
                "contentSortKey": content_key[1], "contentExpiresAt": 1000,
            },
        })
        content_table = RoutedTable({content_key: content})

        class RoutedResource:
            def Table(self, name):
                return {
                    "control": control, "content": content_table,
                    "users": ActiveUsersTable(), "ledger": EmptyLedgerTable(),
                }[name]

        with mock.patch.dict(os.environ, _history_env(), clear=True), mock.patch.object(abuse_controls, "dynamodb", RoutedResource()):
            result = abuse_controls.check_or_lock_request("user-123", "req-1", request_payload, now_epoch=100)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["response"]["requestId"], "req-1")
        self.assertEqual(fake_table.items[short_key]["status"], "COMPLETED")

    def test_clear_generation_blocks_even_unexpired_short_replay(self):
        request_payload = payload()
        payload_hash = abuse_controls._payload_hash(request_payload)
        identity_hash = abuse_controls._hashed_identity("user-123")
        fake_table.items[(f"ANALYSIS#REQUEST#{identity_hash}", "req-1")] = {
            "status": "COMPLETED", "payloadHash": payload_hash,
            "response": response(), "expiresAt": 999,
        }

        class RoutedTable:
            def __init__(self, items):
                self.items = items

            def get_item(self, Key, **_kwargs):
                item = self.items.get((Key["PK"], Key["SK"]))
                return {"Item": item} if item else {}

        control = RoutedTable({
            ("USER#user-123", "STATE"): {"accountStatus": "ACTIVE", "historyGeneration": 2},
            ("USER#user-123", "REQUEST#req-1"): {
                "status": "ACTIVE", "payloadHash": payload_hash, "historyGeneration": 1,
                "contentSortKey": "COMPLETE#0000000100000#req-1", "contentExpiresAt": 1000,
            },
        })

        class RoutedResource:
            def Table(self, name):
                return {
                    "control": control, "users": ActiveUsersTable(),
                    "ledger": EmptyLedgerTable(),
                }.get(name, control)

        with mock.patch.dict(os.environ, _history_env(), clear=True), mock.patch.object(abuse_controls, "dynamodb", RoutedResource()):
            with self.assertRaises(AppError) as raised:
                abuse_controls.check_or_lock_request("user-123", "req-1", request_payload, now_epoch=100)
        self.assertEqual(raised.exception.code, "RESULT_UNAVAILABLE")


def _history_env():
    return {
        "APP_ENVIRONMENT": "dev", "HISTORY_DURABLE_REPLAY_ENABLED": "true",
        "HISTORY_CONTENT_TABLE_NAME": "content", "HISTORY_CONTROL_TABLE_NAME": "control",
        "DEVICE_BINDINGS_TABLE_NAME": "bindings", "ANALYSIS_ABUSE_TABLE_NAME": "abuse",
        "USERS_TABLE_NAME": "users", "DELETION_LEDGER_TABLE_NAME": "ledger",
        "HISTORY_PITR_POLICY_APPROVED": "true", "HISTORY_CONTROL_RETENTION_POLICY_APPROVED": "true",
        "HISTORY_DEDUP_RETENTION_DAYS": "400", "HISTORY_MAX_SUMMARY_BYTES": "4096",
        "HISTORY_MAX_LIST_ITEMS": "20", "HISTORY_MAX_TEXT_FIELD_BYTES": "1024",
    }


if __name__ == "__main__":
    unittest.main()
