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
        self.updates, self.deletes, self.queries, self.transactions = [], [], [], []
        self.key = {"status": {"S": "ENABLED"}, "keyArn": {"S": "arn:key"}}
        self.index_items = [{"PK": {"S": "CANDIDATE#c1"}, "SK": {"S": "CONTRIB#token"}}]
        self.remaining = [{"PK": {"S": "CANDIDATE#c1"}, "SK": {"S": "CONTRIB#other"},
                           "submissionCount": {"N": "2"}, "vectorApplied": {"BOOL": True},
                           "vector": {"L": [{"N": "0.2"}, {"N": "0.8"}]}}]
    def get_item(self, **_kwargs): return {"Item": self.key}
    def update_item(self, **kwargs): self.updates.append(kwargs)
    def delete_item(self, **kwargs): self.deletes.append(kwargs)
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


class DeletionTests(unittest.TestCase):
    def test_active_periods_include_previous_only_during_recovery(self):
        current = 10 * service.PERIOD_SECONDS
        self.assertEqual(service.active_periods(current + 1), [10, 9])
        self.assertEqual(service.active_periods(current + service.RECOVERY_SECONDS), [10])

    def test_tombstones_deletes_and_recomputes_without_persisting_identity(self):
        dynamo, kms = Dynamo(), Kms()

        result = service.delete_account_contributions(command(), table_name="pipeline",
            retention_days=21, dynamodb=dynamo, kms=kms, now_epoch=10 * service.PERIOD_SECONDS + 1)

        self.assertEqual(result, {"deleted": 2, "recomputedCandidates": 1})
        self.assertEqual(len(kms.calls), 2)
        self.assertEqual(kms.calls[0]["Message"], b"campaign-contributor:v1\0account-123")
        self.assertEqual(len(dynamo.deletes), 2)
        self.assertTrue(any("centroid" in call.get("UpdateExpression", "") for call in dynamo.updates))
        self.assertNotIn("account-123", str(dynamo.updates))

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

    def test_successful_deletion_completion_is_atomic_and_privacy_safe(self):
        dynamo = Dynamo()
        result = service.complete_campaign_withdrawal(
            campaign_command(), users_table_name="users", deletion_ledger_table_name="ledger",
            participation_item_sk="CAMPAIGN_PARTICIPATION", audit_days=400,
            dynamodb=dynamo, now_epoch=1_780_000_010,
        )

        self.assertTrue(result)
        transaction = dynamo.transactions[0]
        self.assertEqual(len(transaction), 3)
        state_update = transaction[0]["Update"]
        self.assertIn("#state = :pending", state_update["ConditionExpression"])
        self.assertIn("consentEpochId = :epoch", state_update["ConditionExpression"])
        self.assertIn("lastOperationId = :operation_id", state_update["ConditionExpression"])
        receipt = transaction[1]["Put"]["Item"]
        self.assertEqual(receipt["eventType"], {"S": "campaign.participation.withdrawal_completed"})
        self.assertEqual(receipt["expiresAt"], {"N": str(1_780_000_010 + 400 * 86400)})
        self.assertNotIn("accountId", receipt)
        ledger_update = transaction[2]["Update"]
        self.assertEqual(ledger_update["ExpressionAttributeValues"][":complete"], {"S": "COMPLETE"})
        self.assertEqual(ledger_update["ExpressionAttributeValues"][":completed_at"], {"N": "1780000010"})

    def test_completion_retry_accepts_already_complete_matching_command(self):
        class Canceled(Exception):
            response = {"Error": {"Code": "TransactionCanceledException"}}

        class ReplayDynamo(Dynamo):
            def transact_write_items(self, **_kwargs):
                raise Canceled()

            def get_item(self, TableName=None, **kwargs):
                if TableName == "ledger":
                    complete = campaign_command(status="COMPLETE") | {"completedAtEpoch": 1_780_000_010}
                    return {"Item": {key: ({"N": str(value)} if isinstance(value, int) else {"S": value}) for key, value in complete.items()}}
                return super().get_item(**kwargs)

        self.assertFalse(service.complete_campaign_withdrawal(
            campaign_command(), users_table_name="users", deletion_ledger_table_name="ledger",
            participation_item_sk="CAMPAIGN_PARTICIPATION", audit_days=400,
            dynamodb=ReplayDynamo(), now_epoch=1_780_000_010,
        ))


if __name__ == "__main__": unittest.main()
