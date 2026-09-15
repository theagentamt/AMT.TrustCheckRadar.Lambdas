import importlib.util
import sys
import unittest
from pathlib import Path


MODULE = Path(__file__).resolve().parents[2] / "src" / "account_data_api"
sys.path.insert(0, str(MODULE))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("errors", MODULE / "errors.py")
service = _load("account_data_service_test", MODULE / "service.py")


class Table:
    def __init__(self, items=None):
        self.items = items or {}
        self.puts = []

    def get_item(self, Key, **_kwargs):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}

    def put_item(self, Item, **_kwargs):
        self.puts.append(Item)
        self.items[(Item["PK"], Item["SK"])] = dict(Item)

    def delete_item(self, Key):
        self.items.pop((Key["PK"], Key["SK"]), None)


class ScanTable(Table):
    def __init__(self, pages, items=None):
        super().__init__(items)
        self.pages = list(pages)
        self.scans = []

    def scan(self, **kwargs):
        self.scans.append(kwargs)
        return self.pages.pop(0)


class DeviceTable:
    def __init__(self, pages):
        self.pages = list(pages)
        self.queries = []
        self.deletes = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return self.pages.pop(0)

    def delete_item(self, Key):
        self.deletes.append(Key)


class RecoveryTable(Table):
    def __init__(self, pages, items=None):
        super().__init__(items)
        self.pages = list(pages)
        self.queries = []
        self.deletes = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return self.pages.pop(0)

    def delete_item(self, Key):
        self.deletes.append(Key)
        super().delete_item(Key)


def _deserialize(item):
    result = {}
    for key, value in item.items():
        kind, raw = next(iter(value.items()))
        result[key] = raw if kind == "S" else int(raw) if kind == "N" else raw
    return result


class Client:
    def __init__(self, ledger):
        self.ledger = ledger
        self.transactions = []
        self.cancel = False

    def transact_write_items(self, TransactItems):
        self.transactions.append(TransactItems)
        if self.cancel:
            error = RuntimeError("cancelled")
            error.response = {"Error": {"Code": "TransactionCanceledException"}}
            raise error
        command = _deserialize(TransactItems[1]["Put"]["Item"])
        self.ledger.items[(command["PK"], command["SK"])] = command


def command():
    return {
        "PK": "ACCOUNT#account-1", "SK": "ACCOUNT_DELETION",
        "schemaVersion": 1, "recordVersion": 1, "environment": "dev",
        "eventType": "account.deletion.requested", "accountId": "account-1",
        "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
        "status": "REQUESTED", "occurredAtEpoch": 100,
        "deleteByEpoch": 86_500,
    }


class AccountDeletionServiceTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Table()
        self.client = Client(self.ledger)
        self.subject = service.AccountDeletionService(
            environment="dev", ledger_table=self.ledger,
            users_table_name="users", ledger_table_name="ledger",
            dynamodb_client=self.client,
            required_components=("SESSION_REVOCATION", "HISTORY", "CAMPAIGN"),
            now=lambda: 100,
        )

    def test_request_atomically_fences_profile_and_writes_exact_command(self):
        result = self.subject.request(
            "account-1", "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4"
        )

        self.assertEqual(result["status"], "REQUESTED")
        self.assertFalse(result["completionEligible"])
        transaction = self.client.transactions[0]
        self.assertEqual(len(transaction), 2)
        profile = transaction[0]["Update"]
        self.assertEqual(profile["TableName"], "users")
        self.assertIn("#status = :active", profile["ConditionExpression"])
        self.assertIn("ageVerified = :true", profile["ConditionExpression"])
        stored = self.ledger.items[("ACCOUNT#account-1", "ACCOUNT_DELETION")]
        self.assertEqual(set(stored), service.COMMAND_FIELDS)
        self.assertEqual(stored["deleteByEpoch"], 86_500)

    def test_same_operation_replays_and_different_operation_conflicts(self):
        self.ledger.items[("ACCOUNT#account-1", "ACCOUNT_DELETION")] = command()

        replay = self.subject.request(
            "account-1", "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4"
        )
        self.assertEqual(replay["operationId"], command()["operationId"])
        self.assertEqual(self.client.transactions, [])

        with self.assertRaises(service.AppError) as raised:
            self.subject.request(
                "account-1", "47debb73-444b-4bb1-9889-fb56885b7922"
            )
        self.assertEqual(raised.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_status_accepts_only_receipts_bound_to_same_request(self):
        self.ledger.items[("ACCOUNT#account-1", "ACCOUNT_DELETION")] = command()
        self.ledger.items[("ACCOUNT#account-1", "ACCOUNT_DELETION#HISTORY")] = {
            "PK": "ACCOUNT#account-1", "SK": "ACCOUNT_DELETION#HISTORY",
            "schemaVersion": 1, "recordVersion": 1, "environment": "dev",
            "eventType": "account.deletion.component.completed",
            "component": "HISTORY", "status": "COMPLETE",
            "operationId": command()["operationId"],
            "occurredAtEpoch": 110, "requestOccurredAtEpoch": 100,
            "retainUntilEpoch": 110 + 120 * 86400,
        }
        self.ledger.items[("ACCOUNT#account-1", "ACCOUNT_DELETION#CAMPAIGN")] = {
            "PK": "ACCOUNT#account-1", "SK": "ACCOUNT_DELETION#CAMPAIGN",
            "schemaVersion": 1, "recordVersion": 1, "environment": "dev",
            "eventType": "account.deletion.component.completed",
            "component": "CAMPAIGN", "status": "COMPLETE",
            "operationId": "47debb73-444b-4bb1-9889-fb56885b7922",
            "occurredAtEpoch": 111, "requestOccurredAtEpoch": 100,
            "retainUntilEpoch": 111 + 120 * 86400,
        }

        result = self.subject.status("account-1")

        self.assertEqual(
            result["components"],
            [
                {"component": "SESSION_REVOCATION", "status": "PENDING"},
                {"component": "HISTORY", "status": "COMPLETE"},
                {"component": "CAMPAIGN", "status": "PENDING"},
            ],
        )
        self.assertFalse(result["completionEligible"])

    def test_session_revocation_writes_idempotent_component_receipt(self):
        class Cognito:
            def __init__(self):
                self.calls = []
            def admin_user_global_sign_out(self, **kwargs):
                self.calls.append(kwargs)

        cognito = Cognito()

        self.assertTrue(service.ensure_session_revoked(
            command(), user_pool_id="pool", cognito=cognito, ledger_table=self.ledger
        ))

        self.assertEqual(cognito.calls, [{"UserPoolId": "pool", "Username": "account-1"}])
        receipt = self.ledger.items[("ACCOUNT#account-1", "ACCOUNT_DELETION#SESSION_REVOCATION")]
        self.assertEqual(receipt["operationId"], command()["operationId"])
        self.assertTrue(service.ensure_session_revoked(
            command(), user_pool_id="pool", cognito=cognito, ledger_table=self.ledger
        ))
        self.assertEqual(len(cognito.calls), 1)

    def test_stream_parser_rejects_noncanonical_or_extra_commands(self):
        def av(value):
            return {"N": str(value)} if isinstance(value, int) else {"S": value}
        record = {
            "eventName": "INSERT",
            "dynamodb": {"NewImage": {key: av(value) for key, value in command().items()}},
        }
        self.assertEqual(
            service.command_from_stream(record, environment="dev"), command()
        )
        invalid = command() | {"unexpected": "value"}
        record["dynamodb"]["NewImage"] = {key: av(value) for key, value in invalid.items()}
        with self.assertRaises(ValueError):
            service.command_from_stream(record, environment="dev")

    def test_bounded_reconciliation_recovers_session_revocation_after_stream_window(self):
        ledger = ScanTable([{"Items": [command()], "ScannedCount": 1}])
        devices = DeviceTable([{"Items": []}])
        recovery = RecoveryTable([{"Items": []}])

        class Cognito:
            def __init__(self):
                self.calls = []
            def admin_user_global_sign_out(self, **kwargs):
                self.calls.append(kwargs)

        cognito = Cognito()
        result = service.reconcile_session_revocations(
            environment="dev", ledger_table=ledger, device_table=devices,
            recovery_table=recovery,
            user_pool_id="pool",
            cognito=cognito, scan_limit=100, max_pages=1, now=lambda: 200,
        )

        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["revoked"], 1)
        self.assertEqual(result["deviceComponentsCompleted"], 1)
        self.assertTrue(result["completedFullPass"])
        self.assertEqual(result["fullPassAgeSeconds"], 0)
        self.assertEqual(cognito.calls[0]["Username"], "account-1")
        checkpoint = ledger.items[(
            "LIFECYCLE#dev",
            "ACCOUNT_DELETION_SESSION_REVOCATION_RECONCILIATION",
        )]
        self.assertEqual(checkpoint["completedPassAtEpoch"], 200)

    def test_device_cleanup_is_bounded_resumable_and_receipted(self):
        ledger = Table()
        continuation = {"PK": "USER#account-1", "SK": "DEVICE#one"}
        devices = DeviceTable([
            {
                "Items": [{"PK": "USER#account-1", "SK": "DEVICE#one"}],
                "LastEvaluatedKey": continuation,
            },
            {"Items": [{"PK": "USER#account-1", "SK": "ACTIVE_BINDING"}]},
        ])

        first = service.delete_device_bindings(
            command(), device_table=devices, ledger_table=ledger,
            page_size=100, now_epoch=200,
        )
        second = service.delete_device_bindings(
            command(), device_table=devices, ledger_table=ledger,
            page_size=100, now_epoch=201,
        )

        self.assertEqual(first, {"deleted": 1, "complete": False, "alreadyComplete": False})
        self.assertEqual(second, {"deleted": 1, "complete": True, "alreadyComplete": False})
        self.assertEqual(devices.queries[1]["ExclusiveStartKey"], continuation)
        self.assertEqual(
            devices.deletes,
            [
                {"PK": "USER#account-1", "SK": "DEVICE#one"},
                {"PK": "USER#account-1", "SK": "ACTIVE_BINDING"},
            ],
        )
        receipt = ledger.items[("ACCOUNT#account-1", "ACCOUNT_DELETION#DEVICE_BINDINGS")]
        self.assertEqual(receipt["operationId"], command()["operationId"])
        self.assertEqual(receipt["retainUntilEpoch"], 201 + 120 * 86400)

    def test_recovery_cleanup_is_bounded_minimizes_security_evidence_and_receipts(self):
        operation_id = command()["operationId"]
        receipt_key = ("USER#account-1", f"RECOVERY#{operation_id}")
        audit_key = ("USER#account-1", f"AUDIT#0000000200#{operation_id}")
        rate_key = ("USER#account-1", "RATE#0")
        receipt = {
            "PK": receipt_key[0], "SK": receipt_key[1],
            "recordType": "DEVICE_RECOVERY_RECEIPT", "schemaVersion": 1,
            "operationId": operation_id, "operation": "REPLACE_ACTIVE_BINDING",
            "payloadHash": "a" * 64, "result": "RECOVERED", "status": "COMPLETE",
            "bindingFingerprint": "sensitive-fingerprint", "completedAtEpoch": 100,
            "expiresAt": 100 + 7 * 86400,
        }
        audit = {
            "PK": audit_key[0], "SK": audit_key[1],
            "recordType": "DEVICE_RECOVERY_AUDIT", "schemaVersion": 1,
            "operationId": operation_id, "actorType": "SELF",
            "action": "REPLACE_ACTIVE_BINDING", "result": "RECOVERED",
            "bindingFingerprint": "sensitive-fingerprint", "occurredAtEpoch": 200,
            "expiresAt": 200 + 90 * 86400,
        }
        rate = {
            "PK": rate_key[0], "SK": rate_key[1],
            "requestCount": 1, "expiresAt": 24 * 3600,
        }
        continuation = {"PK": receipt["PK"], "SK": receipt["SK"]}
        recovery = RecoveryTable(
            [
                {"Items": [receipt, rate], "LastEvaluatedKey": continuation},
                {"Items": [audit]},
            ],
            {receipt_key: receipt, audit_key: audit, rate_key: rate},
        )
        ledger = Table()

        first = service.delete_device_recovery_control(
            command(), recovery_table=recovery, ledger_table=ledger,
            page_size=100, now_epoch=300,
        )
        second = service.delete_device_recovery_control(
            command(), recovery_table=recovery, ledger_table=ledger,
            page_size=100, now_epoch=301,
        )

        self.assertEqual(
            first,
            {"deleted": 1, "minimized": 1, "complete": False,
             "alreadyComplete": False},
        )
        self.assertEqual(
            second,
            {"deleted": 0, "minimized": 1, "complete": True,
             "alreadyComplete": False},
        )
        self.assertEqual(recovery.queries[1]["ExclusiveStartKey"], continuation)
        self.assertNotIn(rate_key, recovery.items)
        self.assertNotIn("payloadHash", recovery.items[receipt_key])
        self.assertNotIn("bindingFingerprint", recovery.items[receipt_key])
        self.assertNotIn("bindingFingerprint", recovery.items[audit_key])
        component = ledger.items[(
            "ACCOUNT#account-1", "ACCOUNT_DELETION#DEVICE_RECOVERY"
        )]
        self.assertEqual(component["operationId"], operation_id)
        self.assertEqual(component["requestOccurredAtEpoch"], 100)
        self.assertEqual(component["retainUntilEpoch"], 301 + 120 * 86400)

        replay = service.delete_device_recovery_control(
            command(), recovery_table=recovery, ledger_table=ledger,
            page_size=100, now_epoch=302,
        )
        self.assertTrue(replay["alreadyComplete"])
        self.assertEqual(len(recovery.queries), 2)

    def test_recovery_cleanup_rejects_unknown_family_without_receipt(self):
        recovery = RecoveryTable([{"Items": [{
            "PK": "USER#account-1", "SK": "UNKNOWN#late-write",
        }]}])
        ledger = Table()

        with self.assertRaisesRegex(ValueError, "Unknown device-recovery"):
            service.delete_device_recovery_control(
                command(), recovery_table=recovery, ledger_table=ledger,
                page_size=100, now_epoch=300,
            )

        self.assertNotIn(
            ("ACCOUNT#account-1", "ACCOUNT_DELETION#DEVICE_RECOVERY"),
            ledger.items,
        )


if __name__ == "__main__":
    unittest.main()
