import sys
import types
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
MODULE_DIR = SRC_DIR / "conversation_analysis"
for path in (SRC_DIR, MODULE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

for module_name in ["config", "errors", "scan_access", "shared_entitlements", "shared_entitlements.service", "shared_entitlements.config"]:
    sys.modules.pop(module_name, None)


class FakeClientError(Exception):
    def __init__(self, response=None, operation_name=None):
        super().__init__(operation_name or "client error")
        self.response = response or {}


botocore_ex = types.ModuleType("botocore.exceptions")
botocore_ex.ClientError = FakeClientError
sys.modules["botocore.exceptions"] = botocore_ex


class FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}

    def put_item(self, Item):
        self.items[(Item["PK"], Item["SK"])] = dict(Item)
        return {}

    def update_item(
        self,
        Key,
        UpdateExpression=None,
        ExpressionAttributeNames=None,
        ExpressionAttributeValues=None,
        ReturnValues=None,
    ):
        key = (Key["PK"], Key["SK"])
        item = self.items.get(key, {"PK": Key["PK"], "SK": Key["SK"], "requestCount": 0})
        item["requestCount"] = int(item.get("requestCount", 0)) + int(ExpressionAttributeValues[":one"])
        item["expiresAt"] = ExpressionAttributeValues[":expires_at"]
        item["ttl"] = ExpressionAttributeValues[":expires_at"]
        item["updatedAt"] = ExpressionAttributeValues[":updated_at"]
        self.items[key] = item
        return {"Attributes": {"requestCount": item["requestCount"]}}


class FakeDynamoClient:
    def __init__(self):
        self.transactions = []
        self.error = None

    def transact_write_items(self, TransactItems):
        if self.error:
            raise self.error
        self.transactions.append(TransactItems)
        return {}


entitlements_fake = FakeTable()
abuse_fake = FakeTable()
transaction_fake = FakeDynamoClient()


class FakeResource:
    def Table(self, name):
        if "analysis-abuse" in name:
            return abuse_fake
        return entitlements_fake


boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *args, **kwargs: FakeResource()
boto3_stub.client = lambda *args, **kwargs: transaction_fake
sys.modules["boto3"] = boto3_stub

import shared_entitlements.service as shared_service  # noqa: E402
import scan_access  # noqa: E402
from errors import AppError  # noqa: E402


