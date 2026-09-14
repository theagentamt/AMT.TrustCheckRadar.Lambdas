from datetime import UTC, datetime, timedelta
from decimal import Decimal
import time

from shared_history.errors import HistoryError


class HistoryLifecycleService:
    def __init__(self, *, settings, content_table, control_table, now=lambda: int(time.time())):
        self.settings = settings
        self.content_table = content_table
        self.control_table = control_table
        self.now = now

    def sweep(self):
        now = self.now()
        budget = self.settings.lifecycle_max_items_per_sweep
        expired_content = self._expire_table(self.content_table, "HISTORY", now, content=True, limit=budget)
        budget -= expired_content
        expired_control = self._expire_table(self.control_table, "CONTROL", now, content=False, limit=budget)
        budget -= expired_control
        pending = self._process_pending_jobs(now, budget)
        return {
            "schemaVersion": self.settings.schema_version,
            "operation": "sweep",
            "expiredContentRecords": expired_content,
            "expiredControlRecords": expired_control,
            "completedErasureJobs": pending["completedErasureJobs"],
            "overdueErasureJobs": pending["overdueErasureJobs"],
            "observedPendingCompletions": pending["observedPendingCompletions"],
            "oldestPendingCompletionAgeSeconds": pending["oldestPendingCompletionAgeSeconds"],
            "oldestPendingErasureAgeSeconds": pending["oldestPendingErasureAgeSeconds"],
            "stuckPendingCompletions": pending["stuckPendingCompletions"],
            "worksetTruncated": pending["worksetTruncated"] or budget <= 0,
            "completedAtEpoch": now,
        }

    def _expire_table(self, table, prefix, now, *, content, limit):
        deleted = 0
        if limit <= 0:
            return 0
        for hour in reversed(_hour_buckets(now, self.settings.lifecycle_lookback_hours)):
            for shard in range(16):
                if deleted >= limit:
                    return deleted
                bucket = f"{prefix}#{hour}#{shard:02d}"
                page = table.query(
                    IndexName=self.settings.expiration_index_name,
                    KeyConditionExpression="expiryBucket = :bucket AND expiresAt <= :now",
                    ExpressionAttributeValues={":bucket": bucket, ":now": now},
                    ProjectionExpression="PK, SK, expiresAt",
                    Limit=min(25, limit - deleted),
                )
                for item in page.get("Items") or []:
                    table.delete_item(
                        Key={"PK": item["PK"], "SK": item["SK"]},
                        ConditionExpression="expiresAt <= :now",
                        ExpressionAttributeValues={":now": now},
                    )
                    deleted += 1
                    if content:
                        self._expire_locator(item, now)
        return deleted

    def _expire_locator(self, content_item, now):
        request_id = _request_id_from_sort_key(content_item.get("SK"))
        account_id = _account_id_from_partition(content_item.get("PK"))
        generation = _generation_from_partition(content_item.get("PK"))
        if request_id is None or account_id is None or generation is None:
            raise HistoryError("SERVER_UNAVAILABLE", "An indexed History key is invalid.")
        try:
            self.control_table.update_item(
                Key={"PK": f"USER#{account_id}", "SK": f"REQUEST#{request_id}"},
                UpdateExpression="SET #status = :expired, deletedAtEpoch = :now REMOVE contentSortKey",
                ConditionExpression="historyGeneration = :generation AND contentSortKey = :content_sk",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":expired": "EXPIRED", ":now": now, ":generation": generation,
                    ":content_sk": content_item["SK"],
                },
            )
        except Exception as err:
            if getattr(err, "response", {}).get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise

    def _process_pending_jobs(self, now, limit):
        result = {
            "completedErasureJobs": 0, "overdueErasureJobs": 0,
            "observedPendingCompletions": 0, "oldestPendingCompletionAgeSeconds": None,
            "oldestPendingErasureAgeSeconds": None, "stuckPendingCompletions": 0,
            "worksetTruncated": limit <= 0,
        }
        examined = 0
        for shard in range(16):
            if examined >= limit:
                result["worksetTruncated"] = True
                break
            page = self.control_table.query(
                IndexName=self.settings.lifecycle_index_name,
                KeyConditionExpression="lifecycleBucket = :bucket AND lifecycleAt <= :now",
                ExpressionAttributeValues={":bucket": f"PENDING#{shard:02d}", ":now": now},
                ProjectionExpression="PK, SK, lifecycleAt",
                Limit=min(25, limit - examined),
            )
            if page.get("LastEvaluatedKey"):
                result["worksetTruncated"] = True
            for key_item in page.get("Items") or []:
                examined += 1
                job = self.control_table.get_item(
                    Key={"PK": key_item["PK"], "SK": key_item["SK"]}, ConsistentRead=True
                ).get("Item")
                if not job or job.get("status") != "PENDING":
                    continue
                if job.get("recordType") == "COMPLETION":
                    age = max(0, now - int(job.get("acceptedAtEpoch", now)))
                    result["observedPendingCompletions"] += 1
                    result["oldestPendingCompletionAgeSeconds"] = max(
                        result["oldestPendingCompletionAgeSeconds"] or 0, age
                    )
                    if age >= self.settings.completion_stuck_seconds:
                        result["stuckPendingCompletions"] += 1
                    continue
                if job.get("recordType") != "ERASURE":
                    raise HistoryError("SERVER_UNAVAILABLE", "A pending lifecycle record is invalid.")
                age = max(0, now - int(job.get("createdAtEpoch", now)))
                result["oldestPendingErasureAgeSeconds"] = max(
                    result["oldestPendingErasureAgeSeconds"] or 0, age
                )
                if int(job.get("deleteByEpoch", now)) < now:
                    result["overdueErasureJobs"] += 1
                if self._process_erasure_job(job, now):
                    result["completedErasureJobs"] += 1
        if not result["worksetTruncated"]:
            result["oldestPendingCompletionAgeSeconds"] = result["oldestPendingCompletionAgeSeconds"] or 0
            result["oldestPendingErasureAgeSeconds"] = result["oldestPendingErasureAgeSeconds"] or 0
        return result

    def _process_erasure_job(self, job, now):
        account_id = _account_id_from_user_pk(job.get("PK"))
        generation = _exact_nonnegative_int(job.get("historyGeneration"))
        if account_id is None or generation is None:
            raise HistoryError("SERVER_UNAVAILABLE", "A pending erasure job is invalid.")
        partition = f"USER#{account_id}#HISTORY#{generation}"
        kwargs = {
            "KeyConditionExpression": "PK = :pk", "ExpressionAttributeValues": {":pk": partition},
            "ConsistentRead": True, "ProjectionExpression": "PK, SK, requestId",
            "Limit": self.settings.erasure_batch_size,
        }
        if job.get("continuationSortKey"):
            kwargs["ExclusiveStartKey"] = {"PK": partition, "SK": job["continuationSortKey"]}
        page = self.content_table.query(**kwargs)
        for item in page.get("Items") or []:
            self.content_table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
            self._mark_locator_erased(account_id, generation, item, now)
        start_key = page.get("LastEvaluatedKey")
        if start_key:
            self.control_table.update_item(
                Key={"PK": job["PK"], "SK": job["SK"]},
                UpdateExpression="SET continuationSortKey = :continuation, lifecycleAt = :next",
                ConditionExpression="#status = :pending AND historyGeneration = :generation",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":continuation": start_key["SK"], ":next": now + 1, ":pending": "PENDING", ":generation": generation},
            )
            return False
        self.control_table.update_item(
            Key={"PK": job["PK"], "SK": job["SK"]},
            UpdateExpression="SET #status = :complete, completedAtEpoch = :now, expiresAt = :expires_at, expiryBucket = :expiry_bucket REMOVE lifecycleBucket, lifecycleAt, continuationSortKey",
            ConditionExpression="#status = :pending AND historyGeneration = :generation",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":complete": "COMPLETE", ":pending": "PENDING", ":now": now,
                ":generation": generation,
                ":expires_at": now + self.settings.mutation_retention_days * 86400,
                ":expiry_bucket": _control_expiry_bucket(
                    now + self.settings.mutation_retention_days * 86400,
                    str(job.get("operationId") or job.get("SK")),
                ),
            },
        )
        return True

    def _mark_locator_erased(self, account_id, generation, content, now):
        request_id = content.get("requestId") or _request_id_from_sort_key(content.get("SK"))
        if not request_id:
            raise HistoryError("SERVER_UNAVAILABLE", "An erasure History key is invalid.")
        try:
            self.control_table.update_item(
                Key={"PK": f"USER#{account_id}", "SK": f"REQUEST#{request_id}"},
                UpdateExpression="SET #status = :cleared, deletedAtEpoch = :now REMOVE contentSortKey",
                ConditionExpression="historyGeneration = :generation AND contentSortKey = :content_sk",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":cleared": "CLEARED", ":now": now, ":generation": generation,
                    ":content_sk": content["SK"],
                },
            )
        except Exception as err:
            if getattr(err, "response", {}).get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise


