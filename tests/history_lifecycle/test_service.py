import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from history_lifecycle.service import (
    HistoryLifecycleService,
    _account_hash,
    _account_id_from_partition,
    _generation_from_partition,
    _hour_label,
    _request_id_from_sort_key,
)


class ConditionalFailure(Exception):
    def __init__(self):
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class Settings:
    environment = "dev"
    schema_version = 1
    expiration_index_name = "ExpirationIndex"
    lifecycle_index_name = "PendingLifecycleIndex"
    lifecycle_start_epoch_hour = 0
    lifecycle_max_items_per_sweep = 30
    lifecycle_max_bucket_queries_per_sweep = 40
    expiration_reconciliation_hours = 24
    completion_stuck_seconds = 30
    completion_recheck_seconds = 60
    erasure_batch_size = 25
    dedup_retention_days = 400
    mutation_retention_days = 400


class ControlTable:
    def __init__(self, items=None, pending=None):
        self.items = items or {}
        self.pending = pending or {}
        self.updates = []

    def get_item(self, Key, **_kwargs):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}

    def put_item(self, Item, **_kwargs):
        self.items[(Item["PK"], Item["SK"])] = dict(Item)
        return {}

    def update_item(self, Key, **kwargs):
        self.updates.append((Key, kwargs))
        item = self.items.get((Key["PK"], Key["SK"]), {"PK": Key["PK"], "SK": Key["SK"]})
        values = kwargs.get("ExpressionAttributeValues") or {}
        expression = kwargs.get("UpdateExpression", "")
        if "hourEpoch = :hour" in expression:
            item.update({"hourEpoch": values[":hour"], "shard": values[":shard"], "stateVersion": values[":next_version"]})
        if "lifecycleAt = :next" in expression:
            item["lifecycleAt"] = values[":next"]
        if ":cancelled" in values:
            item["status"] = values[":cancelled"]
        if ":complete" in values:
            item["status"] = values[":complete"]
        self.items[(Key["PK"], Key["SK"])] = item
        return {}

    def query(self, **kwargs):
        bucket = kwargs["ExpressionAttributeValues"].get(":bucket")
        now = kwargs["ExpressionAttributeValues"].get(":now", 0)
        items = [
            {"PK": item["PK"], "SK": item["SK"], "lifecycleAt": item["lifecycleAt"]}
            for item in self.pending.get(bucket, []) if item["lifecycleAt"] <= now
        ]
        return {"Items": items[:kwargs["Limit"]], **({"LastEvaluatedKey": {"PK": items[kwargs["Limit"] - 1]["PK"], "SK": items[kwargs["Limit"] - 1]["SK"]}} if len(items) > kwargs["Limit"] else {})}


class ExpirationTable:
    def __init__(self, pages):
        self.pages = list(pages)
        self.deletes = []

    def query(self, **_kwargs):
        return self.pages.pop(0) if self.pages else {"Items": []}

    def delete_item(self, **kwargs):
        self.deletes.append(kwargs["Key"])


class BucketExpirationTable:
    def __init__(self, items=None, *, visible=True):
        self.items = list(items or [])
        self.visible = visible
        self.deletes = []
        self.queries = []

    def query(self, **kwargs):
        values = kwargs["ExpressionAttributeValues"]
        bucket = values[":bucket"]
        now = values[":now"]
        self.queries.append(bucket)
        if not self.visible:
            return {"Items": []}
        items = [
            dict(item) for item in self.items
            if item["expiryBucket"] == bucket and item["expiresAt"] <= now
        ]
        limit = kwargs["Limit"]
        return {
            "Items": items[:limit],
            **({"LastEvaluatedKey": {"PK": items[limit - 1]["PK"], "SK": items[limit - 1]["SK"]}}
               if len(items) > limit else {}),
        }

    def delete_item(self, **kwargs):
        key = kwargs["Key"]
        self.deletes.append(key)
        self.items = [
            item for item in self.items
            if (item["PK"], item["SK"]) != (key["PK"], key["SK"])
        ]


class AbuseTable:
    def __init__(self, items=None):
        self.items = items or {}
        self.updates = []

    def query(self, **kwargs):
        partition = kwargs["ExpressionAttributeValues"][":pk"]
        items = [dict(item) for (pk, _), item in self.items.items() if pk == partition]
        return {"Items": items[:kwargs["Limit"]]}

    def update_item(self, Key, **kwargs):
        key = (Key["PK"], Key["SK"])
        item = self.items.get(key)
        if not item:
            raise ConditionalFailure()
        generation = (kwargs.get("ExpressionAttributeValues") or {}).get(":generation")
        if generation is not None and (item.get("historyAuthorization") or {}).get("historyGeneration") != generation:
            raise ConditionalFailure()
        if "attribute_not_exists(historyAuthorization)" in kwargs.get("ConditionExpression", "") and "historyAuthorization" in item:
            raise ConditionalFailure()
        if "attribute_exists(#response)" in kwargs.get("ConditionExpression", "") and "response" not in item:
            raise ConditionalFailure()
        item.pop("response", None)
        item["status"] = "COMPLETED_ERASED"
        self.updates.append(key)
        return {}