class ScanAccessTests(unittest.TestCase):
    def setUp(self):
        entitlements_fake.items.clear()
        abuse_fake.items.clear()
        shared_service.table = entitlements_fake
        scan_access.abuse_table = abuse_fake
        scan_access.dynamodb_client = transaction_fake
        scan_access.ANALYSIS_ABUSE_TABLE_NAME = "test-analysis-abuse"
        scan_access.ENTITLEMENTS_TABLE_NAME = "test-entitlements"
        scan_access.APP_ENVIRONMENT = "dev"
        scan_access.CAMPAIGN_OUTBOX_TABLE_NAME = "test-campaign-outbox"
        scan_access.CAMPAIGN_SCHEMA_VERSION = 1
        scan_access.CAMPAIGN_OBSERVATION_RETENTION_HOURS = 72
        transaction_fake.transactions.clear()
        transaction_fake.error = None

    def test_default_free_entitlement_consumes_monthly_scan(self):
        grant = scan_access.prepare_scan_access("user-123", now_epoch=60)

        updated = scan_access.consume_scan_access(grant, "request-123", now_iso="2026-05-11T00:00:00+00:00")

        self.assertEqual(updated["entitlementTier"], "FREE")
        self.assertEqual(updated["remainingMonthlyScans"], 4)
        self.assertEqual(updated["remainingCredits"], 0)
        self.assertEqual(updated["lastScanRequestId"], "request-123")

    def test_consumes_credit_when_monthly_scans_are_exhausted(self):
        entitlements_fake.put_item(
            {
                "PK": "USER#user-123",
                "SK": "ENTITLEMENT",
                "accountId": "user-123",
                "entitlementTier": "FREE",
                "subscriptionStatus": "expired",
                "monthlyScanLimit": 5,
                "remainingMonthlyScans": 0,
                "remainingCredits": 3,
                "createdAt": "2026-05-01T00:00:00+00:00",
                "updatedAt": "2026-05-01T00:00:00+00:00",
            }
        )

        grant = scan_access.prepare_scan_access("user-123", now_epoch=60)
        updated = scan_access.consume_scan_access(grant, "request-123", now_iso="2026-05-11T00:00:00+00:00")

        self.assertEqual(updated["remainingMonthlyScans"], 0)
        self.assertEqual(updated["remainingCredits"], 2)
        self.assertEqual(updated["lastScanConsumptionType"], "credit")

    def test_same_request_id_does_not_consume_twice(self):
        grant = scan_access.prepare_scan_access("user-123", now_epoch=60)

        first = scan_access.consume_scan_access(grant, "request-123", now_iso="2026-05-11T00:00:00+00:00")
        second = scan_access.consume_scan_access(first | {"accountId": "user-123", "consumptionType": "monthly", "entitlement": first}, "request-123", now_iso="2026-05-11T00:01:00+00:00")

        self.assertEqual(first["remainingMonthlyScans"], 4)
        self.assertEqual(second["remainingMonthlyScans"], 4)
        self.assertEqual(second["lastScanRequestId"], "request-123")

    def test_result_completion_and_quota_consumption_share_one_transaction(self):
        grant = scan_access.prepare_scan_access("user-123", now_epoch=60)

        updated = scan_access.commit_scan_and_request(
            grant,
            "request-123",
            "payload-hash",
            {"schemaVersion": "1.0", "requestId": "request-123"},
            now_epoch=100,
            now_iso="2026-05-11T00:00:00Z",
        )

        self.assertEqual(updated["remainingMonthlyScans"], 4)
        self.assertEqual(len(transaction_fake.transactions), 1)
        transaction = transaction_fake.transactions[0]
        self.assertEqual(len(transaction), 3)
        request_update = transaction[0]["Update"]
        self.assertEqual(request_update["TableName"], "test-analysis-abuse")
        self.assertIn("#status = :result_ready", request_update["ConditionExpression"])
        consumption_event = transaction[1]["Put"]
        self.assertEqual(
            consumption_event["Item"]["PK"],
            {"S": f"ANALYSIS#CONSUMPTION#{scan_access._hashed_account_id('user-123')}"},
        )
        entitlement_put = transaction[2]["Put"]
        self.assertEqual(entitlement_put["TableName"], "test-entitlements")
        self.assertEqual(
            entitlement_put["Item"]["remainingMonthlyScans"],
            {"N": "4"},
        )

    def test_transaction_conflict_is_retryable_without_partial_local_success(self):
        grant = scan_access.prepare_scan_access("user-123", now_epoch=60)
        try:
            error = scan_access.ClientError(
                {"Error": {"Code": "TransactionCanceledException"}},
                "TransactWriteItems",
            )
        except TypeError:
            error = scan_access.ClientError({"Error": {"Code": "TransactionCanceledException"}})
        transaction_fake.error = error

        with self.assertRaises(AppError) as context:
            scan_access.commit_scan_and_request(
                grant,
                "request-123",
                "payload-hash",
                {"schemaVersion": "1.0", "requestId": "request-123"},
            )

        self.assertEqual(context.exception.code, "REQUEST_IN_PROGRESS")
        self.assertTrue(context.exception.retryable)

    def test_opted_in_analysis_atomically_writes_campaign_outbox(self):
        grant = scan_access.prepare_scan_access("user-123", now_epoch=60)
        event_id = "7fbce2ac-bd2e-4d2e-9ec6-1f895a482abc"
        scan_access.CAMPAIGN_OBSERVATION_RETENTION_HOURS = 168

        scan_access.commit_scan_and_request(
            grant,
            "request-123",
            "payload-hash",
            {
                "schemaVersion": "1.0",
                "requestId": "request-123",
                "riskLevel": "high",
                "signals": ["payment_request"],
            },
            campaign_payload={
                "campaignConsentGranted": True,
                "sourceType": "pasted_text",
                "sanitizedText": "Send money to [PAYMENT_HANDLE_1].",
                "images": ["forbidden-binary-reference"],
                "files": [{"name": "forbidden.txt", "content": b"forbidden"}],
                "screenshots": [b"forbidden"],
                "rawOcrText": "forbidden raw OCR",
                "unknownField": "must not cross the boundary",
            },
            statistics_event_id=event_id,
            now_epoch=100,
            now_iso="2026-05-11T00:00:00Z",
        )

        transaction = transaction_fake.transactions[0]
        self.assertEqual(len(transaction), 4)
        outbox = transaction[3]["Put"]
        self.assertEqual(outbox["TableName"], "test-campaign-outbox")
        self.assertEqual(outbox["Item"]["PK"], {"S": f"EVENT#{event_id}"})
        self.assertEqual(outbox["Item"]["accountId"], {"S": "user-123"})
        self.assertEqual(
            set(outbox["Item"]),
            {
                "PK",
                "SK",
                "schemaVersion",
                "recordVersion",
                "eventType",
                "environment",
                "statisticsEventId",
                "accountId",
                "campaignConsentGranted",
                "observedAtEpoch",
                "sourceType",
                "sanitizedText",
                "riskLevel",
                "signalIds",
                "expiresAt",
            },
        )
        self.assertEqual(outbox["Item"]["expiresAt"], {"N": str(100 + 72 * 60 * 60)})
        serialized = str(outbox["Item"])
        self.assertNotIn("request-123", serialized)
        self.assertNotIn("forbidden", serialized)

    def test_opted_in_commit_requires_the_persisted_uuid4(self):
        grant = scan_access.prepare_scan_access("user-123", now_epoch=60)

        for event_id in (None, "not-a-uuid", "550e8400-e29b-11d4-a716-446655440000"):
            with self.subTest(event_id=event_id):
                with self.assertRaises(AppError) as context:
                    scan_access.commit_scan_and_request(
                        grant,
                        "request-123",
                        "payload-hash",
                        {"schemaVersion": "1.0", "requestId": "request-123"},
                        campaign_payload={
                            "campaignConsentGranted": True,
                            "sourceType": "pasted_text",
                            "sanitizedText": "Send money to [PAYMENT_HANDLE_1].",
                        },
                        statistics_event_id=event_id,
                        now_epoch=100,
                    )

                self.assertEqual(context.exception.code, "SERVER_UNAVAILABLE")
                self.assertFalse(context.exception.retryable)
                self.assertEqual(transaction_fake.transactions, [])

    def test_declined_campaign_consent_does_not_write_outbox(self):
        grant = scan_access.prepare_scan_access("user-123", now_epoch=60)

        scan_access.commit_scan_and_request(
            grant,
            "request-123",
            "payload-hash",
            {"schemaVersion": "1.0", "requestId": "request-123"},
            campaign_payload={"campaignConsentGranted": False},
            now_epoch=100,
        )

        self.assertEqual(len(transaction_fake.transactions[0]), 3)

    def test_atomic_commit_rejects_a_result_for_another_request(self):
        grant = scan_access.prepare_scan_access("user-123", now_epoch=60)

        with self.assertRaises(AppError) as context:
            scan_access.commit_scan_and_request(
                grant,
                "request-123",
                "payload-hash",
                {"schemaVersion": "1.0", "requestId": "another-request"},
            )

        self.assertEqual(context.exception.code, "SERVER_UNAVAILABLE")
        self.assertEqual(transaction_fake.transactions, [])

    def test_rejects_when_entitlement_is_exhausted(self):
        entitlements_fake.put_item(
            {
                "PK": "USER#user-123",
                "SK": "ENTITLEMENT",
                "accountId": "user-123",
                "entitlementTier": "FREE",
                "subscriptionStatus": "expired",
                "monthlyScanLimit": 5,
                "remainingMonthlyScans": 0,
                "remainingCredits": 0,
                "createdAt": "2026-05-01T00:00:00+00:00",
                "updatedAt": "2026-05-01T00:00:00+00:00",
            }
        )

        with self.assertRaises(AppError) as context:
            scan_access.prepare_scan_access("user-123", now_epoch=60)

        self.assertEqual(context.exception.code, "ENTITLEMENT_EXHAUSTED")

    def test_rejects_when_scan_rate_cap_is_exceeded(self):
        for _ in range(10):
            scan_access.prepare_scan_access("user-123", now_epoch=60)

        with self.assertRaises(AppError) as context:
            scan_access.prepare_scan_access("user-123", now_epoch=60)

        self.assertEqual(context.exception.code, "RATE_LIMITED")


if __name__ == "__main__":
    unittest.main()
