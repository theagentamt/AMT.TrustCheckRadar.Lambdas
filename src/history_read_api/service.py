from decimal import Decimal
import base64
import hashlib
import json
import secrets
import time
import hmac
from datetime import UTC, datetime

from shared_history.contracts import public_history_item
from shared_history.errors import HistoryError
from shared_history.security import subject_binding


class CursorStore:
    def __init__(self, *, table, secret: bytes, ttl_seconds: int, now=lambda: int(time.time())):
        if len(secret) < 32:
            raise HistoryError("SERVER_UNAVAILABLE", "The History cursor secret is too short.")
        self.table = table
        self.secret = secret
        self.ttl_seconds = ttl_seconds
        self.now = now

    @classmethod
    def from_aws(cls, settings, table):
        import boto3
        value = boto3.client("secretsmanager").get_secret_value(SecretId=settings.cursor_secret_name)
        raw = value.get("SecretString")
        if raw is None and value.get("SecretBinary") is not None:
            raw = base64.b64decode(value["SecretBinary"]).decode("utf-8")
        if not isinstance(raw, str):
            raise HistoryError("SERVER_UNAVAILABLE", "The History cursor secret is unavailable.")
        return cls(table=table, secret=raw.encode("utf-8"), ttl_seconds=settings.cursor_ttl_seconds)

    def create(self, account_id: str, generation: int, last_sort_key: str) -> str:
        for _attempt in range(3):
            handle = secrets.token_urlsafe(32)
            digest = hashlib.sha256(handle.encode("ascii")).hexdigest()
            now = self.now()
            expires_at = now + self.ttl_seconds
            try:
                self.table.put_item(
                    Item={
                        "PK": f"CURSOR#{digest}",
                        "SK": "CURSOR",
                        "recordType": "CURSOR",
                        "subjectBinding": subject_binding(account_id, self.secret),
                        "historyGeneration": generation,
                        "lastSortKey": last_sort_key,
                        "expiresAt": expires_at,
                        "ttl": expires_at,
                        "expiryBucket": _cursor_expiry_bucket(expires_at, digest),
                    },
                    ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
                )
                return handle
            except Exception as err:
                if _error_code(err) != "ConditionalCheckFailedException":
                    raise
        raise HistoryError("SERVER_UNAVAILABLE", "A unique History cursor could not be created.")

    def load(self, handle: str, account_id: str, generation: int) -> str:
        if not isinstance(handle, str) or len(handle) < 32 or len(handle) > 128:
            raise HistoryError("INVALID_REQUEST", "The History cursor is invalid.")
        digest = hashlib.sha256(handle.encode("utf-8")).hexdigest()
        item = self.table.get_item(
            Key={"PK": f"CURSOR#{digest}", "SK": "CURSOR"}, ConsistentRead=True
        ).get("Item")
        expected = subject_binding(account_id, self.secret)
        if (
            not item
            or not hmac.compare_digest(str(item.get("subjectBinding", "")), expected)
            or int(item.get("historyGeneration", -1)) != generation
            or int(item.get("expiresAt", 0)) <= self.now()
            or not isinstance(item.get("lastSortKey"), str)
        ):
            raise HistoryError("INVALID_REQUEST", "The History cursor is invalid or expired.")
        return item["lastSortKey"]


