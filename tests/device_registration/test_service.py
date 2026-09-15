import importlib.util
import os
import sys
import types
import unittest
from decimal import Decimal
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "device_registration"
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

os.environ.update({
    "DEVICE_BINDINGS_TABLE_NAME": "device-bindings",
    "USERS_TABLE_NAME": "users",
    "DELETION_LEDGER_TABLE_NAME": "deletion-ledger",
})


class FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key, **_kwargs):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}

    def put_item(self, Item):
        self.items[(Item["PK"], Item["SK"])] = dict(Item)
        return {}

    def query(self, IndexName=None, KeyConditionExpression=None, ExpressionAttributeValues=None, Limit=None, ScanIndexForward=None):
        gsi1pk = ExpressionAttributeValues[":gsi1pk"]
        matches = [item for item in self.items.values() if item.get("GSI1PK") == gsi1pk]
        matches.sort(key=lambda item: item.get("GSI1SK", ""), reverse=not ScanIndexForward)
        if Limit:
            matches = matches[:Limit]
        return {"Items": matches}


fake_table = FakeTable()


def _deserialize(item):
    result = {}
    for key, value in item.items():
        kind, raw = next(iter(value.items()))
        result[key] = raw if kind == "S" else int(raw) if kind == "N" else raw if kind == "BOOL" else None
    return result


class FakeClient:
    def __init__(self):
        self.transactions = []
        self.cancel_next = False

    def transact_write_items(self, TransactItems):
        self.transactions.append(TransactItems)
        if self.cancel_next:
            self.cancel_next = False
            error = RuntimeError("cancelled")
            error.response = {"Error": {"Code": "TransactionCanceledException"}}
            raise error
        for operation in TransactItems:
            if "Put" in operation:
                item = _deserialize(operation["Put"]["Item"])
                fake_table.items[(item["PK"], item["SK"])] = item
            elif "Update" in operation:
                update = operation["Update"]
                key = _deserialize(update["Key"])
                values = _deserialize(update["ExpressionAttributeValues"])
                item = fake_table.items[(key["PK"], key["SK"])]
                item.update({
                    "bindingFingerprint": values[":target"],
                    "stateVersion": values[":next"], "updatedAt": values[":now"],
                })


fake_client = FakeClient()


class FakeResource:
    def Table(self, _name):
        return fake_table


boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *args, **kwargs: FakeResource()
boto3_stub.client = lambda *args, **kwargs: fake_client
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
service = _load_module("service", MODULE_DIR / "service.py")


