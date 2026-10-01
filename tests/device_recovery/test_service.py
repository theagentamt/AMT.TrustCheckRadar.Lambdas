import importlib.util
import hashlib
import json
import sys
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "device_recovery"
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))


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
        result[key] = (
            raw if kind == "S" else int(raw) if kind == "N"
            else raw if kind == "BOOL" else None
        )
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
                if ":one" in values:
                    item = fake_table.items.setdefault(
                        (key["PK"], key["SK"]), {**key, "requestCount": 0}
                    )
                    item["requestCount"] += values[":one"]
                    item["expiresAt"] = values[":expires_at"]
                else:
                    item = fake_table.items[(key["PK"], key["SK"])]
                    item.update({
                        "bindingFingerprint": values.get(":target", values.get(":none")),
                        "stateVersion": values[":next"],
                        "updatedAt": values[":now"],
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


class DeviceRecoveryServiceTests(unittest.TestCase):
    def setUp(self):
        fake_table.items.clear()
        fake_client.transactions.clear()
        fake_client.cancel_next = False
        service.table = fake_table
        service.control_table = fake_table
        service.users_table = fake_table
        service.deletion_ledger_table = fake_table
        service.dynamodb_client = fake_client
        service.config.DEVICE_BINDINGS_TABLE_NAME = "device-bindings"
        service.config.DEVICE_RECOVERY_CONTROL_TABLE_NAME = "recovery-control"
        service.config.USERS_TABLE_NAME = "users"
        service.config.DELETION_LEDGER_TABLE_NAME = "deletion-ledger"
        service.config.DEVICE_RECOVERY_AUDIT_RETENTION_DAYS = 90
        fake_table.put_item({
            "PK": "USER#user-123", "SK": "PROFILE", "sub": "user-123",
            "status": "ACTIVE", "ageVerified": True,
        })

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
        self.assertEqual(
            fake_table.items[("USER#user-123", "ACTIVE_BINDING")]["bindingFingerprint"],
            "NONE",
        )

    def test_sdk_decimal_pointer_version_is_normalized_for_recovery(self):
        fake_table.put_item({
            "PK": "USER#user-123", "SK": "ACTIVE_BINDING",
            "recordType": "ACTIVE_BINDING_POINTER",
            "bindingFingerprint": "fp-1", "stateVersion": Decimal("1"),
        })
        fake_table.put_item({
            "PK": "USER#user-123", "SK": "DEVICE#fp-1",
            "accountId": "user-123", "bindingFingerprint": "fp-1",
            "platform": "ios", "osVersion": "18.4", "status": "ACTIVE",
            "firstSeenAt": "2026-05-01T00:00:00+00:00",
            "lastSeenAt": "2026-05-02T00:00:00+00:00",
            "deactivatedAt": None, "GSI1PK": "USER#user-123#ACTIVE",
            "GSI1SK": "2026-05-02T00:00:00+00:00",
        })

        result = service.process_recovery(
            account_id="user-123", action="RESET_ACTIVE_BINDING",
            binding_fingerprint=None, operator_id="support-1",
        )

        self.assertEqual(result["result"], "CLEARED")
        self.assertEqual(
            fake_table.items[("USER#user-123", "ACTIVE_BINDING")]["stateVersion"],
            2,
        )

    def test_pointer_version_rejects_invalid_decimal_values(self):
        for value in (Decimal("1.5"), Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(value=value):
                self.assertIsNone(service._positive_version(value))

    def test_reset_active_binding_returns_noop_when_no_active_exists(self):
        result = service.process_recovery(
            account_id="user-123",
            action="RESET_ACTIVE_BINDING",
            binding_fingerprint=None,
            operator_id="support-1",
        )

        self.assertEqual(result["result"], "NO_ACTIVE_BINDING")
        self.assertEqual(
            fake_table.items[("USER#user-123", "ACTIVE_BINDING")]["bindingFingerprint"],
            "NONE",
        )

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
        self.assertEqual(
            fake_table.items[("USER#user-123", "ACTIVE_BINDING")]["bindingFingerprint"],
            "fp-1",
        )

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

    def test_transaction_cancellation_does_not_mutate_binding_state(self):
        fake_table.put_item({
            "PK": "USER#user-123", "SK": "DEVICE#fp-1",
            "accountId": "user-123", "bindingFingerprint": "fp-1",
            "platform": "ios", "osVersion": "18.4", "status": "ACTIVE",
            "firstSeenAt": "2026-05-01T00:00:00+00:00",
            "lastSeenAt": "2026-05-02T00:00:00+00:00", "deactivatedAt": None,
            "GSI1PK": "USER#user-123#ACTIVE", "GSI1SK": "2026-05-02T00:00:00+00:00",
        })
        before = dict(fake_table.items[("USER#user-123", "DEVICE#fp-1")])
        fake_client.cancel_next = True

        with self.assertRaises(service.AppError) as context:
            service.process_recovery(
                account_id="user-123", action="RESET_ACTIVE_BINDING",
                binding_fingerprint=None, operator_id="support-1",
            )

        self.assertEqual(context.exception.code, "CONFLICT")
        self.assertEqual(fake_table.items[("USER#user-123", "DEVICE#fp-1")], before)
        self.assertNotIn(("USER#user-123", "ACTIVE_BINDING"), fake_table.items)

    def test_self_recovery_is_atomic_and_idempotent(self):
        payload = {
            "schemaVersion": 1,
            "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
            "action": "REPLACE_ACTIVE_BINDING",
            "bindingFingerprint": "fp-new",
            "platform": "ios",
            "osVersion": "18.4",
        }

        result = service.process_self_recovery(
            account_id="user-123", payload=payload, now_epoch=1_800_000_000
        )

        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(
            fake_table.items[("USER#user-123", "ACTIVE_BINDING")]["bindingFingerprint"],
            "fp-new",
        )
        transaction = fake_client.transactions[-1]
        self.assertEqual(transaction[0]["ConditionCheck"]["TableName"], "users")
        self.assertEqual(transaction[1]["ConditionCheck"]["TableName"], "deletion-ledger")
        self.assertEqual(
            _deserialize(transaction[1]["ConditionCheck"]["Key"]),
            {"PK": "ACCOUNT#user-123", "SK": "ACCOUNT_DELETION"},
        )
        self.assertEqual(
            transaction[1]["ConditionCheck"]["ConditionExpression"],
            "attribute_not_exists(PK)",
        )
        self.assertIn(
            ("USER#user-123", "RECOVERY#3fefbf1a-caf4-4e72-ab61-4fb36bf925b4"),
            fake_table.items,
        )
        self.assertIn(
            ("USER#user-123", "AUDIT#1800000000#3fefbf1a-caf4-4e72-ab61-4fb36bf925b4"),
            fake_table.items,
        )

        replay = service.process_self_recovery(
            account_id="user-123", payload=payload, now_epoch=1_800_000_001
        )
        self.assertEqual(replay, result)
        self.assertEqual(len(fake_client.transactions), 1)

    def test_self_recovery_operation_id_cannot_be_reused_for_another_payload(self):
        payload = {
            "schemaVersion": 1,
            "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
            "action": "REPLACE_ACTIVE_BINDING",
            "bindingFingerprint": "fp-new",
            "platform": "ios",
            "osVersion": "18.4",
        }
        service.process_self_recovery(
            account_id="user-123", payload=payload, now_epoch=1_800_000_000
        )

        with self.assertRaises(service.AppError) as context:
            service.process_self_recovery(
                account_id="user-123",
                payload={**payload, "bindingFingerprint": "fp-other"},
                now_epoch=1_800_000_001,
            )

        self.assertEqual(context.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_expired_present_receipt_fails_closed_instead_of_replaying_success(self):
        payload = {
            "schemaVersion": 1,
            "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
            "action": "REPLACE_ACTIVE_BINDING",
            "bindingFingerprint": "fp-new",
            "platform": "ios",
            "osVersion": "18.4",
        }
        started_at = 1_800_000_000
        service.process_self_recovery(
            account_id="user-123", payload=payload, now_epoch=started_at
        )
        transaction_count = len(fake_client.transactions)

        with self.assertRaises(service.AppError) as context:
            service.process_self_recovery(
                account_id="user-123",
                payload=payload,
                now_epoch=started_at + 7 * 86400,
            )

        self.assertEqual(context.exception.code, "SERVER_UNAVAILABLE")
        self.assertFalse(context.exception.retryable)
        self.assertEqual(len(fake_client.transactions), transaction_count)

    def test_receipt_replay_rejects_wrong_subject_key_and_invalid_shape(self):
        payload = {
            "schemaVersion": 1,
            "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
            "action": "REPLACE_ACTIVE_BINDING",
            "bindingFingerprint": "fp-new",
            "platform": "ios",
            "osVersion": "18.4",
        }
        started_at = 1_800_000_000
        service.process_self_recovery(
            account_id="user-123", payload=payload, now_epoch=started_at
        )
        key = ("USER#user-123", f"RECOVERY#{payload['operationId']}")
        valid = dict(fake_table.items[key])
        invalid_receipts = (
            valid | {"PK": "USER#another-subject"},
            valid | {"SK": "RECOVERY#47debb73-444b-4bb1-9889-fb56885b7922"},
            valid | {"schemaVersion": True},
            valid | {"operation": "RESET_ACTIVE_BINDING"},
            valid | {"bindingFingerprint": "fp-other"},
            valid | {"completedAtEpoch": Decimal("1800000000.5")},
            valid | {"expiresAt": valid["expiresAt"] + 1},
            valid | {"unexpected": "field"},
        )
        for receipt in invalid_receipts:
            with self.subTest(receipt=receipt):
                fake_table.items[key] = receipt
                with self.assertRaises(service.AppError) as context:
                    service.process_self_recovery(
                        account_id="user-123",
                        payload=payload,
                        now_epoch=started_at + 1,
                    )
                self.assertEqual(context.exception.code, "SERVER_UNAVAILABLE")
        fake_table.items[key] = valid

    def test_transaction_cancel_replay_rejects_tampered_valid_fingerprint_without_recovery_writes(self):
        payload = {
            "schemaVersion": 1,
            "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
            "action": "REPLACE_ACTIVE_BINDING",
            "bindingFingerprint": "fp-requested",
            "platform": "ios",
            "osVersion": "18.4",
        }
        now_epoch = 1_800_000_000
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        tampered = {
            "PK": "USER#user-123",
            "SK": f"RECOVERY#{payload['operationId']}",
            "recordType": "DEVICE_RECOVERY_RECEIPT",
            "schemaVersion": 1,
            "operationId": payload["operationId"],
            "operation": "REPLACE_ACTIVE_BINDING",
            "payloadHash": payload_hash,
            "result": "RECOVERED",
            "status": "COMPLETE",
            "bindingFingerprint": "fp-other-valid",
            "completedAtEpoch": now_epoch,
            "expiresAt": now_epoch + 7 * 86400,
        }

        def cancel_with_concurrent_tampered_receipt(**_kwargs):
            fake_table.put_item(tampered)
            error = RuntimeError("cancelled")
            error.response = {"Error": {"Code": "TransactionCanceledException"}}
            raise error

        with mock.patch.object(
            service.dynamodb_client,
            "transact_write_items",
            side_effect=cancel_with_concurrent_tampered_receipt,
        ):
            with self.assertRaises(service.AppError) as context:
                service.process_self_recovery(
                    account_id="user-123", payload=payload, now_epoch=now_epoch
                )

        self.assertEqual(context.exception.code, "SERVER_UNAVAILABLE")
        self.assertNotIn(("USER#user-123", "ACTIVE_BINDING"), fake_table.items)
        self.assertNotIn(("USER#user-123", "DEVICE#fp-requested"), fake_table.items)
        self.assertFalse(any(
            pk == "USER#user-123" and sk.startswith(("RATE#", "AUDIT#"))
            for pk, sk in fake_table.items
        ))

    def test_replay_is_historical_outcome_not_current_binding_status(self):
        payload = {
            "schemaVersion": 1,
            "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
            "action": "REPLACE_ACTIVE_BINDING",
            "bindingFingerprint": "fp-original",
            "platform": "ios",
            "osVersion": "18.4",
        }
        started_at = 1_800_000_000
        original = service.process_self_recovery(
            account_id="user-123", payload=payload, now_epoch=started_at
        )
        transaction_count = len(fake_client.transactions)
        fake_table.items[("USER#user-123", "ACTIVE_BINDING")]["bindingFingerprint"] = "fp-later"
        fake_table.put_item({
            "PK": "USER#user-123", "SK": "DEVICE#fp-later",
            "accountId": "user-123", "bindingFingerprint": "fp-later",
            "platform": "ios", "osVersion": "18.5", "status": "ACTIVE",
            "firstSeenAt": "2027-01-15T08:00:00+00:00",
            "lastSeenAt": "2027-01-15T08:00:00+00:00", "deactivatedAt": None,
        })

        replay = service.process_self_recovery(
            account_id="user-123", payload=payload, now_epoch=started_at + 1
        )

        self.assertEqual(replay, original)
        self.assertEqual(replay["bindingFingerprint"], "fp-original")
        self.assertNotIn("currentBinding", replay)
        self.assertEqual(len(fake_client.transactions), transaction_count)

    def test_removed_receipt_has_no_reuse_evidence_pending_owner_decision(self):
        payload = {
            "schemaVersion": 1,
            "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
            "action": "REPLACE_ACTIVE_BINDING",
            "bindingFingerprint": "fp-new",
            "platform": "ios",
            "osVersion": "18.4",
        }
        started_at = 1_800_000_000
        service.process_self_recovery(
            account_id="user-123", payload=payload, now_epoch=started_at
        )
        del fake_table.items[("USER#user-123", f"RECOVERY#{payload['operationId']}")]
        transaction_count = len(fake_client.transactions)

        service.process_self_recovery(
            account_id="user-123", payload=payload, now_epoch=started_at + 1
        )

        self.assertEqual(len(fake_client.transactions), transaction_count + 1)

    def test_deletion_fence_blocks_late_replay_before_receipt_read_can_succeed(self):
        payload = {
            "schemaVersion": 1,
            "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
            "action": "REPLACE_ACTIVE_BINDING",
            "bindingFingerprint": "fp-new",
            "platform": "ios",
            "osVersion": "18.4",
        }
        service.process_self_recovery(
            account_id="user-123", payload=payload, now_epoch=1_800_000_000
        )
        transaction_count = len(fake_client.transactions)
        fake_table.put_item({
            "PK": "ACCOUNT#user-123", "SK": "ACCOUNT_DELETION",
            "operationId": "47debb73-444b-4bb1-9889-fb56885b7922",
        })

        with self.assertRaises(service.AppError) as context:
            service.process_self_recovery(
                account_id="user-123", payload=payload, now_epoch=1_800_000_001
            )

        self.assertEqual(context.exception.code, "FORBIDDEN")
        self.assertEqual(len(fake_client.transactions), transaction_count)


if __name__ == "__main__":
    unittest.main()
