from dataclasses import dataclass
import base64
import hmac
import json
import re
import time

from shared_governed_history import INDEX_NAME, RETENTION_SECONDS, VERSION, present_projection
from shared_governed_history.projection import RESULT, validate_stored_receipt


class GovernedHistoryError(Exception):
    def __init__(self, code, status):
        super().__init__(code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class Settings:
    index_name: str
    cursor_ttl_seconds: int
    retention_seconds: int
    default_page_size: int
    max_page_size: int
    max_response_bytes: int

    @classmethod
    def from_env(cls):
        import os
        try:
            value = cls(
                index_name=os.environ["GOVERNED_HISTORY_INDEX_NAME"],
                cursor_ttl_seconds=int(os.environ["GOVERNED_HISTORY_CURSOR_TTL_SECONDS"]),
                retention_seconds=int(os.environ["GOVERNED_HISTORY_RETENTION_SECONDS"]),
                default_page_size=int(os.environ["GOVERNED_HISTORY_DEFAULT_PAGE_SIZE"]),
                max_page_size=int(os.environ["GOVERNED_HISTORY_MAX_PAGE_SIZE"]),
                max_response_bytes=int(os.environ["GOVERNED_HISTORY_MAX_RESPONSE_BYTES"]),
            )
        except Exception:
            raise GovernedHistoryError("SERVICE_NOT_ENABLED", 503) from None
        if (value.index_name != INDEX_NAME or value.cursor_ttl_seconds != 900
                or value.retention_seconds != RETENTION_SECONDS or value.default_page_size != 20
                or value.max_page_size != 50 or value.max_response_bytes != 262144):
            raise GovernedHistoryError("SERVICE_NOT_ENABLED", 503)
        return value


class Cursor:
    def __init__(self, authority, *, account, fingerprint, device_version, inventory_revision, now):
        self.a, self.account, self.fingerprint = authority, account, fingerprint
        self.device_version, self.inventory_revision, self.now = device_version, inventory_revision, now

    def encode(self, before):
        key_id = self.a.s.active_key_id
        payload = {
            "schemaVersion": 1, "transportVersion": VERSION, "keyId": key_id,
            "accountBinding": self.a._mac(key_id, "governed-history-cursor-account", self.account),
            "deviceBinding": self.a._mac(key_id, "governed-history-cursor-device", self.fingerprint),
            "deviceVersion": self.device_version, "inventoryRevision": self.inventory_revision,
            "before": before, "expiresAtEpoch": self.now + 900,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        tag = self.a._mac(key_id, "governed-history-cursor", encoded)
        return f"ghc1.{encoded}.{tag}"

    def decode(self, token):
        try:
            if not isinstance(token, str) or len(token) > 2048:
                raise ValueError()
            prefix, encoded, tag = token.split(".")
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            def unique_object(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError()
                    value[key] = item
                return value

            value = json.loads(raw, object_pairs_hook=unique_object)
            key_id = value["keyId"]
            expected = self.a._mac(key_id, "governed-history-cursor", encoded)
            exact = {"schemaVersion", "transportVersion", "keyId", "accountBinding", "deviceBinding",
                     "deviceVersion", "inventoryRevision", "before", "expiresAtEpoch"}
            if (prefix != "ghc1" or not hmac.compare_digest(tag, expected) or set(value) != exact
                    or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
                    or value["transportVersion"] != VERSION or key_id not in self.a.s.hmac_keys
                    or not isinstance(value["accountBinding"], str)
                    or not hmac.compare_digest(value["accountBinding"], self.a._mac(key_id, "governed-history-cursor-account", self.account))
                    or not isinstance(value["deviceBinding"], str)
                    or not hmac.compare_digest(value["deviceBinding"], self.a._mac(key_id, "governed-history-cursor-device", self.fingerprint))
                    or type(value["deviceVersion"]) is not int or value["deviceVersion"] != self.device_version
                    or type(value["inventoryRevision"]) is not int or value["inventoryRevision"] != self.inventory_revision
                    or type(value["expiresAtEpoch"]) is not int or value["expiresAtEpoch"] <= self.now
                    or not isinstance(value["before"], str)
                    or not re.fullmatch(r"GOVERNED#[0-9]{12}#[0-9a-f]{32}", value["before"])):
                raise ValueError()
            return value["before"]
        except Exception:
            raise GovernedHistoryError("INVALID_CURSOR", 422) from None


class Service:
    MAX_CANONICAL_READS = 20
    INDEX_PROJECTION = "PK, SK, recordType, #state, governedHistory, expiresAt, GSI2PK, GSI2SK"
    CANONICAL_PROJECTION = "PK, SK, recordType, #state, governedHistory, governedHistoryDigest, expiresAt, retentionDeadlineEpoch, GSI2PK, GSI2SK"

    def __init__(self, authority, settings, *, account, fingerprint, device_version,
                 inventory_revision, now=lambda: int(time.time())):
        self.a, self.s, self.account, self.now = authority, settings, account, now
        if type(inventory_revision) is not int or inventory_revision < 1:
            raise GovernedHistoryError("SERVICE_NOT_ENABLED", 503)
        self.partitions = authority.deletion_partitions(account)
        self.cursor = Cursor(authority, account=account, fingerprint=fingerprint,
                             device_version=device_version, inventory_revision=inventory_revision, now=now())

    def _limit(self, raw):
        if raw is None:
            return self.s.default_page_size
        if not isinstance(raw, str) or not raw.isascii() or not raw.isdigit():
            raise GovernedHistoryError("INVALID_REQUEST", 422)
        value = int(raw)
        if not 1 <= value <= self.s.max_page_size:
            raise GovernedHistoryError("INVALID_REQUEST", 422)
        return value

    def _query(self, partition, *, before=None, exact=None, limit=51):
        values = {":partition": partition}
        condition = "GSI2PK = :partition"
        if before is not None:
            condition += " AND GSI2SK < :before"; values[":before"] = before
        if exact is not None:
            condition += " AND GSI2SK = :exact"; values[":exact"] = exact
        return self.a.ddb.Table(self.a.s.authority_table).query(
            IndexName=self.s.index_name, KeyConditionExpression=condition,
            ExpressionAttributeValues=values, ProjectionExpression=self.INDEX_PROJECTION,
            ExpressionAttributeNames={"#state": "state"}, Select="SPECIFIC_ATTRIBUTES",
            ScanIndexForward=False, Limit=limit)

    def _canonical(self, candidate):
        try:
            if (type(candidate) is not dict or candidate.get("PK") not in self.partitions
                    or not isinstance(candidate.get("SK"), str)):
                return None
            row = self.a.ddb.Table(self.a.s.authority_table).get_item(
                Key={"PK": candidate["PK"], "SK": candidate["SK"]}, ConsistentRead=True,
                ProjectionExpression=self.CANONICAL_PROJECTION,
                ExpressionAttributeNames={"#state": "state"}).get("Item")
            if row is None or row.get("GSI2PK") != candidate.get("GSI2PK") or row.get("GSI2SK") != candidate.get("GSI2SK"):
                return None
            return present_projection(validate_stored_receipt(row, now=self.now()), self.now())
        except Exception:
            return None

    def _candidate(self, item):
        if type(item) is not dict:
            return None
        keys = ("PK", "SK", "GSI2PK", "GSI2SK")
        if any(not isinstance(item.get(key), str) for key in keys):
            return None
        return {key: item[key] for key in keys}

    def list(self, *, cursor=None, requested_limit=None):
        limit = self._limit(requested_limit)
        before = self.cursor.decode(cursor) if cursor else None
        candidates, trailing = [], False
        for partition in self.partitions:
            page = self._query(partition, before=before,
                               limit=min(limit + 1, self.MAX_CANONICAL_READS + 1))
            for item in page.get("Items", []):
                candidate = self._candidate(item)
                if candidate is not None:
                    candidates.append(candidate)
            trailing = trailing or bool(page.get("LastEvaluatedKey"))
        candidates.sort(key=lambda item: item.get("GSI2SK", ""), reverse=True)
        items, examined, examined_count = [], None, 0
        for candidate in candidates:
            if examined_count == self.MAX_CANONICAL_READS:
                break
            examined_count += 1
            examined = candidate.get("GSI2SK")
            public = self._canonical(candidate)
            if public is not None:
                items.append(public)
            if len(items) == limit:
                break
        more = examined is not None and (trailing or len(candidates) > examined_count)
        next_cursor = self.cursor.encode(examined) if more else None
        result = {"transportVersion": VERSION, "serverTimeEpoch": self.now(),
                  "items": items, "nextCursor": next_cursor}
        if len(json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode()) > self.s.max_response_bytes:
            raise GovernedHistoryError("SERVICE_UNAVAILABLE", 503)
        return result

    def detail(self, result_id):
        match = RESULT.fullmatch(result_id) if isinstance(result_id, str) else None
        if not match:
            raise GovernedHistoryError("INVALID_REQUEST", 422)
        sort = f"GOVERNED#{int(match.group(1)):012d}#{match.group(2)}"
        found = []
        for partition in self.partitions:
            for candidate in self._query(partition, exact=sort, limit=2).get("Items", []):
                public = self._canonical(self._candidate(candidate))
                if public is not None and public["resultId"] == result_id:
                    found.append(public)
        if not found:
            raise GovernedHistoryError("NOT_FOUND", 404)
        if len(found) != 1:
            raise GovernedHistoryError("SERVICE_UNAVAILABLE", 503)
        result = {"transportVersion": VERSION, "serverTimeEpoch": self.now(), "result": found[0]}
        if len(json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode()) > self.s.max_response_bytes:
            raise GovernedHistoryError("SERVICE_UNAVAILABLE", 503)
        return result