class DeviceRegistrationServiceTests(unittest.TestCase):
    def setUp(self):
        fake_table.items.clear()
        fake_client.transactions.clear()
        fake_client.cancel_next = False
        service.table = fake_table
        service.dynamodb_client = fake_client

    def test_returns_new_for_first_device(self):
        result = service.register_device(
            account_id="user-123",
            binding_fingerprint="fp-1",
            platform="ios",
            os_version="18.4",
        )

        self.assertEqual(result["decision"], "NEW")
        item = fake_table.items[("USER#user-123", "DEVICE#fp-1")]
        self.assertEqual(item["status"], "ACTIVE")
        self.assertEqual(item["bindingFingerprint"], "fp-1")
        self.assertEqual(
            fake_table.items[("USER#user-123", "ACTIVE_BINDING")]["bindingFingerprint"],
            "fp-1",
        )
        transaction = fake_client.transactions[-1]
        self.assertEqual(transaction[0]["ConditionCheck"]["TableName"], "users")
        self.assertEqual(transaction[1]["ConditionCheck"]["TableName"], "deletion-ledger")
        self.assertEqual(
            _deserialize(transaction[1]["ConditionCheck"]["Key"]),
            {"PK": "ACCOUNT#user-123", "SK": "ACCOUNT_DELETION"},
        )

    def test_returns_known_for_same_active_device(self):
        fake_table.put_item(
            {
                "PK": "USER#user-123",
                "SK": "DEVICE#fp-1",
                "accountId": "user-123",
                "bindingFingerprint": "fp-1",
                "platform": "ios",
                "osVersion": "18.3",
                "status": "ACTIVE",
                "firstSeenAt": "2026-05-01T00:00:00+00:00",
                "lastSeenAt": "2026-05-01T00:00:00+00:00",
                "deactivatedAt": None,
                "GSI1PK": "USER#user-123#ACTIVE",
                "GSI1SK": "2026-05-01T00:00:00+00:00",
            }
        )

        result = service.register_device(
            account_id="user-123",
            binding_fingerprint="fp-1",
            platform="ios",
            os_version="18.4",
        )

        self.assertEqual(result["decision"], "KNOWN")
        item = fake_table.items[("USER#user-123", "DEVICE#fp-1")]
        self.assertEqual(item["status"], "ACTIVE")
        self.assertEqual(item["osVersion"], "18.4")

    def test_sdk_decimal_pointer_version_is_normalized_for_update(self):
        fake_table.put_item({
            "PK": "USER#user-123", "SK": "ACTIVE_BINDING",
            "recordType": "ACTIVE_BINDING_POINTER",
            "bindingFingerprint": "fp-1", "stateVersion": Decimal("1"),
        })
        fake_table.put_item({
            "PK": "USER#user-123", "SK": "DEVICE#fp-1",
            "accountId": "user-123", "bindingFingerprint": "fp-1",
            "platform": "ios", "osVersion": "18.3", "status": "ACTIVE",
            "firstSeenAt": "2026-05-01T00:00:00+00:00",
            "lastSeenAt": "2026-05-01T00:00:00+00:00",
            "deactivatedAt": None, "GSI1PK": "USER#user-123#ACTIVE",
            "GSI1SK": "2026-05-01T00:00:00+00:00",
        })

        result = service.register_device(
            account_id="user-123", binding_fingerprint="fp-1",
            platform="ios", os_version="18.4",
        )

        self.assertEqual(result["decision"], "KNOWN")
        self.assertEqual(
            fake_table.items[("USER#user-123", "ACTIVE_BINDING")]["stateVersion"],
            2,
        )

    def test_pointer_version_rejects_nonintegral_decimal(self):
        self.assertIsNone(service._positive_version(Decimal("1.5")))
        self.assertIsNone(service._positive_version(Decimal("NaN")))

    def test_returns_switch_for_different_active_device(self):
        fake_table.put_item(
            {
                "PK": "USER#user-123",
                "SK": "DEVICE#fp-1",
                "accountId": "user-123",
                "bindingFingerprint": "fp-1",
                "platform": "ios",
                "osVersion": "18.3",
                "status": "ACTIVE",
                "firstSeenAt": "2026-05-01T00:00:00+00:00",
                "lastSeenAt": "2026-05-01T00:00:00+00:00",
                "deactivatedAt": None,
                "GSI1PK": "USER#user-123#ACTIVE",
                "GSI1SK": "2026-05-01T00:00:00+00:00",
            }
        )

        result = service.register_device(
            account_id="user-123",
            binding_fingerprint="fp-2",
            platform="android",
            os_version="15",
        )

        self.assertEqual(result["decision"], "SWITCH")
        new_item = fake_table.items[("USER#user-123", "DEVICE#fp-2")]
        old_item = fake_table.items[("USER#user-123", "DEVICE#fp-1")]
        self.assertEqual(new_item["status"], "ACTIVE")
        self.assertEqual(old_item["status"], "INACTIVE")
        self.assertIn("expiresAt", old_item)

    def test_returns_switch_when_old_inactive_device_returns(self):
        fake_table.put_item(
            {
                "PK": "USER#user-123",
                "SK": "DEVICE#fp-1",
                "accountId": "user-123",
                "bindingFingerprint": "fp-1",
                "platform": "ios",
                "osVersion": "18.2",
                "status": "INACTIVE",
                "firstSeenAt": "2026-04-01T00:00:00+00:00",
                "lastSeenAt": "2026-04-02T00:00:00+00:00",
                "deactivatedAt": "2026-04-03T00:00:00+00:00",
                "GSI1PK": "USER#user-123#INACTIVE",
                "GSI1SK": "2026-04-03T00:00:00+00:00",
                "expiresAt": 9999999999,
            }
        )
        fake_table.put_item(
            {
                "PK": "USER#user-123",
                "SK": "DEVICE#fp-2",
                "accountId": "user-123",
                "bindingFingerprint": "fp-2",
                "platform": "android",
                "osVersion": "15",
                "status": "ACTIVE",
                "firstSeenAt": "2026-05-01T00:00:00+00:00",
                "lastSeenAt": "2026-05-01T00:00:00+00:00",
                "deactivatedAt": None,
                "GSI1PK": "USER#user-123#ACTIVE",
                "GSI1SK": "2026-05-01T00:00:00+00:00",
            }
        )

        result = service.register_device(
            account_id="user-123",
            binding_fingerprint="fp-1",
            platform="ios",
            os_version="18.4",
        )

        self.assertEqual(result["decision"], "SWITCH")
        reactivated = fake_table.items[("USER#user-123", "DEVICE#fp-1")]
        previous = fake_table.items[("USER#user-123", "DEVICE#fp-2")]
        self.assertEqual(reactivated["status"], "ACTIVE")
        self.assertIsNone(reactivated["deactivatedAt"])
        self.assertEqual(previous["status"], "INACTIVE")

    def test_deletion_race_cancellation_leaves_no_pointer_or_device(self):
        fake_client.cancel_next = True

        with self.assertRaises(service.AppError) as context:
            service.register_device(
                account_id="user-123", binding_fingerprint="fp-1",
                platform="ios", os_version="18.4",
            )

        self.assertEqual(context.exception.code, "CONFLICT")
        self.assertEqual(fake_table.items, {})


if __name__ == "__main__":
    unittest.main()