def _hour_buckets(now, lookback_hours):
    current = datetime.fromtimestamp(now, UTC).replace(minute=0, second=0, microsecond=0)
    return [(current - timedelta(hours=offset)).strftime("%Y%m%d%H") for offset in range(lookback_hours + 1)]


def _request_id_from_sort_key(value):
    if not isinstance(value, str) or not value.startswith("COMPLETE#"):
        return None
    parts = value.split("#", 2)
    return parts[2] if len(parts) == 3 and parts[2] else None


def _account_id_from_partition(value):
    if not isinstance(value, str) or not value.startswith("USER#") or "#HISTORY#" not in value:
        return None
    return value[5:].rsplit("#HISTORY#", 1)[0]


def _generation_from_partition(value):
    if not isinstance(value, str) or "#HISTORY#" not in value:
        return None
    try:
        result = int(value.rsplit("#HISTORY#", 1)[1])
    except ValueError:
        return None
    return result if result >= 0 else None


def _account_id_from_user_pk(value):
    return value[5:] if isinstance(value, str) and value.startswith("USER#") and len(value) > 5 else None


def _exact_nonnegative_int(value):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    integer = int(value)
    return integer if integer == value and integer >= 0 else None


def _control_expiry_bucket(expires_at, value):
    import hashlib
    hour = datetime.fromtimestamp(expires_at, UTC).strftime("%Y%m%d%H")
    shard = int(hashlib.sha256(value.encode()).hexdigest()[:2], 16) % 16
    return f"CONTROL#{hour}#{shard:02d}"
