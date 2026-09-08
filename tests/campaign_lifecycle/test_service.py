import sys
import unittest
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "campaign_lifecycle"
sys.path.insert(0, str(MODULE_DIR))
sys.modules.pop("service", None)
import service  # noqa: E402


class Kms:
    def __init__(self): self.created, self.disabled, self.deleted = [], [], []
    def create_key(self, **kwargs):
        self.created.append(kwargs)
        return {"KeyMetadata": {"Arn": "arn:period-key"}}
    def disable_key(self, **kwargs): self.disabled.append(kwargs)
    def schedule_key_deletion(self, **kwargs): self.deleted.append(kwargs)


class Dynamo:
    def __init__(self):
        self.period_keys, self.candidates, self.contributions = {}, [], {}
        self.puts, self.updates, self.batches = [], [], []
        self.expired = []
    def get_item(self, Key, **_kwargs):
        pk, sk = Key["PK"]["S"], Key["SK"]["S"]
        if sk == "HMAC_KEY": item = self.period_keys.get(pk)
        else:
            source = next((c for c in self.candidates if c["PK"] == pk and c["SK"] == sk), None)
            item = service.serialize(source) if source else None
        return {"Item": item} if item else {}
    def put_item(self, **kwargs): self.puts.append(kwargs)
    def update_item(self, **kwargs): self.updates.append(kwargs)
    def query(self, IndexName=None, ExpressionAttributeValues=None, **_kwargs):
        if IndexName == "ExpirationIndex":
            return {"Items": self.expired}
        if IndexName == "CandidateBucketIndex":
            bucket = ExpressionAttributeValues[":bucket"]["S"]
            return {"Items": [{"PK": {"S": c["PK"]}, "SK": {"S": c["SK"]}}
                              for c in self.candidates if c["GSI2PK"] == bucket]}
        candidate_id = ExpressionAttributeValues[":candidate"]["S"].removeprefix("CANDIDATE#")
        return {"Items": [service.serialize(item) for item in self.contributions.get(candidate_id, [])]}
    def batch_write_item(self, **kwargs):
        self.batches.append(kwargs)
        return {}


