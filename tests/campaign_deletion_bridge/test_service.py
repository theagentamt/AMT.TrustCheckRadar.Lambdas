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
        self.updates, self.deletes, self.queries = [], [], []
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


def command():
    return {"schemaVersion": 1, "recordVersion": 1, "environment": "dev",
            "eventType": "campaign.consent.withdrawn", "accountId": "account-123",
            "status": "REQUESTED", "occurredAtEpoch": 1_780_000_000}


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


if __name__ == "__main__": unittest.main()