class HistoryLifecycleTests(unittest.TestCase):
    def test_content_keys_are_parsed_without_assessment_data(self):
        partition = "USER#account-1#HISTORY#7"
        sort_key = "COMPLETE#1700000000000#request-1"
        self.assertEqual(_account_id_from_partition(partition), "account-1")
        self.assertEqual(_generation_from_partition(partition), 7)
        self.assertEqual(_request_id_from_sort_key(sort_key), "request-1")

    def test_old_expiration_bucket_remains_discoverable_through_durable_checkpoint(self):
        checkpoint = {
            "PK": "LIFECYCLE#dev", "SK": "EXPIRATION#CONTROL",
            "recordType": "LIFECYCLE_CHECKPOINT", "hourEpoch": 0,
            "shard": 0, "stateVersion": 1,
        }
        control = ControlTable({("LIFECYCLE#dev", "EXPIRATION#CONTROL"): checkpoint})
        expired = {"PK": "CURSOR#old", "SK": "CURSOR", "expiresAt": 1}
        table = ExpirationTable([{"Items": [expired]}])
        service = HistoryLifecycleService(
            settings=Settings(), content_table=object(), control_table=control,
            abuse_table=AbuseTable(), now=lambda: 200_000,
        )
        result = service._sweep_backlog(
            table, "CONTROL", 198_000, 200_000,
            content=False, item_limit=10, query_limit=1,
        )
        self.assertEqual(result["deleted"], 1)
        self.assertTrue(result["checkpointBacklog"])
        self.assertEqual(control.items[("LIFECYCLE#dev", "EXPIRATION#CONTROL")]["shard"], 1)

    def test_checkpoint_does_not_advance_until_a_paginated_bucket_is_drained(self):
        checkpoint = {
            "PK": "LIFECYCLE#dev", "SK": "EXPIRATION#CONTROL",
            "recordType": "LIFECYCLE_CHECKPOINT", "hourEpoch": 0,
            "shard": 4, "stateVersion": 9,
        }
        control = ControlTable({("LIFECYCLE#dev", "EXPIRATION#CONTROL"): checkpoint})
        page = {
            "Items": [{"PK": "CURSOR#one", "SK": "CURSOR", "expiresAt": 1}],
            "LastEvaluatedKey": {"PK": "CURSOR#one", "SK": "CURSOR"},
        }
        service = HistoryLifecycleService(
            settings=Settings(), content_table=object(), control_table=control,
            abuse_table=AbuseTable(), now=lambda: 200_000,
        )
        result = service._sweep_backlog(
            ExpirationTable([page]), "CONTROL", 198_000, 200_000,
            content=False, item_limit=10, query_limit=1,
        )
        self.assertTrue(result["checkpointBacklog"])
        self.assertEqual(control.items[("LIFECYCLE#dev", "EXPIRATION#CONTROL")]["shard"], 4)

    def test_current_hour_is_revisited_when_an_item_becomes_due_later(self):
        current_hour = 3_600
        item = {
            "PK": "CURSOR#future", "SK": "CURSOR", "expiresAt": current_hour + 2_400,
            "expiryBucket": f"CONTROL#{_hour_label(current_hour)}#00",
        }
        table = BucketExpirationTable([item])
        service = HistoryLifecycleService(
            settings=Settings(), content_table=object(), control_table=ControlTable(),
            abuse_table=AbuseTable(), now=lambda: current_hour + 300,
        )

        early = service._sweep_current_hour(
            table, "CONTROL", current_hour, current_hour + 300,
            content=False, item_limit=10, query_limit=16,
        )
        late = service._sweep_current_hour(
            table, "CONTROL", current_hour, current_hour + 2_700,
            content=False, item_limit=10, query_limit=16,
        )

        self.assertEqual(early["deleted"], 0)
        self.assertEqual(late["deleted"], 1)
        self.assertEqual(table.deletes, [{"PK": "CURSOR#future", "SK": "CURSOR"}])

    def test_reconciliation_recovers_an_item_visible_after_backlog_advanced(self):
        expired_hour = 0
        item = {
            "PK": "CURSOR#delayed", "SK": "CURSOR", "expiresAt": 300,
            "expiryBucket": f"CONTROL#{_hour_label(expired_hour)}#00",
        }
        table = BucketExpirationTable([item], visible=False)
        control = ControlTable()
        service = HistoryLifecycleService(
            settings=Settings(), content_table=object(), control_table=control,
            abuse_table=AbuseTable(), now=lambda: 7_500,
        )

        first = service._sweep_backlog(
            table, "CONTROL", expired_hour, 7_500,
            content=False, item_limit=25, query_limit=16,
        )
        self.assertEqual(first["deleted"], 0)
        self.assertEqual(
            control.items[("LIFECYCLE#dev", "EXPIRATION#CONTROL")]["hourEpoch"], 3_600
        )

        table.visible = True
        recovered = service._sweep_reconciliation(
            table, "CONTROL", expired_hour, 7_500,
            content=False, item_limit=25, query_limit=16,
        )

        self.assertEqual(recovered["deleted"], 1)
        self.assertEqual(table.deletes, [{"PK": "CURSOR#delayed", "SK": "CURSOR"}])

    def test_completion_is_rescheduled_so_later_erasure_can_be_seen(self):
        completion = {
            "PK": "USER#a", "SK": "COMPLETION#request-1", "recordType": "COMPLETION",
            "status": "PENDING", "acceptedAtEpoch": 10, "lifecycleAt": 10,
            "historyGeneration": 1, "acceptedSequence": 1, "requestId": "request-1",
        }
        erasure = {
            "PK": "USER#a", "SK": "ERASURE#op", "recordType": "ERASURE",
            "status": "PENDING", "stage": "REPLAY", "historyGeneration": 0,
            "operationId": "op", "reason": "CLEAR_HISTORY", "createdAtEpoch": 20,
            "deleteByEpoch": 200, "lifecycleAt": 20,
        }
        control = ControlTable(
            {
                ("USER#a", "COMPLETION#request-1"): completion,
                ("USER#a", "STATE"): {"accountStatus": "ACTIVE", "historyGeneration": 1},
                ("USER#a", "ERASURE#op"): erasure,
            },
            {"PENDING#00": [completion, erasure]},
        )
        service = HistoryLifecycleService(
            settings=Settings(), content_table=object(), control_table=control,
            abuse_table=AbuseTable(), now=lambda: 100,
        )
        result = service._process_pending_jobs(100, 1)
        self.assertEqual(result["oldestPendingCompletionAgeSeconds"], 90)
        self.assertEqual(completion["lifecycleAt"], 160)
        second = service._process_pending_jobs(100, 1)
        self.assertEqual(second["completedErasureJobs"], 1)

    def test_clear_replay_stage_erases_responses_when_ttl_does_nothing(self):
        account_id = "a"
        partition = f"ANALYSIS#REQUEST#{_account_hash(account_id)}"
        abuse = AbuseTable({
            (partition, "old"): {"PK": partition, "SK": "old", "response": {"summary": "secret"}, "historyAuthorization": {"historyGeneration": 1}},
            (partition, "new"): {"PK": partition, "SK": "new", "response": {"summary": "keep"}, "historyAuthorization": {"historyGeneration": 2}},
            (partition, "legacy"): {"PK": partition, "SK": "legacy", "response": {"summary": "legacy secret"}},
        })
        job = {
            "PK": "USER#a", "SK": "ERASURE#op", "recordType": "ERASURE",
            "status": "PENDING", "stage": "REPLAY", "historyGeneration": 1,
            "operationId": "op", "reason": "CLEAR_HISTORY",
        }
        control = ControlTable({("USER#a", "ERASURE#op"): job})
        service = HistoryLifecycleService(
            settings=Settings(), content_table=object(), control_table=control,
            abuse_table=abuse, now=lambda: 100,
        )
        complete, redacted = service._process_erasure_job(job, 100)
        self.assertTrue(complete)
        self.assertEqual(redacted, 2)
        self.assertNotIn("response", abuse.items[(partition, "old")])
        self.assertNotIn("response", abuse.items[(partition, "legacy")])
        self.assertEqual(abuse.items[(partition, "new")]["response"], {"summary": "keep"})

    def test_expiration_explicitly_erases_replay_without_ttl_help(self):
        account_id = "a"
        request_id = "request-1"
        partition = f"ANALYSIS#REQUEST#{_account_hash(account_id)}"
        abuse = AbuseTable({
            (partition, request_id): {"PK": partition, "SK": request_id, "response": {"summary": "secret"}}
        })
        control = ControlTable()
        service = HistoryLifecycleService(
            settings=Settings(), content_table=object(), control_table=control,
            abuse_table=abuse, now=lambda: 100,
        )
        redacted = service._expire_locator_and_replay(
            {"PK": "USER#a#HISTORY#0", "SK": "COMPLETE#0000000000001#request-1"}, 100
        )
        self.assertEqual(redacted, 1)
        self.assertNotIn("response", abuse.items[(partition, request_id)])


if __name__ == "__main__":
    unittest.main()
