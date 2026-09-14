import sys
import unittest
from pathlib import Path


MODULE = Path(__file__).resolve().parents[2] / "src" / "history_account_deletion_bridge"
sys.path.insert(0, str(MODULE))
sys.modules.pop("service", None)
import service


def command():
    return {
        "PK": "ACCOUNT#a", "SK": "ACCOUNT_DELETION",
        "schemaVersion": 1, "recordVersion": 1, "environment": "dev",
        "eventType": "account.deletion.requested", "accountId": "a",
        "status": "REQUESTED", "occurredAtEpoch": 100,
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
            deletion_ledger_table=Table(), deletion_ledger_table_name="ledger",
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
        ledger = Table()
        result = service.start_history_deletion(
            command(), control_table=Table(), control_table_name="control",
            deletion_ledger_table=ledger, deletion_ledger_table_name="ledger",
            dynamodb_client=Client(), schema_version=1, erasure_sla_hours=24,
        )
        self.assertTrue(result["completed"])
        self.assertEqual(ledger.puts[0]["eventType"], "account.deletion.component.completed")
        self.assertEqual(ledger.puts[0]["component"], "HISTORY")


if __name__ == "__main__":
    unittest.main()
