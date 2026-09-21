import sys
import unittest
from pathlib import Path


MODULE = Path(__file__).resolve().parents[2] / "src" / "history_account_deletion_bridge"
sys.path.insert(0, str(MODULE))
sys.path.insert(0, str(MODULE.parent))
sys.modules.pop("service", None)
import service


def command():
    return {
        "PK": "ACCOUNT#a", "SK": "ACCOUNT_DELETION",
        "schemaVersion": 1, "recordVersion": 1, "environment": "dev",
        "eventType": "account.deletion.requested", "accountId": "a",
        "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
        "status": "REQUESTED", "occurredAtEpoch": 100,
        "deleteByEpoch": 86_500,
    }


def stream(item=None):
    item = command() if item is None else item
    return {
        "eventName": "INSERT",
        "dynamodb": {"NewImage": {
            key: ({"N": str(value)} if isinstance(value, int) else {"S": value})
            for key, value in item.items()
        }},
    }


class Table:
    def __init__(self, items=None):
        self.items = items or {}
        self.puts = []

    def get_item(self, Key, **_kwargs):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}

    def put_item(self, Item, **_kwargs):
        self.puts.append(Item)


class Client:
    def __init__(self):
        self.transactions = []

    def transact_write_items(self, **kwargs):
        self.transactions.append(kwargs["TransactItems"])


class ScanLedger(Table):
    def __init__(self, pages):
        super().__init__({("ACCOUNT#a", "ACCOUNT_DELETION"): command()})
        self.pages = list(pages)
        self.scans = []

    def scan(self, **kwargs):
        self.scans.append(kwargs)
        return self.pages.pop(0)


class HistoryAccountDeletionBridgeTests(unittest.TestCase):
    def test_parses_only_exact_authoritative_fixed_fence(self):
        parsed = service.parse_account_deletion_record(
            stream(), environment="dev", schema_version=1
        )
        self.assertEqual(parsed, command())
        for changes in ({"SK": "other"}, {"environment": "prod"}, {"extra": "bad"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                service.parse_account_deletion_record(
                    stream(command() | changes), environment="dev", schema_version=1
                )

    def test_stream_command_atomically_fences_history_and_creates_all_generation_job(self):
        control = Table({
            ("USER#a", "STATE"): {
                "accountStatus": "ACTIVE", "historyGeneration": 3,
                "recognitionGeneration": 2, "acceptedSequence": 9,
            },
        })
        client = Client()
        result = service.start_history_deletion(
            command(), control_table=control, control_table_name="control",
            deletion_ledger_table=Table({("ACCOUNT#a", "ACCOUNT_DELETION"): command()}), deletion_ledger_table_name="ledger",
            dynamodb_client=client, schema_version=1, erasure_sla_hours=24,
        )
        self.assertTrue(result["started"])
        transaction = client.transactions[0]
        self.assertEqual(len(transaction), 3)
        self.assertIn("DELETING", repr(transaction))
        self.assertIn("maxHistoryGeneration", repr(transaction))
        self.assertIn("'N': '3'", repr(transaction))
        self.assertIn("ACCOUNT_DELETION", repr(transaction))

    def test_missing_history_state_writes_component_completion_receipt(self):
        ledger = Table({("ACCOUNT#a", "ACCOUNT_DELETION"): command()})
        client = Client()
        result = service.start_history_deletion(
            command(), control_table=Table(), control_table_name="control",
            deletion_ledger_table=ledger, deletion_ledger_table_name="ledger",
            dynamodb_client=client, schema_version=1, erasure_sla_hours=24, now_epoch=100,
        )
        self.assertTrue(result["completed"])
        receipt = service._deserialize(client.transactions[0][-1]["Put"]["Item"])
        self.assertFalse(ledger.puts)
        self.assertEqual(receipt["eventType"], "account.deletion.component.completed")
        self.assertEqual(
            receipt["retainUntilEpoch"],
            command()["occurredAtEpoch"] + 120 * 86400,
        )
        self.assertEqual(receipt["component"], "HISTORY")
        self.assertEqual(
            receipt["operationId"],
            "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
        )

    def test_bounded_reconciliation_recovers_stream_records_after_retention(self):
        ledger = ScanLedger([{
            "Items": [command()], "ScannedCount": 100,
            "LastEvaluatedKey": {"PK": "ACCOUNT#a", "SK": "ACCOUNT_DELETION"},
        }])
        control = Table()
        result = service.reconcile_account_deletions(
            environment="dev", schema_version=1,
            control_table=control, control_table_name="control",
            deletion_ledger_table=ledger, deletion_ledger_table_name="ledger",
            dynamodb_client=Client(), erasure_sla_hours=24,
            scan_limit=100, max_pages=1, now=lambda: 200,
        )
        self.assertEqual(result["scanned"], 100)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["completed"], 1)
        self.assertTrue(result["worksetTruncated"])
        self.assertFalse(result["completedFullPass"])
        self.assertIsNone(result["fullPassAgeSeconds"])
        checkpoint = next(
            item for item in control.puts
            if item["SK"] == "ACCOUNT_DELETION_RECONCILIATION"
        )
        self.assertEqual(
            checkpoint["lastEvaluatedKey"],
            {"PK": "ACCOUNT#a", "SK": "ACCOUNT_DELETION"},
        )

    def test_full_reconciliation_pass_records_zero_age_success_heartbeat(self):
        ledger = ScanLedger([{"Items": [], "ScannedCount": 0}])
        control = Table()

        result = service.reconcile_account_deletions(
            environment="dev", schema_version=1,
            control_table=control, control_table_name="control",
            deletion_ledger_table=ledger, deletion_ledger_table_name="ledger",
            dynamodb_client=Client(), erasure_sla_hours=24,
            scan_limit=100, max_pages=1, now=lambda: 300,
        )

        self.assertTrue(result["completedFullPass"])
        self.assertEqual(result["completedPassAtEpoch"], 300)
        self.assertEqual(result["fullPassAgeSeconds"], 0)
        checkpoint = control.puts[-1]
        self.assertEqual(checkpoint["completedPassAtEpoch"], 300)


if __name__ == "__main__":
    unittest.main()
