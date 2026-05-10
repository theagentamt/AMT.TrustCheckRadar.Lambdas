import sys
import types
import unittest
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "conversation_analysis"
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))


class FakeTable:
    def __init__(self):
        self.items = {}

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


if __name__ == "__main__":
    unittest.main()
