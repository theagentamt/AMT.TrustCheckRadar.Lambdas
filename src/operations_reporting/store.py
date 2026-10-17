"""Conditional DynamoDB adapter for unlinkable aggregate revisions only.

No resource creation, handler, producer, scheduler or notification is provided.
An injected one-attempt client and trusted clock are required. A transport error
after a write is UNKNOWN; this adapter never blindly retries it. Physical TTL,
backup exclusions and deployed IAM remain separate activation requirements.
"""

import hashlib
import json
import re

from botocore.exceptions import ClientError

from .reducer import CORRECTION_SECONDS, ProjectionFault, day_bounds
from .revisions import (
    MAX_RECORD_BYTES, RETENTION_SECONDS, RevisionFault, plan_revision,
    validate_revision, validate_summary,
)


_CODES = frozenset({
    "INVALID_CONFIGURATION", "INVALID_INPUT", "CLOCK_UNAVAILABLE",
    "READ_UNAVAILABLE", "INVALID_STORED_RECORD", "CONDITIONAL_CONFLICT",
    "WRITE_OUTCOME_UNKNOWN", "CORRECTION_WINDOW_CLOSED", "AGGREGATE_EXPIRED",
})


class AggregateStoreFault(ValueError):
    def __init__(self, code):
        self.code = code if code in _CODES else "INVALID_INPUT"
        super().__init__(self.code)


def _fail(code):
    raise AggregateStoreFault(code) from None


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _string(value):
    if type(value) is not dict or set(value) != {"S"} or type(value["S"]) is not str:
        _fail("INVALID_STORED_RECORD")
    return value["S"]


def _number(value):
    if (type(value) is not dict or set(value) != {"N"}
            or type(value["N"]) is not str
            or not re.fullmatch(r"0|[1-9][0-9]{0,13}", value["N"])):
        _fail("INVALID_STORED_RECORD")
    return int(value["N"])


def _s(value):
    return {"S": value}


def _n(value):
    return {"N": str(value)}


def _conditional_failure(error):
    try:
        reasons = error.response.get("CancellationReasons", [])
        if type(reasons) is not list or not reasons or len(reasons) > 2:
            return False
        codes = [r.get("Code") for r in reasons if type(r) is dict]
        return (error.response.get("Error", {}).get("Code") == "TransactionCanceledException"
                and len(codes) == len(reasons) and "ConditionalCheckFailed" in codes
                and all(c in ("None", "ConditionalCheckFailed") for c in codes))
    except Exception:
        return False


