import json
import sys
import unittest
from pathlib import Path
from unittest import mock

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "campaign_cluster_aggregator"
sys.path.insert(0, str(MODULE_DIR))
for name in ("service", "scoring"): sys.modules.pop(name, None)
import scoring  # noqa: E402
import service  # noqa: E402

EVENT_ID = "7fbce2ac-bd2e-4d2e-9ec6-1f895a482abc"


def feature():
    return {"PK": f"EVENT#{EVENT_ID}", "SK": "FEATURE", "environment": "dev", "schemaVersion": 1,
            "periodId": 1471, "contributorToken": "token-a", "taxonomyBucket": "advance_fee",
            "vector": [1.0, 0.0], "lexicalFingerprint": ["a", "b"],
            "signalIds": ["payment_request"], "expiresAt": 2_000_000_000}


def candidate():
    return {"PK": "CANDIDATE#c1", "SK": "SUMMARY", "candidateId": "c1", "periodId": 1471,
            "taxonomyBucket": "advance_fee", "centroid": [1.0, 0.0],
            "lexicalFingerprint": ["a", "b"], "signalIds": ["payment_request"], "indicatorIds": [],
            "contributorCount": 9, "submissionCount": 9, "version": 2, "expiresAt": 2_000_000_000}


class Dynamo:
    def __init__(self, feature_item=None, candidates=None, contribution=None, dedupe=None):
        self.feature_item = service.serialize(feature_item) if feature_item else None
        self.candidates = candidates or []
        self.contribution = contribution
        self.dedupe = dedupe
        self.transactions = []
        self.puts = []

    def get_item(self, Key, **_kwargs):
        pk, sk = Key["PK"]["S"], Key["SK"]["S"]
        if sk == "FEATURE": item = self.feature_item
        elif sk == "CLUSTERED": item = self.dedupe
        elif sk.startswith("CONTRIB#"): item = self.contribution
        else:
            found = next((c for c in self.candidates if c["PK"] == pk and c["SK"] == sk), None)
            item = service.serialize(found) if found else None
        return {"Item": item} if item else {}

    def query(self, **_kwargs):
        return {"Items": [{"PK": {"S": c["PK"]}, "SK": {"S": c["SK"]}} for c in self.candidates]}

    def transact_write_items(self, TransactItems): self.transactions.append(TransactItems)
    def put_item(self, **kwargs): self.puts.append(kwargs)


def body(**updates):
    value = {"schemaVersion": 1, "eventType": "campaign.cluster.requested", "environment": "dev",
             "statisticsEventId": EVENT_ID, "recordVersion": 1}
    value.update(updates)
    return json.dumps(value)


class ScoringTests(unittest.TestCase):
    def test_applies_approved_weights(self):
        self.assertEqual(scoring.similarity(feature(), candidate()), 0.9)

    def test_category_conflict_cannot_match(self):
        self.assertEqual(scoring.similarity(feature(), candidate() | {"taxonomyBucket": "impersonation"}), 0.0)

    def test_centroid_uses_one_vector_per_new_contributor(self):
        self.assertEqual(scoring.updated_centroid([1.0, 0.0], 1, [0.0, 1.0]), [0.5, 0.5])


class ClusterServiceTests(unittest.TestCase):
    def process(self, dynamo, message=None):
        return service.process_message(message or body(), environment="dev", schema_version=1,
            table_name="pipeline", retention_days=21, max_submissions=3, dynamodb=dynamo,
            now_epoch=1_780_000_100)

    def test_matches_high_score_and_writes_one_contribution(self):
        dynamo = Dynamo(feature(), [candidate()])

        self.assertEqual(self.process(dynamo), "matched")

        transaction = dynamo.transactions[0]
        updated = service.deserialize(transaction[0]["Put"]["Item"])
        self.assertEqual(updated["candidateId"], "c1")
        self.assertEqual(updated["contributorCount"], 10)
        self.assertEqual(updated["submissionCount"], 10)
        self.assertEqual(updated["centroid"], [1.0, 0.0])
        self.assertEqual(len(transaction), 3)

    @mock.patch.object(service.uuid, "uuid4", return_value="new-candidate")
    def test_conflict_creates_separate_candidate(self, _uuid):
        dynamo = Dynamo(feature() | {"taxonomyBucket": "impersonation"}, [candidate()])

        self.assertEqual(self.process(dynamo), "candidate-created")
        created = service.deserialize(dynamo.transactions[0][0]["Put"]["Item"])
        self.assertEqual(created["candidateId"], "new-candidate")

    def test_caps_fourth_submission_without_second_vector(self):
        dynamo = Dynamo(feature(), [candidate()], contribution={"submissionCount": {"N": "3"}})

        self.assertEqual(self.process(dynamo), "contributor-capped")
        self.assertEqual(dynamo.transactions, [])
        self.assertEqual(len(dynamo.puts), 1)

    def test_missing_suppressed_and_duplicate_are_noops(self):
        self.assertEqual(self.process(Dynamo()), "missing")
        self.assertEqual(self.process(Dynamo(feature() | {"suppressed": True})), "suppressed")
        self.assertEqual(self.process(Dynamo(feature(), dedupe={"PK": {"S": "x"}})), "duplicate")

    def test_rejects_cross_environment_and_extra_fields(self):
        for message in (body(environment="prod"), body(extra="bad")):
            with self.subTest(message=message), self.assertRaises(ValueError):
                self.process(Dynamo(feature()), message)


if __name__ == "__main__": unittest.main()
