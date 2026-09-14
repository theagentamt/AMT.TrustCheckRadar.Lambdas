import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from history_mutation_api.service import HistoryMutationService


class Table:
    def __init__(self, values):
        self.values = values

    def get_item(self, Key, **_kwargs):
        item = self.values.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}


class Client:
    def __init__(self):
        self.transactions = []

    def transact_write_items(self, **kwargs):
        self.transactions.append(kwargs["TransactItems"])


class Settings:
    schema_version = 1
    control_table_name = "control"
    content_table_name = "content"
    analysis_abuse_table_name = "abuse"
    mutation_retention_days = 400
    dedup_retention_days = 400
    erasure_sla_hours = 24

    def validate_recognition(self):
        return None


STATE = {"accountStatus": "ACTIVE", "historyGeneration": 2, "recognitionGeneration": 3, "acceptedSequence": 8}


class HistoryMutationTests(unittest.TestCase):
    def test_clear_only_advances_history_and_enqueues_durable_erasure(self):
        table = Table({("USER#a", "STATE"): STATE})
        client = Client()
        service = HistoryMutationService(settings=Settings(), control_table=table, dynamodb_client=client, now=lambda: 100)
        result = service.clear_history("a", "1b3f88fb-78ec-4585-a8ed-cdc751595664")
        self.assertEqual(result["historyGeneration"], 3)
        transaction = repr(client.transactions[0])
        self.assertIn("historyGeneration", transaction)
        self.assertIn("ERASURE#", transaction)
        self.assertNotIn("recognitionGeneration = :new", transaction)
        self.assertIn("PENDING#", transaction)

    def test_reset_only_advances_recognition_and_starts_zero_progress(self):
        table = Table({("USER#a", "STATE"): STATE})
        client = Client()
        service = HistoryMutationService(settings=Settings(), control_table=table, dynamodb_client=client, now=lambda: 100)
        result = service.reset_progress("a", "1b3f88fb-78ec-4585-a8ed-cdc751595664")
        self.assertEqual(result["recognitionGeneration"], 4)
        transaction = repr(client.transactions[0])
        self.assertIn("PROGRESS#4", transaction)
        self.assertIn("qualifyingChecks", transaction)
        self.assertNotIn("historyGeneration = :new", transaction)


if __name__ == "__main__":
    unittest.main()
