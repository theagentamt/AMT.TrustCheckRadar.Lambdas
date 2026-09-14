import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from history_mutation_api.service import HistoryMutationService
from shared_history.errors import HistoryError


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
    users_table_name = "users"
    deletion_ledger_table_name = "ledger"

    def validate_recognition(self):
        return None


STATE = {"accountStatus": "ACTIVE", "historyGeneration": 2, "recognitionGeneration": 3, "acceptedSequence": 8}


class HistoryMutationTests(unittest.TestCase):
    def test_bootstrap_is_authoritative_atomic_and_idempotent(self):
        users = Table({
            ("USER#a", "PROFILE"): {
                "PK": "USER#a", "SK": "PROFILE", "sub": "a",
                "status": "ACTIVE", "ageVerified": True,
            },
        })
        ledger = Table({})
        control = Table({})
        client = Client()
        service = HistoryMutationService(
            settings=Settings(), control_table=control, users_table=users,
            deletion_ledger_table=ledger, dynamodb_client=client, now=lambda: 100,
        )

        result = service.bootstrap("a")

        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["historyGeneration"], 0)
        transaction = client.transactions[0]
        self.assertEqual(len(transaction), 4)
        self.assertEqual(transaction[0]["ConditionCheck"]["TableName"], "users")
        self.assertEqual(transaction[1]["ConditionCheck"]["TableName"], "ledger")
        self.assertIn("attribute_not_exists", transaction[1]["ConditionCheck"]["ConditionExpression"])
        self.assertIn("PROGRESS#0", repr(transaction))

        control.values[("USER#a", "STATE")] = STATE
        replay = service.bootstrap("a")
        self.assertEqual(replay["historyGeneration"], 2)
        self.assertEqual(len(client.transactions), 1)

    def test_bootstrap_rejects_deleted_or_ineligible_foundation_account(self):
        valid_profile = {
            "PK": "USER#a", "SK": "PROFILE", "sub": "a",
            "status": "ACTIVE", "ageVerified": True,
        }
        cases = [
            ({}, {}),
            ({("USER#a", "PROFILE"): valid_profile | {"ageVerified": False}}, {}),
            ({("USER#a", "PROFILE"): valid_profile}, {
                ("ACCOUNT#a", "ACCOUNT_DELETION"): {"status": "REQUESTED"},
            }),
        ]
        for users_values, ledger_values in cases:
            service = HistoryMutationService(
                settings=Settings(), control_table=Table({}), users_table=Table(users_values),
                deletion_ledger_table=Table(ledger_values), dynamodb_client=Client(),
            )
            with self.subTest(cases=(users_values, ledger_values)), self.assertRaises(HistoryError) as raised:
                service.bootstrap("a")
            self.assertEqual(raised.exception.code, "FORBIDDEN")

    def test_delete_one_atomically_removes_the_retention_work_marker(self):
        locator = {
            "status": "ACTIVE", "historyGeneration": 2,
            "contentSortKey": "COMPLETE#0000000000100#request-1",
            "payloadHash": "a" * 64, "lifecycleBucket": "PENDING#00", "lifecycleAt": 100,
        }
        table = Table({
            ("USER#a", "STATE"): STATE,
            ("USER#a", "REQUEST#request-1"): locator,
        })
        client = Client()
        service = HistoryMutationService(
            settings=Settings(), control_table=table, dynamodb_client=client, now=lambda: 100
        )

        service.delete_one("a", "request-1", "1b3f88fb-78ec-4585-a8ed-cdc751595664")

        locator_update = next(
            item["Update"]["UpdateExpression"]
            for item in client.transactions[0]
            if "Update" in item and "deletedAtEpoch" in item["Update"]["UpdateExpression"]
        )
        self.assertIn("REMOVE contentSortKey, lifecycleBucket, lifecycleAt", locator_update)

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

    def test_history_account_deletion_fences_and_schedules_every_generation(self):
        table = Table({("USER#a", "STATE"): STATE})
        client = Client()
        service = HistoryMutationService(
            settings=Settings(), control_table=table, dynamodb_client=client, now=lambda: 100
        )

        result = service.delete_account_history(
            "a", "1b3f88fb-78ec-4585-a8ed-cdc751595664"
        )

        self.assertEqual(result["operation"], "delete_history_account_data")
        self.assertEqual(result["status"], "PENDING")
        transaction = repr(client.transactions[0])
        self.assertIn("HISTORY_DELETING", transaction)
        self.assertIn("maxHistoryGeneration", transaction)
        self.assertIn("'N': '2'", transaction)


if __name__ == "__main__":
    unittest.main()
