import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
MODULE_DIR = SRC_DIR / "purchase_handoff"
for path in (SRC_DIR, MODULE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

for module_name in [
    "config",
    "errors",
    "google_play_client",
    "idempotency",
    "verification",
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

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["PK"], Item["SK"])
        if ConditionExpression and key in self.items:
            raise FakeClientError({"Error": {"Code": "ConditionalCheckFailedException"}})
        self.items[key] = dict(Item)
        return {}


class FakeClientError(Exception):
    def __init__(self, response=None):
        super().__init__("client error")
        self.response = response or {}


fake_table = FakeTable()


class FakeResource:
    def Table(self, _name):
        return fake_table


boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *args, **kwargs: FakeResource()
boto3_stub.client = lambda *args, **kwargs: object()
sys.modules["boto3"] = boto3_stub

botocore_ex = types.ModuleType("botocore.exceptions")
botocore_ex.ClientError = FakeClientError
sys.modules["botocore.exceptions"] = botocore_ex


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_load_module("errors", MODULE_DIR / "errors.py")
_load_module("config", MODULE_DIR / "config.py")
_load_module("google_play_client", MODULE_DIR / "google_play_client.py")
_load_module("idempotency", MODULE_DIR / "idempotency.py")
_load_module("verification", MODULE_DIR / "verification.py")
import shared_entitlements.service as shared_service  # noqa: E402
import idempotency as idempotency_module  # noqa: E402
service = _load_module("service", MODULE_DIR / "service.py")


class PurchaseHandoffServiceTests(unittest.TestCase):
    def setUp(self):
        fake_table.items.clear()
        shared_service.table = fake_table
        shared_service.participation_table = fake_table
        idempotency_module.table = fake_table

    def test_accepts_verified_google_play_purchase(self):
        payload = {
            "productId": "trustcheck_radar_pro_monthly",
            "platform": "google_play",
            "purchaseToken": "purchase-token-12345",
            "purchaseState": "PURCHASED",
            "packageName": "com.example.app",
            "orderId": None,
            "purchaseTime": None,
            "subscriptionMetadata": {},
        }

        with mock.patch.object(
            service,
            "verify_purchase",
            return_value={
                "status": "accepted",
                "reason": "Google Play verified the subscription purchase.",
                "normalizedStatus": "active",
                "isAccessGranted": True,
                "billingPeriodStartUtc": "2026-06-27T19:15:00Z",
                "billingPeriodEndUtc": "2026-07-27T19:15:00Z",
            },
        ):
            result = service.process_purchase_handoff(account_id="user-123", payload=payload)

        self.assertEqual(result["verificationStatus"], "accepted")
        self.assertEqual(result["entitlement"]["tier"], "pro")
        self.assertEqual(result["entitlement"]["status"], "active")
        self.assertEqual(result["entitlement"]["platform"], "google_play")
        self.assertEqual(result["entitlement"]["productId"], "trustcheck_radar_pro_monthly")
        self.assertEqual(result["usage"]["remainingCount"], 100)
        self.assertFalse(result["idempotencyReplay"])

    def test_duplicate_replay_returns_safe_result_without_double_granting(self):
        token_hash = idempotency_module.hash_purchase_token("purchase-token-12345")
        fake_table.put_item(
            {
                "PK": "USER#user-123",
                "SK": "ENTITLEMENT#google_play#trustcheck_radar_pro_monthly",
                "accountId": "user-123",
                "entitlementTier": "PRO",
                "subscriptionStatus": "active",
                "platform": "google_play",
                "productId": "trustcheck_radar_pro_monthly",
                "billingPeriodStartUtc": "2026-06-27T19:15:00Z",
                "billingPeriodEndUtc": "2026-07-27T19:15:00Z",
                "isAccessGranted": True,
                "monthlyScanLimit": 100,
                "remainingMonthlyScans": 93,
                "remainingCredits": 0,
                "createdAt": "2026-06-27T19:15:05Z",
                "updatedAt": "2026-06-28T00:00:00Z",
            }
        )
        fake_table.put_item(
            {
                "PK": f"TOKEN#{token_hash}",
                "SK": "IDEMPOTENCY",
                "purchaseTokenHash": token_hash,
                "accountId": "user-123",
                "platform": "google_play",
                "productId": "trustcheck_radar_pro_monthly",
                "verificationStatus": "accepted",
                "normalizedStatus": "active",
                "updatedAt": "2026-06-28T00:00:00Z",
            }
        )
        payload = {
            "productId": "trustcheck_radar_pro_monthly",
            "platform": "google_play",
            "purchaseToken": "purchase-token-12345",
            "purchaseState": "PURCHASED",
            "packageName": "com.example.app",
            "orderId": None,
            "purchaseTime": None,
            "subscriptionMetadata": {},
        }

        result = service.process_purchase_handoff(account_id="user-123", payload=payload)

        self.assertEqual(result["verificationStatus"], "accepted")
        self.assertTrue(result["idempotencyReplay"])
        self.assertEqual(result["usage"]["remainingCount"], 93)

    def test_returns_rejected_for_canceled_or_expired_state(self):
        payload = {
            "productId": "trustcheck_radar_pro_monthly",
            "platform": "google_play",
            "purchaseToken": "purchase-token-12345",
            "purchaseState": "PURCHASED",
            "packageName": "com.example.app",
            "orderId": None,
            "purchaseTime": None,
            "subscriptionMetadata": {},
        }

        with mock.patch.object(
            service,
            "verify_purchase",
            return_value={
                "status": "rejected",
                "reason": "Google Play reports the subscription status as expired.",
                "normalizedStatus": "expired",
                "isAccessGranted": False,
                "billingPeriodStartUtc": "2026-05-27T19:15:00Z",
                "billingPeriodEndUtc": "2026-06-27T19:15:00Z",
            },
        ):
            result = service.process_purchase_handoff(account_id="user-123", payload=payload)

        self.assertEqual(result["verificationStatus"], "rejected")
        self.assertEqual(result["entitlement"]["tier"], "free")
        self.assertEqual(result["entitlement"]["status"], "expired")

    def test_returns_pending_for_pending_state(self):
        payload = {
            "productId": "trustcheck_radar_pro_monthly",
            "platform": "google_play",
            "purchaseToken": "purchase-token-12345",
            "purchaseState": "PENDING",
            "packageName": "com.example.app",
            "orderId": None,
            "purchaseTime": None,
            "subscriptionMetadata": {},
        }

        with mock.patch.object(
            service,
            "verify_purchase",
            return_value={
                "status": "pending",
                "reason": "Google Play reports the subscription purchase as pending.",
                "normalizedStatus": "pending",
                "isAccessGranted": False,
                "billingPeriodStartUtc": None,
                "billingPeriodEndUtc": None,
            },
        ):
            result = service.process_purchase_handoff(account_id="user-123", payload=payload)

        self.assertEqual(result["verificationStatus"], "pending")
        self.assertEqual(result["entitlement"]["status"], "expired")

    def test_returns_retryable_failure_for_upstream_issue(self):
        payload = {
            "productId": "trustcheck_radar_pro_monthly",
            "platform": "google_play",
            "purchaseToken": "purchase-token-12345",
            "purchaseState": "PURCHASED",
            "packageName": "com.example.app",
            "orderId": None,
            "purchaseTime": None,
            "subscriptionMetadata": {},
        }

        with mock.patch.object(
            service,
            "verify_purchase",
            return_value={
                "status": "failed_retryable",
                "reason": "Unable to reach Google Play verification service.",
                "normalizedStatus": None,
                "isAccessGranted": False,
                "billingPeriodStartUtc": None,
                "billingPeriodEndUtc": None,
            },
        ):
            result = service.process_purchase_handoff(account_id="user-123", payload=payload)

        self.assertEqual(result["verificationStatus"], "failed_retryable")
        self.assertFalse(result["idempotencyReplay"])


if __name__ == "__main__":
    unittest.main()
