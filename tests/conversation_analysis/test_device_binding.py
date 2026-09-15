import sys
import types
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
MODULE_DIR = SRC_DIR / "conversation_analysis"
for path in (SRC_DIR, MODULE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


class FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key, **_kwargs):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}

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
boto3_stub.client = lambda *args, **kwargs: object()
sys.modules.setdefault("boto3", boto3_stub)

import device_binding  # noqa: E402
from errors import AppError  # noqa: E402


class ConversationAnalysisDeviceBindingTests(unittest.TestCase):
    def setUp(self):
        fake_table.items.clear()
        device_binding.table = fake_table

    def test_accepts_matching_active_binding(self):
        fake_table.items[("USER#user-123", "ACTIVE_BINDING")] = {
            "PK": "USER#user-123", "SK": "ACTIVE_BINDING",
            "recordType": "ACTIVE_BINDING_POINTER", "schemaVersion": 1,
            "bindingFingerprint": "fp-1", "stateVersion": 3,
        }
        fake_table.items[("USER#user-123", "DEVICE#fp-1")] = {
            "PK": "USER#user-123",
            "SK": "DEVICE#fp-1",
            "accountId": "user-123",
            "bindingFingerprint": "fp-1",
            "status": "ACTIVE",
            "GSI1PK": "USER#user-123#ACTIVE",
            "GSI1SK": "2026-05-10T00:00:00+00:00",
        }
        event = {"headers": {"X-Device-Binding-Fingerprint": "fp-1"}}

        device_binding.assert_active_device_binding(event, "user-123")

    def test_rejects_missing_header(self):
        with self.assertRaises(AppError) as context:
            device_binding.assert_active_device_binding({}, "user-123")

        self.assertEqual(context.exception.code, "DEVICE_BINDING_REQUIRED")

    def test_rejects_mismatched_binding(self):
        fake_table.items[("USER#user-123", "ACTIVE_BINDING")] = {
            "PK": "USER#user-123", "SK": "ACTIVE_BINDING",
            "recordType": "ACTIVE_BINDING_POINTER", "schemaVersion": 1,
            "bindingFingerprint": "fp-2", "stateVersion": 2,
        }
        fake_table.items[("USER#user-123", "DEVICE#fp-2")] = {
            "PK": "USER#user-123",
            "SK": "DEVICE#fp-2",
            "accountId": "user-123",
            "bindingFingerprint": "fp-2",
            "status": "ACTIVE",
            "GSI1PK": "USER#user-123#ACTIVE",
            "GSI1SK": "2026-05-10T00:00:00+00:00",
        }
        event = {"headers": {"X-Device-Binding-Fingerprint": "fp-1"}}

        with self.assertRaises(AppError) as context:
            device_binding.assert_active_device_binding(event, "user-123")

        self.assertEqual(context.exception.code, "DEVICE_BINDING_MISMATCH")

    def test_none_pointer_revokes_even_when_gsi_still_reports_active(self):
        fake_table.items[("USER#user-123", "ACTIVE_BINDING")] = {
            "PK": "USER#user-123", "SK": "ACTIVE_BINDING",
            "recordType": "ACTIVE_BINDING_POINTER", "schemaVersion": 1,
            "bindingFingerprint": "NONE", "stateVersion": 4,
        }
        fake_table.items[("USER#user-123", "DEVICE#fp-1")] = {
            "PK": "USER#user-123", "SK": "DEVICE#fp-1",
            "accountId": "user-123", "bindingFingerprint": "fp-1",
            "status": "ACTIVE", "GSI1PK": "USER#user-123#ACTIVE",
            "GSI1SK": "2026-05-10T00:00:00+00:00",
        }
        event = {"headers": {"X-Device-Binding-Fingerprint": "fp-1"}}

        with self.assertRaises(AppError) as context:
            device_binding.assert_active_device_binding(event, "user-123")

        self.assertEqual(context.exception.code, "DEVICE_BINDING_MISMATCH")

    def test_rejects_inconsistent_pointer_target(self):
        fake_table.items[("USER#user-123", "ACTIVE_BINDING")] = {
            "PK": "USER#user-123", "SK": "ACTIVE_BINDING",
            "recordType": "ACTIVE_BINDING_POINTER", "schemaVersion": 1,
            "bindingFingerprint": "fp-1", "stateVersion": 1,
        }
        fake_table.items[("USER#user-123", "DEVICE#fp-1")] = {
            "PK": "USER#user-123", "SK": "DEVICE#fp-1",
            "accountId": "user-123", "bindingFingerprint": "fp-1",
            "status": "INACTIVE",
        }
        event = {"headers": {"X-Device-Binding-Fingerprint": "fp-1"}}

        with self.assertRaises(AppError) as context:
            device_binding.assert_active_device_binding(event, "user-123")

        self.assertEqual(context.exception.code, "SERVER_UNAVAILABLE")

    def test_legacy_fallback_rejects_multiple_active_bindings(self):
        for fingerprint, timestamp in (("fp-1", "2026-05-10T00:00:00+00:00"), ("fp-2", "2026-05-11T00:00:00+00:00")):
            fake_table.items[("USER#user-123", f"DEVICE#{fingerprint}")] = {
                "PK": "USER#user-123", "SK": f"DEVICE#{fingerprint}",
                "accountId": "user-123", "bindingFingerprint": fingerprint,
                "status": "ACTIVE", "GSI1PK": "USER#user-123#ACTIVE",
                "GSI1SK": timestamp,
            }
        event = {"headers": {"X-Device-Binding-Fingerprint": "fp-2"}}

        with self.assertRaises(AppError) as context:
            device_binding.assert_active_device_binding(event, "user-123")

        self.assertEqual(context.exception.code, "DEVICE_BINDING_MISMATCH")


if __name__ == "__main__":
    unittest.main()
