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


def _deserialize(item):
    result = {}
    for key, value in item.items():
        kind, raw = next(iter(value.items()))
        if kind == "S":
            result[key] = raw
        elif kind == "N":
            result[key] = int(raw)
        elif kind == "BOOL":
            result[key] = raw
        elif kind == "NULL":
            result[key] = None
        elif kind == "M":
            result[key] = _deserialize(raw)
    return result


class FakeClient:
    def __init__(self):
        self.transactions = []

    def transact_write_items(self, TransactItems):
        self.transactions.append(TransactItems)
        for operation in TransactItems:
            if "Put" in operation:
                item = _deserialize(operation["Put"]["Item"])
                fake_table.items[(item["PK"], item["SK"])] = item


fake_client = FakeClient()


class FakeResource:
    def Table(self, _name):
        return fake_table


boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *args, **kwargs: FakeResource()
boto3_stub.client = lambda *args, **kwargs: fake_client
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
        fake_client.transactions.clear()
        shared_service.table = fake_table
        shared_service.participation_table = fake_table
        idempotency_module.table = fake_table
        service.users_table = fake_table
        service.deletion_ledger_table = fake_table
        service.dynamodb_client = fake_client
        service.config.ENTITLEMENTS_TABLE_NAME = "entitlements"
        service.config.USERS_TABLE_NAME = "users"
        service.config.DELETION_LEDGER_TABLE_NAME = "deletion-ledger"
        fake_table.put_item({
            "PK": "USER#user-123", "SK": "PROFILE", "sub": "user-123",
            "status": "ACTIVE", "ageVerified": True,
        })

    def _candidate_payload(self):
        return {"productId": "trustcheck_radar_pro_monthly", "platform": "google_play",
                "purchaseToken": "purchase-token-12345", "purchaseState": "RESTORED",
                "packageName": "com.example.app", "orderId": "must-not-persist",
                "purchaseTime": None, "subscriptionMetadata": {}}

    def test_candidate_fresh_verifies_even_cached_same_account_token(self):
        import shared_purchase_ownership
        payload = self._candidate_payload()
        token_hash = idempotency_module.hash_purchase_token(payload["purchaseToken"])
        fake_table.put_item({"PK": "TOKEN#" + token_hash, "SK": "IDEMPOTENCY", "accountId": "user-123", "verificationStatus": "accepted"})
        verification = {"status": "accepted", "normalizedStatus": "active", "isAccessGranted": True,
                        "billingPeriodStartUtc": "2026-06-27T19:15:00Z", "billingPeriodEndUtc": "2026-07-27T19:15:00Z"}
        with mock.patch.object(service.config, "PURCHASE_OWNERSHIP_CANDIDATE_ENABLED", True), \
                mock.patch.object(service.config, "GOOGLE_PLAY_PACKAGE_NAME", "com.example.app"), \
                mock.patch.object(shared_purchase_ownership, "OwnershipStore") as store_type, \
                mock.patch.object(shared_purchase_ownership, "verified_lineage", return_value=((token_hash,), verification)) as verify:
            store = store_type.return_value
            store._get.return_value = None
            result = service.process_purchase_handoff(account_id="user-123", payload=payload)
            verify.assert_called_once()
            store.inventory.assert_called_once()
            store.claim.assert_called_once()
            self.assertNotIn("orderId", store.claim.call_args.kwargs["entitlement"])
            self.assertEqual(store.claim.call_args.kwargs["entitlement"]["purchaseTokenHash"], token_hash)
            self.assertFalse(result["idempotencyReplay"])
            self.assertEqual(result["verificationStatus"], "accepted")

    def test_candidate_unverified_purchase_never_creates_ownership(self):
        import shared_purchase_ownership
        with mock.patch.object(service.config, "PURCHASE_OWNERSHIP_CANDIDATE_ENABLED", True), \
                mock.patch.object(service.config, "GOOGLE_PLAY_PACKAGE_NAME", "com.example.app"), \
                mock.patch.object(shared_purchase_ownership, "OwnershipStore") as store_type, \
                mock.patch.object(shared_purchase_ownership, "verified_lineage", side_effect=shared_purchase_ownership.OwnershipError("PURCHASE_NOT_ACTIVE")):
            store_type.return_value._get.return_value = None
            result = service.process_purchase_handoff(account_id="user-123", payload=self._candidate_payload())
            store_type.return_value.claim.assert_not_called()
            self.assertEqual(result["verificationStatus"], "rejected")

    def test_candidate_inventory_missing_never_calls_provider_or_claims(self):
        import shared_purchase_ownership
        with mock.patch.object(service.config, "PURCHASE_OWNERSHIP_CANDIDATE_ENABLED", True), \
                mock.patch.object(service.config, "GOOGLE_PLAY_PACKAGE_NAME", "com.example.app"), \
                mock.patch.object(shared_purchase_ownership, "OwnershipStore") as store_type, \
                mock.patch.object(shared_purchase_ownership, "verified_lineage") as verify:
            store_type.return_value._get.return_value = None
            store_type.return_value.inventory.side_effect = shared_purchase_ownership.OwnershipError("PURCHASE_LEGACY_COVERAGE_UNVERIFIED")
            result = service.process_purchase_handoff(account_id="user-123", payload=self._candidate_payload())
            verify.assert_not_called()
            store_type.return_value.claim.assert_not_called()
            self.assertEqual(result["verificationStatus"], "failed_retryable")

    def test_retired_legacy_path_preserves_paid_usage_and_token_without_provider(self):
        import copy
        fake_table.put_item({"PK":"USER#user-123", "SK":"ENTITLEMENT#google_play#trustcheck_radar_pro_monthly",
            "entitlementTier":"PRO", "remainingMonthlyScans":37,"remainingCredits":2,
            "purchaseTokenHash":"existing-owner", "billingPeriodStartUtc":"2026-09-01T00:00:00Z"})
        fake_table.put_item({"PK":"TOKEN#existing-owner","SK":"IDEMPOTENCY","accountId":"user-123"})
        before=copy.deepcopy(fake_table.items)
        with mock.patch.object(service.config,"PURCHASE_OWNERSHIP_CANDIDATE_ENABLED",False), \
             mock.patch("verification.verify_purchase") as verify, \
             mock.patch.object(service,"_process_owned_purchase") as owned:
            for state in ("PURCHASED","RESTORED","PENDING"):
                with self.subTest(state=state), self.assertRaises(service.AppError) as raised:
                    service.process_purchase_handoff(account_id="user-123",payload=self._candidate_payload()|{"purchaseState":state})
                self.assertEqual(raised.exception.code,"LEGACY_MIGRATION_REQUIRED")
                self.assertFalse(raised.exception.retryable)
            verify.assert_not_called();owned.assert_not_called()
        self.assertFalse(hasattr(service,"_commit_verified_result"))
        self.assertEqual(fake_table.items,before)
        self.assertEqual(fake_client.transactions,[])

    def test_fixed_account_deletion_fence_blocks_purchase_replay_and_writes(self):
        fake_table.put_item({
            "PK": "ACCOUNT#user-123", "SK": "ACCOUNT_DELETION",
            "status": "REQUESTED",
        })
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

        with self.assertRaises(service.AppError) as raised:
            service.process_purchase_handoff(account_id="user-123", payload=payload)

        self.assertEqual(raised.exception.code, "FORBIDDEN")
        self.assertEqual(fake_client.transactions, [])


if __name__ == "__main__":
    unittest.main()
