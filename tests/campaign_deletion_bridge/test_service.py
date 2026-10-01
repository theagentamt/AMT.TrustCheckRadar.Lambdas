import sys
import unittest
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "campaign_deletion_bridge"
sys.path.insert(0, str(MODULE_DIR))
sys.modules.pop("service", None)
import service  # noqa: E402


class Kms:
    def __init__(self): self.calls = []
    def generate_mac(self, **kwargs): self.calls.append(kwargs); return {"Mac": b"x" * 32}


class Dynamo:
    def __init__(self):
        self.updates, self.deletes, self.queries, self.transactions, self.puts = [], [], [], [], []
        self.key = {"status": {"S": "ENABLED"}, "keyArn": {"S": "arn:key"}}
        self.index_items = [{"PK": {"S": "CANDIDATE#c1"}, "SK": {"S": "CONTRIB#token"}}]
        self.remaining = [{"PK": {"S": "CANDIDATE#c1"}, "SK": {"S": "CONTRIB#other"},
                           "submissionCount": {"N": "2"}, "vectorApplied": {"BOOL": True},
                           "vector": {"L": [{"N": "0.2"}, {"N": "0.8"}]}}]
    def get_item(self, **_kwargs): return {"Item": self.key}
    def update_item(self, **kwargs): self.updates.append(kwargs)
    def delete_item(self, **kwargs): self.deletes.append(kwargs)
    def put_item(self, **kwargs): self.puts.append(kwargs)
    def query(self, IndexName=None, **kwargs):
        self.queries.append(kwargs)
        return {"Items": self.index_items if IndexName else self.remaining}
    def transact_write_items(self, **kwargs): self.transactions.append(kwargs["TransactItems"])


def command():
    return {"schemaVersion": 1, "recordVersion": 1, "environment": "dev",
            "eventType": "campaign.consent.withdrawn", "accountId": "account-123",
            "status": "REQUESTED", "occurredAtEpoch": 1_780_000_000}


def campaign_command(**changes):
    value = {
        "PK": "ACCOUNT#account-123", "SK": "CAMPAIGN_WITHDRAWAL#47debb73-444b-4bb1-9889-fb56885b7922",
        "schemaVersion": 1, "recordVersion": 1, "environment": "dev",
        "eventType": "campaign.consent.withdrawn", "accountId": "account-123",
        "status": "PENDING", "occurredAtEpoch": 1_780_000_000,
        "consentEpochId": "15c81ba4-2fa6-43c3-8895-889f08c931bf",
        "deleteByEpoch": 1_780_086_400,
        "operationId": "47debb73-444b-4bb1-9889-fb56885b7922",
    }
    value.update(changes)
    return value


def account_command(**changes):
    value = {
        "PK": "ACCOUNT#account-123", "SK": "ACCOUNT_DELETION",
        "schemaVersion": 1, "recordVersion": 1, "environment": "dev",
        "eventType": "account.deletion.requested", "accountId": "account-123",
        "status": "REQUESTED", "occurredAtEpoch": 1_780_000_000,
        "deleteByEpoch": 1_780_086_400,
        "operationId": "47debb73-444b-4bb1-9889-fb56885b7922",
    }
    value.update(changes)
    return value


class DeletionTests(unittest.TestCase):
    def test_active_periods_include_previous_only_during_recovery(self):
        current = 10 * service.PERIOD_SECONDS
        self.assertEqual(service.active_periods(current + 1), [10, 9])
        self.assertEqual(service.active_periods(current + service.RECOVERY_SECONDS), [10])

    def test_no_completion_helper_can_bypass_missing_locator_proof(self):
        for function, value in ((service.complete_account_deletion_component, account_command()),
                                (service.complete_campaign_withdrawal, campaign_command())):
            dynamo = Dynamo()
            with self.assertRaises(RuntimeError):
                function(value, dynamodb=dynamo)
            self.assertEqual(dynamo.puts + dynamo.transactions, [])

    def test_strict_command_rejects_cross_environment_and_extra_fields(self):
        def av(value): return {"N": str(value)} if isinstance(value, int) else {"S": value}
        for changes in ({"environment": "prod"}, {"extra": "bad"}):
            item = command() | changes
            record = {"eventName": "INSERT", "dynamodb": {"NewImage": {k: av(v) for k, v in item.items()}}}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                service.parse_deletion_record(record, environment="dev", schema_version=1)

    def test_parses_exact_pending_campaign_command_and_ignores_complete_update(self):
        def av(value): return {"N": str(value)} if isinstance(value, int) else {"S": value}
        pending = campaign_command()
        record = {"eventName": "INSERT", "dynamodb": {"NewImage": {k: av(v) for k, v in pending.items()}}}
        self.assertEqual(service.parse_deletion_record(record, environment="dev", schema_version=1), pending)

        complete = pending | {"status": "COMPLETE", "completedAtEpoch": 1_780_000_010}
        record = {"eventName": "MODIFY", "dynamodb": {"NewImage": {k: av(v) for k, v in complete.items()}}}
        self.assertIsNone(service.parse_deletion_record(record, environment="dev", schema_version=1))

    def test_campaign_command_rejects_wrong_status_deadline_key_or_unknown_field(self):
        def av(value): return {"N": str(value)} if isinstance(value, int) else {"S": value}
        for changes in ({"status": "REQUESTED"}, {"deleteByEpoch": 1}, {"PK": "wrong"}, {"extra": "bad"}):
            item = campaign_command(**changes)
            record = {"eventName": "INSERT", "dynamodb": {"NewImage": {k: av(v) for k, v in item.items()}}}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                service.parse_deletion_record(record, environment="dev", schema_version=1)

    def test_parses_exact_account_deletion_command(self):
        def av(value): return {"N": str(value)} if isinstance(value, int) else {"S": value}
        item = account_command()
        record = {"eventName": "INSERT", "dynamodb": {"NewImage": {k: av(v) for k, v in item.items()}}}

        self.assertEqual(
            service.parse_deletion_record(record, environment="dev", schema_version=1),
            item,
        )
        for changes in ({"deleteByEpoch": 1}, {"operationId": "not-a-uuid"}, {"extra": "bad"}):
            invalid = account_command(**changes)
            record = {"eventName": "INSERT", "dynamodb": {"NewImage": {k: av(v) for k, v in invalid.items()}}}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                service.parse_deletion_record(record, environment="dev", schema_version=1)



if __name__ == "__main__": unittest.main()
