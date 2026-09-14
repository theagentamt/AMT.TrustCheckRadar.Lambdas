from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import time

from shared_history.errors import HistoryError


class HistoryMutationService:
    def __init__(self, *, settings, control_table, dynamodb_client, now=lambda: int(time.time())):
        self.settings = settings
        self.control_table = control_table
        self.client = dynamodb_client
        self.now = now

    def delete_one(self, account_id, request_id, operation_id):
        previous = self._receipt(account_id, operation_id, "delete_one", request_id)
        if previous:
            return self._public_receipt(previous)
        state = self._state(account_id)
        locator = self.control_table.get_item(
            Key={"PK": f"USER#{account_id}", "SK": f"REQUEST#{request_id}"}, ConsistentRead=True
        ).get("Item")
        now = self.now()
        receipt = self._receipt_item(account_id, operation_id, "delete_one", request_id, now)
        items = [{"Put": self._put(self.settings.control_table_name, receipt)}]
        if locator and locator.get("status") == "ACTIVE" and int(locator.get("historyGeneration", -1)) == int(state["historyGeneration"]):
            content_key = {
                "PK": f"USER#{account_id}#HISTORY#{int(state['historyGeneration'])}",
                "SK": locator.get("contentSortKey"),
            }
            items.insert(0, {"Delete": {
                "TableName": self.settings.content_table_name,
                "Key": _serialize(content_key),
            }})
            items.insert(1, {"Update": {
                "TableName": self.settings.control_table_name,
                "Key": _serialize({"PK": f"USER#{account_id}", "SK": f"REQUEST#{request_id}"}),
                "UpdateExpression": "SET #status = :deleted, deletedAtEpoch = :now REMOVE contentSortKey",
                "ConditionExpression": "#status = :active AND historyGeneration = :generation",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": _serialize({":deleted": "DELETED", ":active": "ACTIVE", ":now": now, ":generation": int(state["historyGeneration"])}),
            }})
            items.insert(2, {"Update": {
                "TableName": self.settings.analysis_abuse_table_name,
                "Key": _serialize({"PK": f"ANALYSIS#REQUEST#{_account_hash(account_id)}", "SK": request_id}),
                "UpdateExpression": "SET #status = :erased, updatedAt = :updated, expiresAt = :expires_at, #ttl = :expires_at REMOVE #response",
                "ConditionExpression": "attribute_not_exists(payloadHash) OR payloadHash = :payload_hash",
                "ExpressionAttributeNames": {"#status": "status", "#response": "response", "#ttl": "ttl"},
                "ExpressionAttributeValues": _serialize({":erased": "COMPLETED_ERASED", ":updated": _iso(now), ":payload_hash": locator.get("payloadHash", ""), ":expires_at": now + self.settings.dedup_retention_days * 86400}),
            }})
        self._transact(items)
        return self._public_receipt(receipt)

    def clear_history(self, account_id, operation_id):
        previous = self._receipt(account_id, operation_id, "clear_history", None)
        if previous:
            return self._public_receipt(previous)
        state = self._state(account_id)
        now = self.now()
        old_generation = int(state["historyGeneration"])
        new_generation = old_generation + 1
        receipt = self._receipt_item(account_id, operation_id, "clear_history", None, now)
        receipt["historyGeneration"] = new_generation
        job = self._erasure_job(account_id, operation_id, "CLEAR_HISTORY", old_generation, now)
        items = [
            {"Update": {
                "TableName": self.settings.control_table_name,
                "Key": _serialize({"PK": f"USER#{account_id}", "SK": "STATE"}),
                "UpdateExpression": "SET historyGeneration = :new, clearAfterAcceptedSequence = acceptedSequence, updatedAtEpoch = :now",
                "ConditionExpression": "accountStatus = :active AND historyGeneration = :old",
                "ExpressionAttributeValues": _serialize({":new": new_generation, ":old": old_generation, ":now": now, ":active": "ACTIVE"}),
            }},
            {"Put": self._put(self.settings.control_table_name, job)},
            {"Put": self._put(self.settings.control_table_name, receipt)},
        ]
        self._transact(items)
        return self._public_receipt(receipt)

    def reset_progress(self, account_id, operation_id):
        self.settings.validate_recognition()
        previous = self._receipt(account_id, operation_id, "reset_progress", None)
        if previous:
            return self._public_receipt(previous)
        state = self._state(account_id)
        now = self.now()
        old_generation = int(state["recognitionGeneration"])
        new_generation = old_generation + 1
        receipt = self._receipt_item(account_id, operation_id, "reset_progress", None, now)
        receipt["recognitionGeneration"] = new_generation
        progress = {
            "PK": f"USER#{account_id}", "SK": f"PROGRESS#{new_generation}",
            "recordType": "PROGRESS", "schemaVersion": self.settings.schema_version,
            "recognitionGeneration": new_generation, "qualifyingChecks": 0,
            "awardedBadgeIds": [], "stateVersion": 1, "updatedAtEpoch": now,
        }
        self._transact([
            {"Update": {
                "TableName": self.settings.control_table_name,
                "Key": _serialize({"PK": f"USER#{account_id}", "SK": "STATE"}),
                "UpdateExpression": "SET recognitionGeneration = :new, resetAfterAcceptedSequence = acceptedSequence, updatedAtEpoch = :now",
                "ConditionExpression": "accountStatus = :active AND recognitionGeneration = :old",
                "ExpressionAttributeValues": _serialize({":new": new_generation, ":old": old_generation, ":now": now, ":active": "ACTIVE"}),
            }},
            {"Put": self._put(self.settings.control_table_name, progress)},
            {"Put": self._put(self.settings.control_table_name, receipt)},
        ])
        return self._public_receipt(receipt)

    def _state(self, account_id):
        state = self.control_table.get_item(
            Key={"PK": f"USER#{account_id}", "SK": "STATE"}, ConsistentRead=True
        ).get("Item")
        if not state or state.get("accountStatus") != "ACTIVE":
            raise HistoryError("SERVER_UNAVAILABLE", "The account History state is unavailable.")
        for field in ("historyGeneration", "recognitionGeneration", "acceptedSequence"):
            value = state.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, Decimal)) or int(value) != value or int(value) < 0:
                raise HistoryError("SERVER_UNAVAILABLE", "The account History state is invalid.")
        return state

    def _receipt(self, account_id, operation_id, operation, target):
        item = self.control_table.get_item(
            Key={"PK": f"USER#{account_id}", "SK": f"MUTATION#{operation_id}"}, ConsistentRead=True
        ).get("Item")
        if not item:
            return None
        if item.get("operation") != operation or item.get("targetRequestId") != target:
            raise HistoryError("CONFLICT", "The operationId is already bound to a different mutation.")
        return item

    def _receipt_item(self, account_id, operation_id, operation, target, now):
        item = {
            "PK": f"USER#{account_id}", "SK": f"MUTATION#{operation_id}",
            "recordType": "MUTATION", "schemaVersion": self.settings.schema_version,
            "operationId": operation_id, "operation": operation, "status": "COMPLETE",
            "completedAtEpoch": now,
            "expiresAt": now + self.settings.mutation_retention_days * 86400,
        }
        item["expiryBucket"] = _expiry_bucket(item["expiresAt"], operation_id)
        if target is not None:
            item["targetRequestId"] = target
        return item

    def _erasure_job(self, account_id, operation_id, reason, generation, now):
        shard = int(hashlib.sha256(operation_id.encode()).hexdigest()[:2], 16) % 16
        return {
            "PK": f"USER#{account_id}", "SK": f"ERASURE#{operation_id}",
            "recordType": "ERASURE", "schemaVersion": self.settings.schema_version,
            "operationId": operation_id, "reason": reason, "status": "PENDING",
            "stage": "HISTORY",
            "historyGeneration": generation, "createdAtEpoch": now,
            "deleteByEpoch": now + self.settings.erasure_sla_hours * 3600,
            "lifecycleBucket": f"PENDING#{shard:02d}", "lifecycleAt": now,
        }

    def _put(self, table_name, item):
        return {"TableName": table_name, "Item": _serialize(item), "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)"}

    def _transact(self, items):
        try:
            self.client.transact_write_items(TransactItems=items)
        except Exception as err:
            if getattr(err, "response", {}).get("Error", {}).get("Code") == "TransactionCanceledException":
                raise HistoryError("CONFLICT", "History state changed during the mutation.", retryable=True) from err
            raise

    @staticmethod
    def _public_receipt(item):
        result = {
            "schemaVersion": int(item["schemaVersion"]), "operationId": item["operationId"],
            "operation": item["operation"], "status": item["status"],
            "completedAtEpoch": int(item["completedAtEpoch"]),
        }
        for key in ("targetRequestId", "historyGeneration", "recognitionGeneration"):
            if key in item:
                result[key] = int(item[key]) if key.endswith("Generation") else item[key]
        return result


def _serialize(item):
    return {key: _serialize_value(value) for key, value in item.items()}


def _serialize_value(value):
    if value is None:
        return {"NULL": True}
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, (int, float, Decimal)):
        return {"N": str(value)}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, list):
        return {"L": [_serialize_value(item) for item in value]}
    if isinstance(value, dict):
        return {"M": _serialize(value)}
    raise TypeError(f"Unsupported DynamoDB value type: {type(value).__name__}")


def _account_hash(account_id):
    return hashlib.sha256(account_id.encode("utf-8")).hexdigest()


def _iso(epoch):
    return datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


def _expiry_bucket(expires_at, value):
    hour = datetime.fromtimestamp(expires_at, UTC).strftime("%Y%m%d%H")
    shard = int(hashlib.sha256(value.encode()).hexdigest()[:2], 16) % 16
    return f"CONTROL#{hour}#{shard:02d}"