class LifecycleTests(unittest.TestCase):
    def test_explicit_expiry_queries_sparse_index_and_deletes_in_batches(self):
        dynamo = Dynamo()
        dynamo.expired = [
            {"PK": {"S": f"EVENT#{index}"}, "SK": {"S": "FEATURE"}}
            for index in range(26)
        ]

        result = service.expire_transient(environment="dev", table_name="pipeline",
            index_name="ExpirationIndex", dynamodb=dynamo, now_epoch=1_780_000_100)

        self.assertEqual(result, {"expired": 26, "backlog": False})
        self.assertEqual([len(batch["RequestItems"]["pipeline"]) for batch in dynamo.batches], [25, 1])

    def test_explicit_expiry_rejects_cross_environment_contract(self):
        with self.assertRaises(ValueError):
            service.expire_transient(environment="other", table_name="pipeline",
                index_name="ExpirationIndex", dynamodb=Dynamo(), now_epoch=1)

    def test_explicit_expiry_fails_when_dynamodb_does_not_delete_every_item(self):
        class ThrottledDynamo(Dynamo):
            def batch_write_item(self, **kwargs):
                return {"UnprocessedItems": kwargs["RequestItems"]}

        dynamo = ThrottledDynamo()
        dynamo.expired = [{"PK": {"S": "EVENT#1"}, "SK": {"S": "FEATURE"}}]
        with self.assertRaises(RuntimeError):
            service.expire_transient(environment="dev", table_name="pipeline",
                index_name="ExpirationIndex", dynamodb=dynamo, now_epoch=1)

    def test_creates_current_period_key_with_required_tags(self):
        dynamo, kms = Dynamo(), Kms()

        result = service.manage_keys(environment="dev", project_name="trustcheckradar",
            table_name="pipeline", dynamodb=dynamo, kms=kms, now_epoch=service.PERIOD_SECONDS * 10 + 1)

        self.assertEqual(result["created"], 1)
        self.assertEqual(kms.created[0]["KeySpec"], "HMAC_256")
        self.assertEqual(kms.created[0]["KeyUsage"], "GENERATE_VERIFY_MAC")
        tags = {item["TagKey"]: item["TagValue"] for item in kms.created[0]["Tags"]}
        self.assertEqual(tags["Environment"], "dev")
        self.assertEqual(tags["PeriodId"], "10")
        self.assertEqual(dynamo.puts[0]["Item"]["keyArn"], {"S": "arn:period-key"})

    def test_retires_key_after_period_and_recovery_window(self):
        dynamo, kms = Dynamo(), Kms()
        dynamo.period_keys["PERIOD#8"] = {"status": {"S": "ENABLED"}, "keyArn": {"S": "arn:old"}}

        result = service.manage_keys(environment="dev", project_name="trustcheckradar",
            table_name="pipeline", dynamodb=dynamo, kms=kms,
            now_epoch=service.PERIOD_SECONDS * 10 + service.RECOVERY_SECONDS)

        self.assertEqual(result["retired"], 1)
        self.assertEqual(kms.disabled, [{"KeyId": "arn:old"}])
        self.assertEqual(kms.deleted, [{"KeyId": "arn:old", "PendingWindowInDays": 7}])

    def test_finalizes_only_thresholded_nonlinkable_aggregate(self):
        dynamo = Dynamo()
        period = 8
        dynamo.candidates = [{"PK": "CANDIDATE#c1", "SK": "SUMMARY", "candidateId": "c1",
            "periodId": period, "taxonomyBucket": "advance_fee",
            "GSI2PK": f"PERIOD#{period}#BUCKET#advance_fee", "contributorCount": 10,
            "submissionCount": 10, "languageIds": ["en", "es"]}]
        dynamo.contributions["c1"] = [{"PK": "CANDIDATE#c1", "SK": f"CONTRIB#t{i}",
            "submissionCount": 1, "vectorApplied": True, "vector": [1.0],
            "languageId": "en", "signalIds": ["tactic.urgency", "channel.sms"]}
            for i in range(10)]

        result = service.finalize_periods(environment="dev", schema_version=1,
            pipeline_table="pipeline", intelligence_table="intelligence", minimum_contributors=10,
            aggregate_retention_days=400, dynamodb=dynamo,
            now_epoch=service.PERIOD_SECONDS * 10 + service.RECOVERY_SECONDS)

        self.assertEqual(result["finalized"], 1)
        aggregate = service.deserialize(dynamo.puts[0]["Item"])
        self.assertEqual(aggregate["state"], "PENDING_REVIEW")
        self.assertEqual(aggregate["contributorCount"], 10)
        self.assertEqual(aggregate["dimensionSchemaVersion"], 1)
        self.assertEqual(aggregate["languageIds"], ["en"])
        self.assertEqual(aggregate["tacticIds"], ["urgency"])
        self.assertEqual(aggregate["channelIds"], ["sms"])
        serialized = str(aggregate).lower()
        self.assertNotIn("token", serialized)
        self.assertNotIn("centroid", serialized)
        self.assertTrue(dynamo.batches)

    def test_dimension_ids_require_ten_distinct_contributions(self):
        contributions = [
            {"languageId": "en", "signalIds": ["tactic.urgency", "channel.sms"]}
            for _ in range(10)
        ] + [
            {"languageId": "es", "signalIds": ["tactic.fear", "channel.email"]}
            for _ in range(9)
        ]

        self.assertEqual(service.thresholded_dimension_ids(contributions, "languageId", 10), ["en"])
        self.assertEqual(
            service.thresholded_dimension_ids(contributions, "signalIds", 10, prefix="tactic."),
            ["urgency"],
        )
        self.assertEqual(
            service.thresholded_dimension_ids(contributions, "signalIds", 10, prefix="channel."),
            ["sms"],
        )


if __name__ == "__main__": unittest.main()
