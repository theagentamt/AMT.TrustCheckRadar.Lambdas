import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "purchase_handoff"
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))


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
sys.modules.setdefault("boto3", boto3_stub)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_load_module("errors", MODULE_DIR / "errors.py")
_load_module("config", MODULE_DIR / "config.py")
_load_module("verification", MODULE_DIR / "verification.py")
service = _load_module("service", MODULE_DIR / "service.py")


class PurchaseHandoffServiceTests(unittest.TestCase):
    def setUp(self):
        fake_table.items.clear()
        service.table = fake_table

    def test_accepts_pro_purchase_and_sets_pro_entitlement(self):
        payload = {
            "productId": "pro_monthly",
            "platform": "ios",
            "purchaseState": "purchased",
            "proof": {"transactionId": "tx-123"},
        }

        result = service.process_purchase_handoff(account_id="user-123", payload=payload)

        self.assertEqual(result["verificationStatus"], "accepted")
        self.assertEqual(result["entitlement"]["tier"], "PRO")
        self.assertEqual(result["entitlement"]["remainingMonthlyScans"], 100)
        self.assertEqual(result["entitlement"]["remainingCredits"], 0)

    def test_accepts_credit_purchase_and_adds_credits(self):
        fake_table.put_item(
            {
                "PK": "USER#user-123",
                "SK": "ENTITLEMENT",
                "accountId": "user-123",
                "entitlementTier": "FREE",
                "monthlyScanLimit": 5,
                "remainingMonthlyScans": 2,
                "remainingCredits": 10,
                "createdAt": "2026-05-01T00:00:00+00:00",
                "updatedAt": "2026-05-01T00:00:00+00:00",
                "lastVerifiedAt": None,
            }
        )
        payload = {
            "productId": "credits_20",
            "platform": "android",
            "purchaseState": "purchased",
            "proof": {"purchaseToken": "token-123"},
        }

        result = service.process_purchase_handoff(account_id="user-123", payload=payload)

        self.assertEqual(result["verificationStatus"], "accepted")
        self.assertEqual(result["entitlement"]["tier"], "FREE")
        self.assertEqual(result["entitlement"]["remainingMonthlyScans"], 2)
        self.assertEqual(result["entitlement"]["remainingCredits"], 30)

    def test_returns_pending_without_changing_entitlement(self):
        payload = {
            "productId": "pro_monthly",
            "platform": "ios",
            "purchaseState": "pending",
            "proof": {"transactionId": "tx-123"},
        }

        result = service.process_purchase_handoff(account_id="user-123", payload=payload)

        self.assertEqual(result["verificationStatus"], "pending")
        self.assertEqual(result["entitlement"]["tier"], "FREE")

    def test_returns_rejected_without_changing_entitlement(self):
        payload = {
            "productId": "credits_20",
            "platform": "android",
            "purchaseState": "failed",
            "proof": {"purchaseToken": "token-123"},
        }

        result = service.process_purchase_handoff(account_id="user-123", payload=payload)

        self.assertEqual(result["verificationStatus"], "rejected")
        self.assertEqual(result["entitlement"]["tier"], "FREE")

    def test_returns_retryable_failure_when_verification_adapter_cannot_verify(self):
        payload = {
            "productId": "pro_monthly",
            "platform": "ios",
            "purchaseState": "purchased",
            "proof": {"transactionId": "tx-123"},
        }

        with mock.patch.object(
            service,
            "verify_purchase",
            return_value={"status": "failed_retryable", "reason": "Temporary verification outage."},
        ):
            result = service.process_purchase_handoff(account_id="user-123", payload=payload)

        self.assertEqual(result["verificationStatus"], "failed_retryable")
        self.assertEqual(result["entitlement"]["tier"], "FREE")


if __name__ == "__main__":
    unittest.main()
