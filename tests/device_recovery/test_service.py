import importlib.util
import sys
import types
import unittest
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "device_recovery"
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

    def query(self, IndexName=None, KeyConditionExpression=None, ExpressionAttributeValues=None, Limit=None, ScanIndexForward=None):
        gsi1pk = ExpressionAttributeValues[":gsi1pk"]
        matches = [item for item in self.items.values() if item.get("GSI1PK") == gsi1pk]
        matches.sort(key=lambda item: item.get("GSI1SK", ""), reverse=not ScanIndexForward)
        if Limit:
            matches = matches[:Limit]
        return {"Items": matches}


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
service = _load_module("service", MODULE_DIR / "service.py")


class DeviceRecoveryServiceTests(unittest.TestCase):
    def setUp(self):
        fake_table.items.clear()
        service.table = fake_table

    def test_reset_active_binding_clears_current_active(self):
        fake_table.put_item(
            {
                "PK": "USER#user-123",
                "SK": "DEVICE#fp-1",
                "accountId": "user-123",
                "bindingFingerprint": "fp-1",
                "platform": "ios",
                "osVersion": "18.4",
                "status": "ACTIVE",
                "firstSeenAt": "2026-05-01T00:00:00+00:00",
                "lastSeenAt": "2026-05-02T00:00:00+00:00",
                "deactivatedAt": None,
                "GSI1PK": "USER#user-123#ACTIVE",
                "GSI1SK": "2026-05-02T00:00:00+00:00",
            }
        )

        result = service.process_recovery(
            account_id="user-123",
            action="RESET_ACTIVE_BINDING",
            binding_fingerprint=None,
            operator_id="support-1",
        )

        self.assertEqual(result["result"], "CLEARED")
        item = fake_table.items[("USER#user-123", "DEVICE#fp-1")]
        self.assertEqual(item["status"], "INACTIVE")
        self.assertIn("expiresAt", item)

    def test_reset_active_binding_returns_noop_when_no_active_exists(self):
        result = service.process_recovery(
            account_id="user-123",
            action="RESET_ACTIVE_BINDING",
            binding_fingerprint=None,
            operator_id="support-1",
        )

        self.assertEqual(result["result"], "NO_ACTIVE_BINDING")

    def test_recover_binding_activates_existing_target_and_deactivates_current(self):
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

        result = service.process_recovery(
            account_id="user-123",
            action="RECOVER_BINDING",
            binding_fingerprint="fp-1",
            operator_id="support-1",
        )

        self.assertEqual(result["result"], "RECOVERED")
        recovered = fake_table.items[("USER#user-123", "DEVICE#fp-1")]
        previous = fake_table.items[("USER#user-123", "DEVICE#fp-2")]
        self.assertEqual(recovered["status"], "ACTIVE")
        self.assertIsNone(recovered["deactivatedAt"])
        self.assertEqual(previous["status"], "INACTIVE")

    def test_recover_binding_returns_already_active_when_target_is_current_active(self):
        fake_table.put_item(
            {
                "PK": "USER#user-123",
                "SK": "DEVICE#fp-1",
                "accountId": "user-123",
                "bindingFingerprint": "fp-1",
                "platform": "ios",
                "osVersion": "18.4",
                "status": "ACTIVE",
                "firstSeenAt": "2026-05-01T00:00:00+00:00",
                "lastSeenAt": "2026-05-02T00:00:00+00:00",
                "deactivatedAt": None,
                "GSI1PK": "USER#user-123#ACTIVE",
                "GSI1SK": "2026-05-02T00:00:00+00:00",
            }
        )

        result = service.process_recovery(
            account_id="user-123",
            action="RECOVER_BINDING",
            binding_fingerprint="fp-1",
            operator_id="support-1",
        )

        self.assertEqual(result["result"], "ALREADY_ACTIVE")


if __name__ == "__main__":
    unittest.main()