class HistoryReadService:
    def __init__(self, *, settings, content_table, control_table, cursor_store, now=lambda: int(time.time())):
        self.settings = settings
        self.content_table = content_table
        self.control_table = control_table
        self.cursor_store = cursor_store
        self.now = now

    def list_history(self, account_id, *, cursor=None, requested_limit=None):
        state = self._state(account_id)
        generation = int(state["historyGeneration"])
        limit = self._limit(requested_limit)
        partition = f"USER#{account_id}#HISTORY#{generation}"
        exclusive_start = None
        if cursor:
            last_sort_key = self.cursor_store.load(cursor, account_id, generation)
            exclusive_start = {"PK": partition, "SK": last_sort_key}
        items = []
        more = False
        last_included = None
        while len(items) < limit:
            kwargs = {
                "KeyConditionExpression": "PK = :pk",
                "ExpressionAttributeValues": {":pk": partition},
                "ScanIndexForward": False,
                "ConsistentRead": True,
                "Limit": min(limit - len(items) + 1, self.settings.max_page_size + 1),
            }
            if exclusive_start:
                kwargs["ExclusiveStartKey"] = exclusive_start
            page = self.content_table.query(**kwargs)
            raw_items = page.get("Items") or []
            for raw in raw_items:
                if int(raw.get("expiresAt", 0)) <= self.now():
                    continue
                public = public_history_item(raw, self.settings)
                candidate = items + [public]
                if _encoded_size({"items": candidate}) > self.settings.max_response_bytes:
                    if not items:
                        raise HistoryError("SERVER_UNAVAILABLE", "A stored History record exceeds the approved response bound.")
                    more = True
                    break
                if len(items) == limit:
                    more = True
                    break
                items.append(public)
                last_included = raw["SK"]
            if more:
                break
            lek = page.get("LastEvaluatedKey")
            if not lek:
                break
            exclusive_start = lek
        next_cursor = self.cursor_store.create(account_id, generation, last_included) if more and last_included else None
        result = {"schemaVersion": self.settings.schema_version, "items": items}
        if next_cursor:
            result["nextCursor"] = next_cursor
        return result

    def get_history(self, account_id, request_id):
        state = self._state(account_id)
        generation = int(state["historyGeneration"])
        locator = self.control_table.get_item(
            Key={"PK": f"USER#{account_id}", "SK": f"REQUEST#{request_id}"},
            ConsistentRead=True,
        ).get("Item")
        if (
            not locator
            or locator.get("status") != "ACTIVE"
            or int(locator.get("historyGeneration", -1)) != generation
            or int(locator.get("contentExpiresAt", 0)) <= self.now()
        ):
            raise HistoryError("NOT_FOUND", "The History record was not found.")
        item = self.content_table.get_item(
            Key={
                "PK": f"USER#{account_id}#HISTORY#{generation}",
                "SK": locator.get("contentSortKey"),
            },
            ConsistentRead=True,
        ).get("Item")
        if not item or int(item.get("expiresAt", 0)) <= self.now():
            raise HistoryError("NOT_FOUND", "The History record was not found.")
        return {"schemaVersion": self.settings.schema_version, "item": public_history_item(item, self.settings)}

    def get_progress(self, account_id):
        state = self._state(account_id)
        generation = int(state["recognitionGeneration"])
        progress = self.control_table.get_item(
            Key={"PK": f"USER#{account_id}", "SK": f"PROGRESS#{generation}"},
            ConsistentRead=True,
        ).get("Item")
        if not progress or int(progress.get("recognitionGeneration", -1)) != generation:
            raise HistoryError("SERVER_UNAVAILABLE", "Recognition progress is not initialized.")
        awarded = progress.get("awardedBadgeIds") or []
        catalog_ids = {item["id"] for item in self.settings.badge_catalog}
        count = _exact_nonnegative_int(progress.get("qualifyingChecks"))
        expected_awarded = [
            item["id"] for item in self.settings.badge_catalog if count is not None and item["threshold"] <= count
        ]
        if (
            count is None
            or not isinstance(awarded, list)
            or any(value not in catalog_ids for value in awarded)
            or awarded != expected_awarded
        ):
            raise HistoryError("SERVER_UNAVAILABLE", "Stored recognition progress is invalid.")
        return {
            "schemaVersion": self.settings.schema_version,
            "qualifyingChecks": count,
            "awardedBadgeIds": awarded,
        }

    def _state(self, account_id):
        state = self.control_table.get_item(
            Key={"PK": f"USER#{account_id}", "SK": "STATE"}, ConsistentRead=True
        ).get("Item")
        if (
            not state
            or state.get("accountStatus") != "ACTIVE"
            or isinstance(state.get("historyGeneration"), bool)
            or not isinstance(state.get("historyGeneration"), (int, Decimal))
            or isinstance(state.get("recognitionGeneration"), bool)
            or not isinstance(state.get("recognitionGeneration"), (int, Decimal))
        ):
            raise HistoryError("SERVER_UNAVAILABLE", "The account History state is unavailable.")
        return state

    def _limit(self, raw):
        if raw is None or raw == "":
            return self.settings.default_page_size
        try:
            value = int(raw)
        except (TypeError, ValueError) as err:
            raise HistoryError("INVALID_REQUEST", "The History page limit is invalid.") from err
        if value < 1 or value > self.settings.max_page_size:
            raise HistoryError("INVALID_REQUEST", "The History page limit is invalid.")
        return value


def _encoded_size(value):
    return len(json.dumps(value, separators=(",", ":"), default=_json_default).encode("utf-8"))


def _json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else float(value)
    raise TypeError


def _cursor_expiry_bucket(expires_at, digest):
    hour = datetime.fromtimestamp(expires_at, UTC).strftime("%Y%m%d%H")
    shard = int(digest[:2], 16) % 16
    return f"CONTROL#{hour}#{shard:02d}"


def _exact_nonnegative_int(value):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    integer = int(value)
    return integer if integer == value and integer >= 0 else None


def _error_code(err):
    return getattr(err, "response", {}).get("Error", {}).get("Code")
