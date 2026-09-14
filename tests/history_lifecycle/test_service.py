import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from history_lifecycle.service import (
    HistoryLifecycleService,
    _account_id_from_partition, _generation_from_partition, _hour_buckets,
    _request_id_from_sort_key,
)


class PendingTable:
    def __init__(self, job):
        self.job = job

    def query(self, **kwargs):
        if kwargs["ExpressionAttributeValues"][":bucket"] == "PENDING#00":
            return {"Items": [{"PK": self.job["PK"], "SK": self.job["SK"], "lifecycleAt": self.job["lifecycleAt"]}]}
        return {"Items": []}

    def get_item(self, **_kwargs):
        return {"Item": self.job}


class LifecycleSettings:
    lifecycle_index_name = "PendingLifecycleIndex"
    completion_stuck_seconds = 30
    erasure_batch_size = 25


class HistoryLifecycleTests(unittest.TestCase):
    def test_bounded_lookback_includes_current_and_full_erasure_sla(self):
        buckets = _hour_buckets(1_700_000_000, 24)
        self.assertEqual(len(buckets), 25)
        self.assertEqual(len(set(buckets)), 25)

    def test_content_keys_are_parsed_without_scanning_or_assessment_data(self):
        partition = "USER#account-1#HISTORY#7"
        sort_key = "COMPLETE#1700000000000#request-1"
        self.assertEqual(_account_id_from_partition(partition), "account-1")
        self.assertEqual(_generation_from_partition(partition), 7)
        self.assertEqual(_request_id_from_sort_key(sort_key), "request-1")

    def test_pending_completion_age_is_reported_without_processing_it_as_erasure(self):
        job = {
            "PK": "USER#a", "SK": "COMPLETION#request-1", "recordType": "COMPLETION",
            "status": "PENDING", "acceptedAtEpoch": 10, "lifecycleAt": 10,
        }
        table = PendingTable(job)
        service = HistoryLifecycleService(
            settings=LifecycleSettings(), content_table=object(), control_table=table, now=lambda: 100
        )
        result = service._process_pending_jobs(100, 10)
        self.assertEqual(result["observedPendingCompletions"], 1)
        self.assertEqual(result["stuckPendingCompletions"], 1)
        self.assertEqual(result["oldestPendingCompletionAgeSeconds"], 90)
        self.assertEqual(result["completedErasureJobs"], 0)


if __name__ == "__main__":
    unittest.main()
