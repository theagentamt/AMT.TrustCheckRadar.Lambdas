from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import time

from shared_history.errors import HistoryError
from shared_account_finalization.history_receipts import current_command, write_receipt


ACCOUNT_DELETION_RECEIPT_RETENTION_DAYS = 120


class HistoryLifecycleService:
    def __init__(
        self, *, settings, content_table, control_table, abuse_table,
        deletion_ledger_table=None, dynamodb_client=None, now=lambda: int(time.time()),
    ):
        self.settings = settings
        self.content_table = content_table
        self.control_table = control_table
        self.abuse_table = abuse_table
        self.deletion_ledger_table = deletion_ledger_table
        self.dynamodb_client = dynamodb_client
        self.now = now

    def sweep(self):
        now = self.now()
        item_budget = self.settings.lifecycle_max_items_per_sweep
        content_budget = item_budget // 3
        control_budget = item_budget // 3
        pending_budget = item_budget - content_budget - control_budget
        query_budget = self.settings.lifecycle_max_bucket_queries_per_sweep
        content_queries = query_budget // 2
        control_queries = query_budget - content_queries
        expired_content = self._expire_table(
            self.content_table, "HISTORY", now, content=True,
            item_limit=content_budget, query_limit=content_queries,
        )
        expired_control = self._expire_table(
            self.control_table, "CONTROL", now, content=False,
            item_limit=control_budget, query_limit=control_queries,
        )
        pending = self._process_pending_jobs(now, pending_budget)
        truncated = (
            expired_content["checkpointBacklog"]
            or expired_control["checkpointBacklog"]
            or pending["worksetTruncated"]
        )
        return {
            "schemaVersion": self.settings.schema_version,
            "operation": "sweep",
            "expiredContentRecords": expired_content["deleted"],
            "expiredControlRecords": expired_control["deleted"],
            "expirationBucketQueries": expired_content["queries"] + expired_control["queries"],
            "expirationCheckpointLagSeconds": max(
                expired_content["checkpointLagSeconds"], expired_control["checkpointLagSeconds"]
            ),
            "completedErasureJobs": pending["completedErasureJobs"],
            "completedRetentionPurges": pending["completedRetentionPurges"],
            "overdueErasureJobs": pending["overdueErasureJobs"],
            "observedPendingCompletions": pending["observedPendingCompletions"],
            "oldestPendingCompletionAgeSeconds": pending["oldestPendingCompletionAgeSeconds"],
            "oldestPendingErasureAgeSeconds": pending["oldestPendingErasureAgeSeconds"],
            "stuckPendingCompletions": pending["stuckPendingCompletions"],
            "redactedReplayRecords": pending["redactedReplayRecords"] + expired_content["redactedReplayRecords"],
            "worksetTruncated": truncated,
            "completedAtEpoch": now,
        }

    def _expire_table(self, table, prefix, now, *, content, item_limit, query_limit):
        current_hour = now - (now % 3600)
        lane_item_limit = max(1, item_limit // 3)
        current = self._sweep_current_hour(
            table, prefix, current_hour, now, content=content,
            item_limit=lane_item_limit, query_limit=min(16, query_limit),
        )
        remaining_queries = max(0, query_limit - current["queries"])
        backlog = self._sweep_backlog(
            table, prefix, current_hour - 3600, now, content=content,
            item_limit=lane_item_limit, query_limit=remaining_queries // 2,
        )
        reconciliation = self._sweep_reconciliation(
            table, prefix, current_hour - 3600, now, content=content,
            item_limit=max(1, item_limit - lane_item_limit * 2),
            query_limit=remaining_queries - remaining_queries // 2,
        )
        return {
            "deleted": current["deleted"] + backlog["deleted"] + reconciliation["deleted"],
            "queries": current["queries"] + backlog["queries"] + reconciliation["queries"],
            "checkpointBacklog": current["pageBacklog"] or backlog["checkpointBacklog"] or reconciliation["pageBacklog"],
            "checkpointLagSeconds": backlog["checkpointLagSeconds"],
            "redactedReplayRecords": current["redacted"] + backlog["redacted"] + reconciliation["redacted"],
        }

    def _sweep_current_hour(self, table, prefix, hour, now, *, content, item_limit, query_limit):
        result = {"deleted": 0, "queries": 0, "redacted": 0, "pageBacklog": False}
        for shard in range(min(16, query_limit)):
            if result["deleted"] >= item_limit:
                result["pageBacklog"] = True
                break
            page = self._query_expiration_bucket(
                table, prefix, hour, shard, now, content=content,
                limit=min(25, item_limit - result["deleted"]),
            )
            result["deleted"] += page["deleted"]
            result["queries"] += 1
            result["redacted"] += page["redacted"]
            result["pageBacklog"] = result["pageBacklog"] or page["hasMore"]
        if query_limit < 16:
            result["pageBacklog"] = True
        return result

    def _sweep_backlog(self, table, prefix, closed_hour, now, *, content, item_limit, query_limit):
        checkpoint = self._load_checkpoint("EXPIRATION", prefix, now, self.settings.lifecycle_start_epoch_hour)
        result = {"deleted": 0, "queries": 0, "redacted": 0, "checkpointBacklog": False, "checkpointLagSeconds": 0}
        while checkpoint["hourEpoch"] <= closed_hour and result["deleted"] < item_limit and result["queries"] < query_limit:
            page = self._query_expiration_bucket(
                table, prefix, checkpoint["hourEpoch"], checkpoint["shard"], now,
                content=content, limit=min(25, item_limit - result["deleted"]),
            )
            result["deleted"] += page["deleted"]
            result["queries"] += 1
            result["redacted"] += page["redacted"]
            if page["hasMore"]:
                result["checkpointBacklog"] = True
                break
            checkpoint = self._advance_checkpoint("EXPIRATION", prefix, checkpoint, now)
        if checkpoint["hourEpoch"] <= closed_hour:
            result["checkpointBacklog"] = True
            result["checkpointLagSeconds"] = closed_hour - checkpoint["hourEpoch"] + 3600
        return result

    def _sweep_reconciliation(self, table, prefix, closed_hour, now, *, content, item_limit, query_limit):
        result = {"deleted": 0, "queries": 0, "redacted": 0, "pageBacklog": False}
        if closed_hour < self.settings.lifecycle_start_epoch_hour or query_limit <= 0:
            return result
        window_start = max(
            self.settings.lifecycle_start_epoch_hour,
            closed_hour - (self.settings.expiration_reconciliation_hours - 1) * 3600,
        )
        window_bucket_count = ((closed_hour - window_start) // 3600 + 1) * 16
        query_limit = min(query_limit, window_bucket_count)
        checkpoint = self._load_checkpoint("RECONCILIATION", prefix, now, window_start)
        if checkpoint["hourEpoch"] < window_start or checkpoint["hourEpoch"] > closed_hour:
            checkpoint = self._reset_checkpoint("RECONCILIATION", prefix, checkpoint, window_start, now)
        while result["queries"] < query_limit and result["deleted"] < item_limit:
            page = self._query_expiration_bucket(
                table, prefix, checkpoint["hourEpoch"], checkpoint["shard"], now,
                content=content, limit=min(25, item_limit - result["deleted"]),
            )
            result["deleted"] += page["deleted"]
            result["queries"] += 1
            result["redacted"] += page["redacted"]
            result["pageBacklog"] = result["pageBacklog"] or page["hasMore"]
            if page["hasMore"]:
                continue
            checkpoint = self._advance_reconciliation_checkpoint(
                prefix, checkpoint, window_start, closed_hour, now
            )
        return result

    def _query_expiration_bucket(self, table, prefix, hour, shard, now, *, content, limit):
        bucket = f"{prefix}#{_hour_label(hour)}#{shard:02d}"
        page = table.query(
            IndexName=self.settings.expiration_index_name,
            KeyConditionExpression="expiryBucket = :bucket AND expiresAt <= :now",
            ExpressionAttributeValues={":bucket": bucket, ":now": now},
            ProjectionExpression="PK, SK, expiresAt", Limit=limit,
        )
        deleted = redacted = 0
        for item in page.get("Items") or []:
            try:
                table.delete_item(
                    Key={"PK": item["PK"], "SK": item["SK"]},
                    ConditionExpression="expiresAt <= :now",
                    ExpressionAttributeValues={":now": now},
                )
                deleted += 1
            except Exception as err:
                if _error_code(err) != "ConditionalCheckFailedException":
                    raise
            if content:
                redacted += self._expire_locator_and_replay(item, now)
        return {"deleted": deleted, "redacted": redacted, "hasMore": bool(page.get("LastEvaluatedKey"))}

    def _load_checkpoint(self, kind, prefix, now, initial_hour):
        key = {"PK": f"LIFECYCLE#{self.settings.environment}", "SK": f"{kind}#{prefix}"}
        item = self.control_table.get_item(Key=key, ConsistentRead=True).get("Item")
        if not item:
            initial = key | {
                "recordType": "LIFECYCLE_CHECKPOINT", "schemaVersion": self.settings.schema_version,
                "hourEpoch": initial_hour, "shard": 0,
                "stateVersion": 1, "updatedAtEpoch": now,
            }
            try:
                self.control_table.put_item(
                    Item=initial,
                    ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
                )
                item = initial
            except Exception as err:
                if _error_code(err) != "ConditionalCheckFailedException":
                    raise
                item = self.control_table.get_item(Key=key, ConsistentRead=True).get("Item")
        hour_epoch = _exact_nonnegative_int((item or {}).get("hourEpoch"))
        shard = _exact_nonnegative_int((item or {}).get("shard"))
        version = _exact_nonnegative_int((item or {}).get("stateVersion"))
        if (
            (item or {}).get("recordType") != "LIFECYCLE_CHECKPOINT"
            or hour_epoch is None or hour_epoch % 3600 != 0
            or hour_epoch > (now - (now % 3600)) + 3600
            or shard is None or shard > 15 or version is None or version < 1
        ):
            raise HistoryError("SERVER_UNAVAILABLE", "The expiration checkpoint is invalid.")
        return {"hourEpoch": hour_epoch, "shard": shard, "stateVersion": version}

    def _advance_checkpoint(self, kind, prefix, checkpoint, now):
        next_shard = checkpoint["shard"] + 1
        next_hour = checkpoint["hourEpoch"]
        if next_shard == 16:
            next_shard = 0
            next_hour += 3600
        next_version = checkpoint["stateVersion"] + 1
        self.control_table.update_item(
            Key={"PK": f"LIFECYCLE#{self.settings.environment}", "SK": f"{kind}#{prefix}"},
            UpdateExpression="SET hourEpoch = :hour, #shard = :shard, stateVersion = :next_version, updatedAtEpoch = :now",
            ConditionExpression="stateVersion = :previous_version AND hourEpoch = :previous_hour AND #shard = :previous_shard",
            ExpressionAttributeNames={"#shard": "shard"},
            ExpressionAttributeValues={
                ":hour": next_hour, ":shard": next_shard, ":next_version": next_version,
                ":now": now, ":previous_version": checkpoint["stateVersion"],
                ":previous_hour": checkpoint["hourEpoch"], ":previous_shard": checkpoint["shard"],
            },
        )
        return {"hourEpoch": next_hour, "shard": next_shard, "stateVersion": next_version}

    def _advance_reconciliation_checkpoint(self, prefix, checkpoint, window_start, closed_hour, now):
        next_shard = checkpoint["shard"] + 1
        next_hour = checkpoint["hourEpoch"]
        if next_shard == 16:
            next_shard = 0
            next_hour += 3600
        if next_hour > closed_hour:
            next_hour = window_start
        return self._write_checkpoint(
            "RECONCILIATION", prefix, checkpoint, next_hour, next_shard, now
        )

    def _reset_checkpoint(self, kind, prefix, checkpoint, hour, now):
        return self._write_checkpoint(kind, prefix, checkpoint, hour, 0, now)

    def _write_checkpoint(self, kind, prefix, checkpoint, hour, shard, now):
        next_version = checkpoint["stateVersion"] + 1
        self.control_table.update_item(
            Key={"PK": f"LIFECYCLE#{self.settings.environment}", "SK": f"{kind}#{prefix}"},
            UpdateExpression="SET hourEpoch = :hour, #shard = :shard, stateVersion = :next_version, updatedAtEpoch = :now",
            ConditionExpression="stateVersion = :previous_version AND hourEpoch = :previous_hour AND #shard = :previous_shard",
            ExpressionAttributeNames={"#shard": "shard"},
            ExpressionAttributeValues={
                ":hour": hour, ":shard": shard, ":next_version": next_version, ":now": now,
                ":previous_version": checkpoint["stateVersion"],
                ":previous_hour": checkpoint["hourEpoch"], ":previous_shard": checkpoint["shard"],
            },
        )
        return {"hourEpoch": hour, "shard": shard, "stateVersion": next_version}

    def _expire_locator_and_replay(self, content_item, now):
        request_id = _request_id_from_sort_key(content_item.get("SK"))
        account_id = _account_id_from_partition(content_item.get("PK"))
        generation = _generation_from_partition(content_item.get("PK"))
        if request_id is None or account_id is None or generation is None:
            raise HistoryError("SERVER_UNAVAILABLE", "An indexed History key is invalid.")
        redacted = self._redact_analysis_replay(
            account_id, request_id, now, require_response=True
        )
        try:
            self.control_table.update_item(
                Key={"PK": f"USER#{account_id}", "SK": f"REQUEST#{request_id}"},
                UpdateExpression="SET #status = :expired, deletedAtEpoch = :now REMOVE contentSortKey, lifecycleBucket, lifecycleAt",
                ConditionExpression="#status = :active AND historyGeneration = :generation AND contentSortKey = :content_sk AND contentExpiresAt <= :now",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":expired": "EXPIRED", ":active": "ACTIVE", ":now": now,
                    ":generation": generation,
                    ":content_sk": content_item["SK"],
                },
            )
        except Exception as err:
            if _error_code(err) != "ConditionalCheckFailedException":
                raise
        return redacted

    def _process_pending_jobs(self, now, limit):
        result = {
            "completedErasureJobs": 0, "overdueErasureJobs": 0,
            "completedRetentionPurges": 0,
            "observedPendingCompletions": 0, "oldestPendingCompletionAgeSeconds": None,
            "oldestPendingErasureAgeSeconds": None, "stuckPendingCompletions": 0,
            "redactedReplayRecords": 0, "worksetTruncated": limit <= 0,
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
                if not job or int(job.get("lifecycleAt", now + 1)) > now:
                    continue
                if job.get("recordType") == "REQUEST":
                    if job.get("status") != "ACTIVE":
                        continue
                    result["redactedReplayRecords"] += self._process_retention_job(job, now)
                    result["completedRetentionPurges"] += 1
                    continue
                if job.get("status") != "PENDING":
                    continue
                if job.get("recordType") == "COMPLETION":
                    age = max(0, now - int(job.get("acceptedAtEpoch", now)))
                    result["observedPendingCompletions"] += 1
                    result["oldestPendingCompletionAgeSeconds"] = max(
                        result["oldestPendingCompletionAgeSeconds"] or 0, age
                    )
                    if age >= self.settings.completion_stuck_seconds:
                        result["stuckPendingCompletions"] += 1
                    result["redactedReplayRecords"] += self._observe_completion(job, now)
                    continue
                if job.get("recordType") != "ERASURE":
                    raise HistoryError("SERVER_UNAVAILABLE", "A pending lifecycle record is invalid.")
                age = max(0, now - int(job.get("createdAtEpoch", now)))
                result["oldestPendingErasureAgeSeconds"] = max(
                    result["oldestPendingErasureAgeSeconds"] or 0, age
                )
                if int(job.get("deleteByEpoch", now)) < now:
                    result["overdueErasureJobs"] += 1
                complete, redacted = self._process_erasure_job(job, now)
                result["redactedReplayRecords"] += redacted
                if complete:
                    result["completedErasureJobs"] += 1
        if not result["worksetTruncated"]:
            result["oldestPendingCompletionAgeSeconds"] = result["oldestPendingCompletionAgeSeconds"] or 0
            result["oldestPendingErasureAgeSeconds"] = result["oldestPendingErasureAgeSeconds"] or 0
        return result

    def _process_retention_job(self, job, now):
        account_id = _account_id_from_user_pk(job.get("PK"))
        request_id = _request_id_from_locator_sort_key(job.get("SK"))
        generation = _exact_nonnegative_int(job.get("historyGeneration"))
        content_expires_at = _exact_nonnegative_int(job.get("contentExpiresAt"))
        content_sort_key = job.get("contentSortKey")
        if (
            account_id is None or request_id is None
            or job.get("requestId") != request_id or generation is None
            or content_expires_at is None or content_expires_at > now
            or _exact_nonnegative_int(job.get("lifecycleAt")) != content_expires_at
            or not isinstance(content_sort_key, str)
            or _request_id_from_sort_key(content_sort_key) != request_id
        ):
            raise HistoryError("SERVER_UNAVAILABLE", "A pending retention record is invalid.")
        try:
            self.content_table.delete_item(
                Key={"PK": f"USER#{account_id}#HISTORY#{generation}", "SK": content_sort_key},
                ConditionExpression="expiresAt <= :now",
                ExpressionAttributeValues={":now": now},
            )
        except Exception as err:
            if _error_code(err) != "ConditionalCheckFailedException":
                raise
        return self._expire_locator_and_replay(
            {"PK": f"USER#{account_id}#HISTORY#{generation}", "SK": content_sort_key}, now
        )

    def _observe_completion(self, job, now):
        account_id = _account_id_from_user_pk(job.get("PK"))
        generation = _exact_nonnegative_int(job.get("historyGeneration"))
        request_id = job.get("requestId")
        if account_id is None or generation is None or not isinstance(request_id, str):
            raise HistoryError("SERVER_UNAVAILABLE", "A pending completion record is invalid.")
        state = self.control_table.get_item(
            Key={"PK": f"USER#{account_id}", "SK": "STATE"}, ConsistentRead=True
        ).get("Item")
        stale = (
            not state or state.get("accountStatus") != "ACTIVE"
            or int(state.get("historyGeneration", -1)) != generation
        )
        if stale:
            redacted = self._redact_analysis_replay(
                account_id, request_id, now, generation=generation, force_cancel=True
            )
            self.control_table.update_item(
                Key={"PK": job["PK"], "SK": job["SK"]},
                UpdateExpression="SET #status = :cancelled, completedAtEpoch = :now, expiresAt = :expires_at, expiryBucket = :expiry_bucket REMOVE lifecycleBucket, lifecycleAt",
                ConditionExpression="#status = :pending AND acceptedSequence = :accepted_sequence",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":cancelled": "CANCELLED_ERASED", ":pending": "PENDING",
                    ":accepted_sequence": job["acceptedSequence"], ":now": now,
                    ":expires_at": now + self.settings.dedup_retention_days * 86400,
                    ":expiry_bucket": _control_expiry_bucket(
                        now + self.settings.dedup_retention_days * 86400, request_id
                    ),
                },
            )
            return redacted
        self.control_table.update_item(
            Key={"PK": job["PK"], "SK": job["SK"]},
            UpdateExpression="SET lifecycleAt = :next",
            ConditionExpression="#status = :pending AND lifecycleAt = :previous",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":next": now + self.settings.completion_recheck_seconds,
                ":pending": "PENDING", ":previous": job["lifecycleAt"],
            },
        )
        return 0

    def _process_erasure_job(self, job, now):
        if job.get("reason") == "ACCOUNT_DELETION":
            if current_command(self.deletion_ledger_table, self._account_command(job), now) == "TERMINAL":
                # Restored/stale jobs must not recreate receipt or replay state.
                # Retirement of this stale job is a separate restore-control task.
                return False, 0
        account_id = _account_id_from_user_pk(job.get("PK"))
        generation = _exact_nonnegative_int(job.get("historyGeneration"))
        max_generation = _exact_nonnegative_int(job.get("maxHistoryGeneration", generation))
        if account_id is None or generation is None or max_generation is None or generation > max_generation:
            raise HistoryError("SERVER_UNAVAILABLE", "A pending erasure job is invalid.")
        stage = job.get("stage", "HISTORY")
        if stage == "HISTORY":
            return self._process_history_erasure_stage(
                job, account_id, generation, max_generation, now
            ), 0
        if stage == "REPLAY":
            return self._process_replay_erasure_stage(job, account_id, generation, now)
        raise HistoryError("SERVER_UNAVAILABLE", "A pending erasure stage is invalid.")

    def _process_history_erasure_stage(self, job, account_id, generation, max_generation, now):
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
            self._reschedule_erasure(job, now, continuation=start_key["SK"])
            return False
        if generation < max_generation:
            self.control_table.update_item(
                Key={"PK": job["PK"], "SK": job["SK"]},
                UpdateExpression="SET historyGeneration = :next_generation, lifecycleAt = :next REMOVE continuationSortKey",
                ConditionExpression="#status = :pending AND historyGeneration = :generation",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":next_generation": generation + 1, ":next": now + 1,
                    ":pending": "PENDING", ":generation": generation,
                },
            )
            return False
        self.control_table.update_item(
            Key={"PK": job["PK"], "SK": job["SK"]},
            UpdateExpression="SET #stage = :replay, lifecycleAt = :next REMOVE continuationSortKey",
            ConditionExpression="#status = :pending AND historyGeneration = :generation",
            ExpressionAttributeNames={"#status": "status", "#stage": "stage"},
            ExpressionAttributeValues={
                ":replay": "REPLAY", ":next": now + 1,
                ":pending": "PENDING", ":generation": generation,
            },
        )
        return False

    def _process_replay_erasure_stage(self, job, account_id, generation, now):
        partition = f"ANALYSIS#REQUEST#{_account_hash(account_id)}"
        kwargs = {
            "KeyConditionExpression": "PK = :pk", "ExpressionAttributeValues": {":pk": partition},
            "ConsistentRead": True,
            "ProjectionExpression": (
                "PK, SK, #status, historyAuthorization, #response, payloadHash, "
                "expiresAt, #ttl"
            ),
            "ExpressionAttributeNames": {
                "#status": "status", "#response": "response", "#ttl": "ttl",
            },
            "Limit": self.settings.erasure_batch_size,
        }
        if job.get("continuationSortKey"):
            kwargs["ExclusiveStartKey"] = {"PK": partition, "SK": job["continuationSortKey"]}
        page = self.abuse_table.query(**kwargs)
        redacted = 0
        account_deletion = job.get("reason") == "ACCOUNT_DELETION"
        full_history_deletion = job.get("reason") in {
            "ACCOUNT_DELETION", "HISTORY_ACCOUNT_DELETION",
        }
        for item in page.get("Items") or []:
            authorization = item.get("historyAuthorization")
            old_generation = (
                isinstance(authorization, dict)
                and _exact_nonnegative_int(authorization.get("historyGeneration")) == generation
            )
            legacy_response = not isinstance(authorization, dict) and "response" in item
            if full_history_deletion or old_generation or legacy_response:
                redacted += self._redact_analysis_replay(
                    account_id, item["SK"], now,
                    generation=generation if old_generation else None,
                    require_response=True, account_deletion=full_history_deletion,
                    legacy=legacy_response, payload_hash=item.get("payloadHash"),
                    retention_anchor_epoch=(
                        _exact_nonnegative_int(job.get("deletionRequestedAtEpoch"))
                        or _exact_nonnegative_int(job.get("createdAtEpoch"))
                        or now
                    ),
                )
        start_key = page.get("LastEvaluatedKey")
        if start_key:
            self._reschedule_erasure(job, now, continuation=start_key["SK"])
            return False, redacted
        complete = self._complete_erasure(job, generation, now)
        return complete, redacted

    def _reschedule_erasure(self, job, now, *, continuation):
        self.control_table.update_item(
            Key={"PK": job["PK"], "SK": job["SK"]},
            UpdateExpression="SET continuationSortKey = :continuation, lifecycleAt = :next",
            ConditionExpression="#status = :pending AND historyGeneration = :generation",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":continuation": continuation, ":next": now + 1,
                ":pending": "PENDING", ":generation": job["historyGeneration"],
            },
        )

    def _complete_erasure(self, job, generation, now):
        reason = job.get("reason")
        account_id = _account_id_from_user_pk(job.get("PK"))
        if reason == "HISTORY_ACCOUNT_DELETION":
            self._finalize_account_state(
                account_id, "HISTORY_DELETING", "HISTORY_DELETED", now,
                also_accept={"DELETING", "DELETED"},
            )
            self._complete_pending_mutation(job, now)
        elif reason == "ACCOUNT_DELETION":
            if current_command(self.deletion_ledger_table, self._account_command(job), now) == "TERMINAL":
                return False
            self._finalize_account_state(account_id, "DELETING", "DELETED", now)
            result = self._write_account_deletion_receipt(job, now)
            if result["terminal"]:
                return False
        expires_at = now + self.settings.mutation_retention_days * 86400
        self.control_table.update_item(
            Key={"PK": job["PK"], "SK": job["SK"]},
            UpdateExpression="SET #status = :complete, completedAtEpoch = :now, expiresAt = :expires_at, expiryBucket = :expiry_bucket REMOVE lifecycleBucket, lifecycleAt, continuationSortKey",
            ConditionExpression="#status = :pending AND historyGeneration = :generation",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":complete": "COMPLETE", ":pending": "PENDING", ":now": now,
                ":generation": generation, ":expires_at": expires_at,
                ":expiry_bucket": _control_expiry_bucket(
                    expires_at, str(job.get("operationId") or job.get("SK"))
                ),
            },
        )

        return True

    def _finalize_account_state(self, account_id, previous, final, now, *, also_accept=frozenset()):
        try:
            self.control_table.update_item(
                Key={"PK": f"USER#{account_id}", "SK": "STATE"},
                UpdateExpression="SET accountStatus = :final, updatedAtEpoch = :now",
                ConditionExpression="accountStatus = :previous",
                ExpressionAttributeValues={
                    ":final": final, ":previous": previous, ":now": now,
                },
            )
        except Exception as err:
            if _error_code(err) != "ConditionalCheckFailedException":
                raise
            state = self.control_table.get_item(
                Key={"PK": f"USER#{account_id}", "SK": "STATE"}, ConsistentRead=True
            ).get("Item")
            if not state or state.get("accountStatus") not in ({final} | set(also_accept)):
                raise

    def _complete_pending_mutation(self, job, now):
        operation_id = job.get("operationId")
        expires_at = now + self.settings.mutation_retention_days * 86400
        try:
            self.control_table.update_item(
                Key={"PK": job["PK"], "SK": f"MUTATION#{operation_id}"},
                UpdateExpression="SET #status = :complete, completedAtEpoch = :now, expiresAt = :expires_at, expiryBucket = :expiry_bucket",
                ConditionExpression="#status = :pending AND operationId = :operation_id",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":complete": "COMPLETE", ":pending": "PENDING", ":now": now,
                    ":operation_id": operation_id, ":expires_at": expires_at,
                    ":expiry_bucket": _control_expiry_bucket(expires_at, operation_id),
                },
            )
        except Exception as err:
            if _error_code(err) != "ConditionalCheckFailedException":
                raise
            receipt = self.control_table.get_item(
                Key={"PK": job["PK"], "SK": f"MUTATION#{operation_id}"}, ConsistentRead=True
            ).get("Item")
            if not receipt or receipt.get("status") != "COMPLETE":
                raise

    def _account_command(self, job):
        account = _account_id_from_user_pk(job.get("PK"))
        requested = _exact_nonnegative_int(job.get("deletionRequestedAtEpoch"))
        if (account is None or job.get("deletionLedgerPK") != "ACCOUNT#" + account
                or job.get("deletionLedgerSK") != "ACCOUNT_DELETION" or requested is None
                or self.deletion_ledger_table is None):
            raise HistoryError("SERVER_UNAVAILABLE", "The account-deletion job binding is invalid.")
        return {"PK": "ACCOUNT#" + account, "SK": "ACCOUNT_DELETION", "schemaVersion": 1,
            "recordVersion": 1, "environment": self.settings.environment,
            "eventType": "account.deletion.requested", "accountId": account,
            "operationId": job.get("deletionOperationId"), "status": "REQUESTED",
            "occurredAtEpoch": requested, "deleteByEpoch": requested + 86400}

    def _write_account_deletion_receipt(self, job, now):
        if self.dynamodb_client is None:
            raise HistoryError("SERVER_UNAVAILABLE", "The deletion transaction client is unavailable.")
        command = self._account_command(job)
        # Lifecycle already establishes the DELETED state before this receipt;
        # bind that evidence and the exact requested fence atomically.
        guard = {"ConditionCheck": {
            "TableName": self.settings.control_table_name,
            "Key": {"PK": {"S": job["PK"]}, "SK": {"S": "STATE"}},
            "ConditionExpression": "accountStatus = :deleted",
            "ExpressionAttributeValues": {":deleted": {"S": "DELETED"}},
        }}
        return write_receipt(ledger=self.deletion_ledger_table, client=self.dynamodb_client,
            table_name=self.settings.deletion_ledger_table_name, command=command,
            now=now, guards=[guard])

    def _mark_locator_erased(self, account_id, generation, content, now):
        request_id = content.get("requestId") or _request_id_from_sort_key(content.get("SK"))
        if not request_id:
            raise HistoryError("SERVER_UNAVAILABLE", "An erasure History key is invalid.")
        try:
            self.control_table.update_item(
                Key={"PK": f"USER#{account_id}", "SK": f"REQUEST#{request_id}"},
                UpdateExpression="SET #status = :cleared, deletedAtEpoch = :now REMOVE contentSortKey, lifecycleBucket, lifecycleAt",
                ConditionExpression="historyGeneration = :generation AND contentSortKey = :content_sk",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":cleared": "CLEARED", ":now": now, ":generation": generation,
                    ":content_sk": content["SK"],
                },
            )
        except Exception as err:
            if _error_code(err) != "ConditionalCheckFailedException":
                raise

    def _redact_analysis_replay(
        self, account_id, request_id, now, *, generation=None,
        require_response=False, force_cancel=False, account_deletion=False,
        legacy=False, payload_hash=None, retention_anchor_epoch=None,
    ):
        if account_deletion:
            if not _is_payload_hash(payload_hash):
                raise HistoryError(
                    "SERVER_UNAVAILABLE", "The analysis replay hash is invalid."
                )
            anchor = (
                retention_anchor_epoch
                if _exact_nonnegative_int(retention_anchor_epoch) is not None
                else now
            )
            expires_at = anchor + self.settings.dedup_retention_days * 86400
            self.abuse_table.put_item(
                Item={
                    "PK": f"ANALYSIS#REQUEST#{_account_hash(account_id)}",
                    "SK": request_id,
                    "status": "COMPLETED_ERASED",
                    "payloadHash": payload_hash,
                    "expiresAt": expires_at,
                    "ttl": expires_at,
                },
                ConditionExpression="payloadHash = :payload_hash",
                ExpressionAttributeValues={":payload_hash": payload_hash},
            )
            return 1
        conditions = ["attribute_exists(PK)"]
        values = {
            ":erased": "COMPLETED_ERASED", ":updated": _iso(now),
            ":expires_at": now + self.settings.dedup_retention_days * 86400,
        }
        if generation is not None:
            conditions.append("historyAuthorization.historyGeneration = :generation")
            values[":generation"] = generation
        elif legacy:
            conditions.append("attribute_not_exists(historyAuthorization)")
        if require_response and not force_cancel:
            conditions.append("attribute_exists(#response)")
        if not account_deletion and generation is None and not legacy and not require_response:
            raise HistoryError("SERVER_UNAVAILABLE", "Replay redaction is not safely scoped.")
        try:
            self.abuse_table.update_item(
                Key={"PK": f"ANALYSIS#REQUEST#{_account_hash(account_id)}", "SK": request_id},
                UpdateExpression="SET #status = :erased, updatedAt = :updated, expiresAt = :expires_at, #ttl = :expires_at REMOVE #response",
                ConditionExpression=" AND ".join(conditions),
                ExpressionAttributeNames={"#status": "status", "#ttl": "ttl", "#response": "response"},
                ExpressionAttributeValues=values,
            )
            return 1
        except Exception as err:
            if _error_code(err) == "ConditionalCheckFailedException":
                return 0
            raise


def _hour_label(epoch):
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y%m%d%H")


def _is_payload_hash(value):
    return (
        isinstance(value, str) and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _request_id_from_sort_key(value):
    if not isinstance(value, str) or not value.startswith("COMPLETE#"):
        return None
    parts = value.split("#", 2)
    return parts[2] if len(parts) == 3 and parts[2] else None


def _request_id_from_locator_sort_key(value):
    if not isinstance(value, str) or not value.startswith("REQUEST#"):
        return None
    request_id = value[len("REQUEST#"):]
    return request_id or None


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
    hour = _hour_label(expires_at)
    shard = int(hashlib.sha256(value.encode()).hexdigest()[:2], 16) % 16
    return f"CONTROL#{hour}#{shard:02d}"


def _account_hash(account_id):
    return hashlib.sha256(account_id.encode("utf-8")).hexdigest()


def _iso(epoch):
    return datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


def _error_code(err):
    return getattr(err, "response", {}).get("Error", {}).get("Code")
