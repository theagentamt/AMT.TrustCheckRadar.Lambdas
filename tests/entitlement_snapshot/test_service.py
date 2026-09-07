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

    def get_item(self, Key, **_kwargs):
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
        shared_service.participation_table = fake_table
        service.entitlements_table = fake_table

    def _put_entitlement(
        self,
        *,
        account_id: str = "user-123",
        tier: str = "PRO",
        status: str = "active",
        is_access_granted: bool = True,
        billing_period_start: str = "2026-07-01T00:00:00Z",
        billing_period_end: str = "2026-08-01T00:00:00Z",
        remaining_monthly_scans: int = 91,
    ):
        fake_table.put_item(
            {
                "PK": f"USER#{account_id}",
                "SK": "ENTITLEMENT#google_play#trustcheck_radar_pro_monthly",
                "accountId": account_id,
                "entitlementTier": tier,
                "subscriptionStatus": status,
                "platform": "google_play",
                "productId": "trustcheck_radar_pro_monthly",
                "billingPeriodStartUtc": billing_period_start,
                "billingPeriodEndUtc": billing_period_end,
                "isAccessGranted": is_access_granted,
                "monthlyScanLimit": 100 if tier == "PRO" else 10,
                "remainingMonthlyScans": remaining_monthly_scans,
                "remainingCredits": 0,
                "createdAt": "2026-07-01T00:00:00Z",
                "updatedAt": "2026-07-02T00:00:00Z",
            }
        )

    def test_returns_free_user_snapshot_without_usage_item(self):
        result = service.get_entitlement_snapshot("user-123")

        self.assertEqual(result["entitlement"]["tier"], "free")
        self.assertEqual(result["entitlement"]["status"], "expired")
        self.assertFalse(result["entitlement"]["isAccessGranted"])
        self.assertEqual(result["usage"]["periodMode"], "billing_cycle")
        self.assertEqual(result["usage"]["limit"], 10)
        self.assertEqual(result["usage"]["usedCount"], 0)
        self.assertEqual(result["usage"]["remaining"], 10)
        self.assertTrue(result["guidance"]["restoreRecommended"])

    def test_returns_active_pro_user_snapshot_with_usage_item(self):
        self._put_entitlement()
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
        self._put_entitlement(
            tier="FREE",
            status="expired",
            is_access_granted=False,
            billing_period_start="2026-06-01T00:00:00Z",
            billing_period_end="2026-07-01T00:00:00Z",
            remaining_monthly_scans=2,
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

    def test_returns_pending_state_without_restore_recommendation(self):
        self._put_entitlement(status="pending", is_access_granted=False)

        result = service.get_entitlement_snapshot("user-123")

        self.assertEqual(result["entitlement"]["status"], "pending")
        self.assertFalse(result["entitlement"]["isAccessGranted"])
        self.assertFalse(result["guidance"]["restoreRecommended"])

    def test_returns_grace_state_with_access_granted(self):
        self._put_entitlement(status="grace", is_access_granted=True)

        result = service.get_entitlement_snapshot("user-123")

        self.assertEqual(result["entitlement"]["status"], "grace")
        self.assertTrue(result["entitlement"]["isAccessGranted"])
        self.assertFalse(result["guidance"]["restoreRecommended"])

    def test_returns_hold_state_with_restore_recommended(self):
        self._put_entitlement(status="hold", is_access_granted=False)

        result = service.get_entitlement_snapshot("user-123")

        self.assertEqual(result["entitlement"]["status"], "hold")
        self.assertFalse(result["entitlement"]["isAccessGranted"])
        self.assertTrue(result["guidance"]["restoreRecommended"])

    def test_returns_paused_state_with_restore_recommended(self):
        self._put_entitlement(status="paused", is_access_granted=False)

        result = service.get_entitlement_snapshot("user-123")

        self.assertEqual(result["entitlement"]["status"], "paused")
        self.assertFalse(result["entitlement"]["isAccessGranted"])
        self.assertTrue(result["guidance"]["restoreRecommended"])

    def test_returns_canceled_state_without_restore_when_access_still_valid(self):
        self._put_entitlement(status="canceled", is_access_granted=True)

        result = service.get_entitlement_snapshot("user-123")

        self.assertEqual(result["entitlement"]["status"], "canceled")
        self.assertTrue(result["entitlement"]["isAccessGranted"])
        self.assertFalse(result["guidance"]["restoreRecommended"])

    def test_repeated_refresh_returns_stable_snapshot(self):
        self._put_entitlement(status="active", is_access_granted=True)

        first = service.get_entitlement_snapshot("user-123")
        second = service.get_entitlement_snapshot("user-123")

        self.assertEqual(first, second)

    def test_enrolled_free_user_gets_fifteen_with_used_count_preserved(self):
        self._put_entitlement(
            tier="FREE", status="expired", is_access_granted=False,
            remaining_monthly_scans=6,
        )
        fake_table.put_item({
            "PK": "USER#user-123", "SK": "CAMPAIGN_PARTICIPATION", "state": "enrolled",
            "stateVersion": 1, "consentEpochId": "15c81ba4-2fa6-43c3-8895-889f08c931bf",
            "noticeVersion": "notice-2026-09", "environment": "dev",
        })
        fake_table.put_item({
            "PK": "USER#user-123", "SK": "USAGE#2026-07-01T00:00:00Z",
            "monthlyLimit": 10, "usedCount": 4, "remainingCount": 6,
        })

        result = service.get_entitlement_snapshot("user-123")

        self.assertEqual(result["usage"]["limit"], 15)
        self.assertEqual(result["usage"]["usedCount"], 4)
        self.assertEqual(result["usage"]["remaining"], 11)


if __name__ == "__main__":
    unittest.main()