class AggregateStore:
    def __init__(self, client, table_name, *, environment, now):
        try:
            valid = (type(table_name) is str and re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", table_name)
                     and environment in ("dev", "uat", "prod") and callable(now)
                     and client.meta.config.retries.get("total_max_attempts") == 1)
        except Exception:
            valid = False
        if not valid:
            _fail("INVALID_CONFIGURATION")
        self.client, self.table_name = client, table_name
        self.environment, self.now = environment, now

    def _clock(self):
        try:
            value = self.now()
            if type(value) is not int or not 0 <= value <= 10**13:
                _fail("CLOCK_UNAVAILABLE")
            return value
        except Exception:
            _fail("CLOCK_UNAVAILABLE")

    def _scope(self, local_date):
        try:
            start, _ = day_bounds(local_date)
        except (ProjectionFault, TypeError, ValueError):
            _fail("INVALID_INPUT")
        return f"OPS_AGGREGATE#{self.environment}#{local_date}", start

    def _get(self, pk, sk):
        try:
            result = self.client.get_item(TableName=self.table_name,
                                          Key={"PK": _s(pk), "SK": _s(sk)}, ConsistentRead=True)
            if (type(result) is not dict or type(result.get("ResponseMetadata")) is not dict
                    or result["ResponseMetadata"].get("HTTPStatusCode") != 200):
                _fail("READ_UNAVAILABLE")
            item = result.get("Item")
            if item is not None and type(item) is not dict:
                _fail("READ_UNAVAILABLE")
            return item
        except Exception:
            _fail("READ_UNAVAILABLE")

    def _read(self, local_date, observed):
        pk, start = self._scope(local_date)
        head = self._get(pk, "HEAD")
        if head is None:
            return {"state": "MISSING", "record": None}
        if set(head) != {"PK", "SK", "kind", "revision", "recordSha256", "expiresAtEpoch"}:
            _fail("INVALID_STORED_RECORD")
        revision, expiry = _number(head["revision"]), _number(head["expiresAtEpoch"])
        digest = _string(head["recordSha256"])
        if (_string(head["PK"]) != pk or _string(head["SK"]) != "HEAD"
                or _string(head["kind"]) != "OPS_AGGREGATE_HEAD"
                or not 1 <= revision <= 2147483647 or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or expiry != start + RETENTION_SECONDS):
            _fail("INVALID_STORED_RECORD")
        if observed >= expiry:
            return {"state": "EXPIRED", "record": None}
        sk = f"REVISION#{revision:010d}"
        row = self._get(pk, sk)
        if type(row) is not dict or set(row) != {"PK", "SK", "kind", "recordJson", "expiresAtEpoch"}:
            _fail("INVALID_STORED_RECORD")
        wire = _string(row["recordJson"])
        try:
            wire_bytes = len(wire.encode("utf-8")) if len(wire) <= MAX_RECORD_BYTES else MAX_RECORD_BYTES + 1
        except UnicodeError:
            _fail("INVALID_STORED_RECORD")
        if (wire_bytes > MAX_RECORD_BYTES
                or _string(row["PK"]) != pk or _string(row["SK"]) != sk
                or _string(row["kind"]) != "OPS_AGGREGATE_REVISION"
                or _number(row["expiresAtEpoch"]) != expiry):
            _fail("INVALID_STORED_RECORD")
        try:
            record = validate_revision(json.loads(wire))
            if wire != _canonical(record):
                _fail("INVALID_STORED_RECORD")
        except (RevisionFault, ValueError, TypeError, RecursionError):
            _fail("INVALID_STORED_RECORD")
        if (record["revision"] != revision or record["recordSha256"] != digest
                or record["expiresAtEpoch"] != expiry
                or record["report"]["environment"] != self.environment
                or record["report"]["localDate"] != local_date):
            _fail("INVALID_STORED_RECORD")
        return {"state": "PRESENT", "record": record}

    def read(self, local_date):
        """Strong head read plus immutable revision read; expired is never zero."""
        observed = self._clock()
        result = self._read(local_date, observed)
        finished = self._clock()
        if finished < observed:
            _fail("CLOCK_UNAVAILABLE")
        if result["state"] == "PRESENT" and finished >= result["record"]["expiresAtEpoch"]:
            return {"state": "EXPIRED", "record": None}
        return result

    def commit(self, summary):
        """Plan against a trusted stored head and atomically append+advance it.

        One winner per prior hash. Conflicts need deliberate caller reconciliation;
        no retry/recompute loop occurs here. No deferred correction is stored yet.
        """
        try:
            report = validate_summary(summary)
        except RevisionFault:
            _fail("INVALID_INPUT")
        if report["environment"] != self.environment:
            _fail("INVALID_INPUT")
        observed = self._clock()
        pk, start = self._scope(report["localDate"])
        if observed >= start + RETENTION_SECONDS:
            _fail("AGGREGATE_EXPIRED")
        if observed >= start + CORRECTION_SECONDS:
            _fail("CORRECTION_WINDOW_CLOSED")
        old = self._read(report["localDate"], observed)["record"]
        plan = plan_revision(old, report, generated_at_epoch=observed)
        # Do not start a write after a slow read crosses the fixed correction fence.
        dispatch_time = self._clock()
        if dispatch_time < observed:
            _fail("CLOCK_UNAVAILABLE")
        if dispatch_time >= start + CORRECTION_SECONDS:
            _fail("CORRECTION_WINDOW_CLOSED")
        if plan["decision"] != "CREATE_REVISION":
            return plan
        record = plan["record"]
        expiry = record["expiresAtEpoch"]
        head = {"PK": _s(pk), "SK": _s("HEAD"), "kind": _s("OPS_AGGREGATE_HEAD"),
                "revision": _n(record["revision"]), "recordSha256": _s(record["recordSha256"]),
                "expiresAtEpoch": _n(expiry)}
        row = {"PK": _s(pk), "SK": _s(f"REVISION#{record['revision']:010d}"),
               "kind": _s("OPS_AGGREGATE_REVISION"), "recordJson": _s(_canonical(record)),
               "expiresAtEpoch": _n(expiry)}
        update = {"TableName": self.table_name, "Item": head}
        if old is None:
            update["ConditionExpression"] = "attribute_not_exists(PK)"
        else:
            update.update(ConditionExpression="#h = :old AND #r = :rev AND #e = :expiry",
                          ExpressionAttributeNames={"#h": "recordSha256", "#r": "revision", "#e": "expiresAtEpoch"},
                          ExpressionAttributeValues={":old": _s(old["recordSha256"]),
                                                     ":rev": _n(old["revision"]), ":expiry": _n(expiry)})
        transaction = [{"Put": {"TableName": self.table_name, "Item": row,
                                "ConditionExpression": "attribute_not_exists(PK)"}}, {"Put": update}]
        token = hashlib.sha256((self.table_name + "\0" + pk + "\0" + record["recordSha256"]).encode()).hexdigest()[:36]
        try:
            response = self.client.transact_write_items(TransactItems=transaction, ClientRequestToken=token)
            if (type(response) is not dict or type(response.get("ResponseMetadata")) is not dict
                    or response["ResponseMetadata"].get("HTTPStatusCode") != 200):
                _fail("WRITE_OUTCOME_UNKNOWN")
        except ClientError as error:
            # Conditional failures are a known losing CAS. A mixed/service/transport
            # failure remains unknown; cancellation details are never surfaced.
            if _conditional_failure(error):
                _fail("CONDITIONAL_CONFLICT")
            _fail("WRITE_OUTCOME_UNKNOWN")
        except Exception:
            _fail("WRITE_OUTCOME_UNKNOWN")
        return {**plan, "decision": "COMMITTED"}
