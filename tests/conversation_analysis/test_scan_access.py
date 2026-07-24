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


entitlements_fake = FakeTable()
abuse_fake = FakeTable()


class FakeResource:
    def Table(self, name):
        if "analysis-abuse" in name:
            return abuse_fake
        return entitlements_fake


boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *args, **kwargs: FakeResource()
boto3_stub.client = lambda *args, **kwargs: object()
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
