import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
MODULE_DIR = SRC_DIR / "entitlement_snapshot"
for path in (SRC_DIR, MODULE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

for module_name in [
    "config",
    "errors",
    "service",
    "shared_entitlements",
    "shared_entitlements.service",
    "shared_entitlements.config",
]:
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


fake_table = FakeTable()


class FakeResource:
    def Table(self, _name):
        return fake_table


boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *args, **kwargs: FakeResource()
boto3_stub.client = lambda *args, **kwargs: object()
sys.modules["boto3"] = boto3_stub


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


import shared_entitlements.service as shared_service  # noqa: E402
service = _load_module("service", MODULE_DIR / "service.py")


class EntitlementSnapshotServiceTests(unittest.TestCase):
    def setUp(self):
        fake_table.items.clear()
        shared_service.table = fake_table
        service.entitlements_table = fake_table

    def test_returns_free_user_snapshot_without_usage_item(self):
        result = service.get_entitlement_snapshot("user-123")

        self.assertEqual(result["entitlement"]["tier"], "free")
        self.assertEqual(result["entitlement"]["status"], "expired")
        self.assertFalse(result["entitlement"]["isAccessGranted"])
        self.assertEqual(result["usage"]["periodMode"], "billing_cycle")
        self.assertEqual(result["usage"]["limit"], 5)
        self.assertEqual(result["usage"]["usedCount"], 0)
        self.assertEqual(result["usage"]["remaining"], 5)
        self.assertTrue(result["guidance"]["restoreRecommended"])

    def test_returns_active_pro_user_snapshot_with_usage_item(self):
        fake_table.put_item(
            {
                "PK": "USER#user-123",
                "SK": "ENTITLEMENT#google_play#trustcheck_radar_pro_monthly",
                "accountId": "user-123",
                "entitlementTier": "PRO",
                "subscriptionStatus": "active",
                "platform": "google_play",
                "productId": "trustcheck_radar_pro_monthly",
                "billingPeriodStartUtc": "2026-07-01T00:00:00Z",
                "billingPeriodEndUtc": "2026-08-01T00:00:00Z",
                "isAccessGranted": True,
                "monthlyScanLimit": 100,
                "remainingMonthlyScans": 91,
                "remainingCredits": 0,
                "createdAt": "2026-07-01T00:00:00Z",
                "updatedAt": "2026-07-02T00:00:00Z",
            }
        )
        fake_table.put_item(
            {
                "PK": "USER#user-123",
                "SK": "USAGE#2026-07-01T00:00:00Z",
                "usedCount": 9,
                "remainingCount": 91,
                "monthlyLimit": 100,
            }
        )

        result = service.get_entitlement_snapshot("user-123")

        self.assertEqual(result["entitlement"]["tier"], "pro")
        self.assertEqual(result["entitlement"]["status"], "active")
        self.assertTrue(result["entitlement"]["isAccessGranted"])
        self.assertEqual(result["usage"]["limit"], 100)
        self.assertEqual(result["usage"]["usedCount"], 9)
        self.assertEqual(result["usage"]["remaining"], 91)
        self.assertFalse(result["guidance"]["restoreRecommended"])

    def test_returns_expired_user_snapshot(self):
        fake_table.put_item(
            {
                "PK": "USER#user-123",
                "SK": "ENTITLEMENT#google_play#trustcheck_radar_pro_monthly",
                "accountId": "user-123",
                "entitlementTier": "FREE",
                "subscriptionStatus": "expired",
                "platform": "google_play",
                "productId": "trustcheck_radar_pro_monthly",
                "billingPeriodStartUtc": "2026-06-01T00:00:00Z",
                "billingPeriodEndUtc": "2026-07-01T00:00:00Z",
                "isAccessGranted": False,
                "monthlyScanLimit": 5,
                "remainingMonthlyScans": 2,
                "remainingCredits": 0,
                "createdAt": "2026-06-01T00:00:00Z",
                "updatedAt": "2026-07-02T00:00:00Z",
            }
        )

        result = service.get_entitlement_snapshot("user-123")

        self.assertEqual(result["entitlement"]["status"], "expired")
        self.assertFalse(result["entitlement"]["isAccessGranted"])
        self.assertEqual(result["usage"]["remaining"], 2)
        self.assertTrue(result["guidance"]["restoreRecommended"])

    def test_returns_server_unavailable_when_entitlements_table_is_missing(self):
        with mock.patch.object(service, "entitlements_table", None):
            with self.assertRaises(service.AppError) as context:
                service.get_entitlement_snapshot("user-123")

        self.assertEqual(context.exception.code, "SERVER_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
