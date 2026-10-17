"""Source-only immutable aggregate revision planning; no store or send operation.

The returned expectedPreviousSha256 is a future atomic-store precondition, not
an implemented compare-and-swap. Only unlinkable, schema-checked aggregates are
allowed. Current kernel producer completeness remains explicitly unqualified.
"""

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator

from .reducer import (
    ACCESS_CLASSES, CORRECTION_SECONDS, OUTCOMES, PROVIDERS, SERVICES,
    TIMEZONE, ProjectionFault, day_bounds,
)


RETENTION_SECONDS = 90 * 86400
MAX_RECORD_BYTES = 65536
_FAULTS = frozenset({"INVALID_SUMMARY", "INVALID_PREVIOUS_REVISION", "INVALID_RUN",
                     "CORRECTION_WINDOW_CLOSED", "AGGREGATE_EXPIRED",
                     "AGGREGATE_REGRESSION", "STALE_SNAPSHOT", "REVISION_EXHAUSTED"})
_TERMINAL_REASONS = (
    "NONE", "COVERAGE_PARTIAL", "PROVIDER_UNAVAILABLE", "PROVIDER_AUTHORIZATION_FAILED",
    "PROVIDER_RATE_LIMITED", "PROVIDER_RESPONSE_INVALID", "TIME_BUDGET_EXHAUSTED",
    "RESULT_INCONCLUSIVE", "SETTLED_FAILED", "OPERATION_EXPIRED", "SERVICE_UNAVAILABLE",
    "RESULT_BLOCKED", "RESULT_INVALID", "RESULT_UNSUPPORTED", "SERVICE_DISABLED",
)
_REJECTION_REASONS = (
    "AUTHENTICATION", "ACCOUNT_UNAVAILABLE", "DEVICE_REQUIRED", "ACCESS_UNAVAILABLE",
    "ALLOWANCE_EXHAUSTED", "INPUT_REJECTED", "PRIVACY_REVIEW_REQUIRED",
    "CONTRACT_UNSUPPORTED", "RATE_LIMITED", "CHECK_ID_CONFLICT", "OPERATION_EXPIRED",
    "SERVICE_DISABLED", "SERVICE_UNAVAILABLE",
)


class RevisionFault(ValueError):
    def __init__(self, code):
        self.code = code if code in _FAULTS else "INVALID_RUN"
        super().__init__(self.code)


def _fail(code):
    raise RevisionFault(code) from None


def _object(properties, required=True):
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": list(properties) if required else []}


def _count(maximum=10**13):
    return {"type": "integer", "minimum": 0, "maximum": maximum}


def _counts(names):
    return _object({k: _count() for k in names})


_ATTEMPT_FIELDS = ("started", "awaitingFinish", "succeeded", "failed", "denied")
_PROVIDER_FIELDS = _ATTEMPT_FIELDS + (
    "costKnownProviderAttempts", "costUnknownProviderAttempts", "knownEstimatedCostMicrousd",
    "requestUnitsKnownProviderAttempts", "requestUnitsUnknownProviderAttempts", "knownProviderRequestUnits",
)
_ACCESS_FIELDS = ("accepted", "complete", "deductedChecks", "noDeductionChecks", "settlementUnknownChecks")
_RATES = _object({k: _object({"numerator": _count(), "denominator": _count(),
                             "fraction": {"type": ["number", "null"], "minimum": 0, "maximum": 1}})
                  for k in OUTCOMES})
SUMMARY_SCHEMA = _object({
    "schemaVersion": {"const": "1.0.0-operations-summary-candidate.1"},
    "environment": {"enum": ["dev", "uat", "prod"]}, "trafficClass": {"const": "customer"},
    "localDate": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
    "timezone": {"const": TIMEZONE}, "windowStartEpoch": _count(), "windowEndEpoch": _count(),
    "asOfEpoch": _count(), "producerCoverage": {"const": "not_qualified"},
    "localIntakeCoverage": {"const": "not_collected"}, "providerPriceCatalogCoverage": {"const": "not_qualified"},
    "awsCostCoverage": {"const": "unavailable"}, "storeFeesAndRevenueCoverage": {"const": "unavailable"},
    "accepted": _count(), "pending": _count(), "settled": _counts(OUTCOMES), "outcomeRates": _RATES,
    "byService": _object({s: _object({"accepted": _count(), "pending": _count(), "settled": _counts(OUTCOMES)}) for s in SERVICES}),
    "byAccessClass": _object({a: _counts(_ACCESS_FIELDS) for a in ACCESS_CLASSES}),
    "terminalReasons": _object({r: _count() for r in _TERMINAL_REASONS}, False),
    "providerAttemptsByFamily": _object({p: _counts(_PROVIDER_FIELDS) for p in PROVIDERS}),
    "internalAttempts": _counts(_ATTEMPT_FIELDS),
    "requestRejectionsByService": _object({s: _object({r: _count() for r in _REJECTION_REASONS}, False) for s in SERVICES}),
    "reconciliationGaps": _counts(("terminalWithoutAccepted", "finishWithoutStart", "attemptWithoutAccepted")),
    "deliveryPerformed": {"const": False},
})
_SUMMARY = Draft202012Validator(SUMMARY_SCHEMA)
_STAT_FIELDS = ("accepted", "pending", "settled", "byService", "byAccessClass", "terminalReasons",
                "providerAttemptsByFamily", "internalAttempts", "requestRejectionsByService", "reconciliationGaps")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _validated(value, validator, code):
    try:
        if type(value) is not dict:
            _fail(code)
        wire = _canonical(value)
        snapshot = json.loads(wire)
        if len(wire) > MAX_RECORD_BYTES or not validator.is_valid(snapshot):
            _fail(code)
        return snapshot
    except (TypeError, ValueError, OverflowError, RecursionError):
        _fail(code)


def validate_summary(summary):
    """Reject extensions and inconsistent denominators before persistence."""
    value = _validated(summary, _SUMMARY, "INVALID_SUMMARY")
    try:
        start, end = day_bounds(value["localDate"])
    except ProjectionFault:
        _fail("INVALID_SUMMARY")
    if (value["windowStartEpoch"] != start or value["windowEndEpoch"] != end
            or value["asOfEpoch"] < end
            or value["accepted"] != value["pending"] + sum(value["settled"].values())
            or sum(value["terminalReasons"].values()) != sum(value["settled"].values())):
        _fail("INVALID_SUMMARY")
    services, access = value["byService"].values(), value["byAccessClass"].values()
    if (sum(s["accepted"] for s in services) != value["accepted"]
            or sum(s["pending"] for s in services) != value["pending"]
            or any(sum(s["settled"][k] for s in services) != value["settled"][k] for k in OUTCOMES)
            or any(s["accepted"] != s["pending"] + sum(s["settled"].values()) for s in services)
            or sum(a["accepted"] for a in access) != value["accepted"]
            or sum(a["complete"] for a in access) != value["settled"]["complete"]
            or sum(a["settlementUnknownChecks"] for a in access) != value["pending"]):
        _fail("INVALID_SUMMARY")
    for name, row in value["byAccessClass"].items():
        if (row["accepted"] != row["deductedChecks"] + row["noDeductionChecks"] + row["settlementUnknownChecks"]
                or row["deductedChecks"] != (0 if name == "complimentary" else row["complete"])
                or row["complete"] > row["accepted"] - row["settlementUnknownChecks"]):
            _fail("INVALID_SUMMARY")
    reasons = value["terminalReasons"]
    outcomes = value["settled"]
    unique = {"NONE": "complete", "RESULT_INCONCLUSIVE": "inconclusive",
              "RESULT_BLOCKED": "blocked", "RESULT_INVALID": "invalid_input",
              "RESULT_UNSUPPORTED": "unsupported"}
    if any(reasons.get(reason, 0) != outcomes[outcome] for reason, outcome in unique.items()):
        _fail("INVALID_SUMMARY")
    partial_left = outcomes["partial"] - reasons.get("COVERAGE_PARTIAL", 0)
    failed_left = outcomes["failed"] - reasons.get("SETTLED_FAILED", 0) - reasons.get("OPERATION_EXPIRED", 0)
    unavailable_left = outcomes["unavailable"] - reasons.get("SERVICE_DISABLED", 0)
    provider_reasons = sum(reasons.get(k, 0) for k in (
        "PROVIDER_UNAVAILABLE", "PROVIDER_AUTHORIZATION_FAILED", "PROVIDER_RATE_LIMITED",
        "PROVIDER_RESPONSE_INVALID", "TIME_BUDGET_EXHAUSTED"))
    service_unavailable = reasons.get("SERVICE_UNAVAILABLE", 0)
    # The summary does not retain a reason/outcome cross-product. Check that a
    # valid allocation exists under the exact event-schema reason families.
    if (min(partial_left, failed_left, unavailable_left) < 0
            or provider_reasons < partial_left or service_unavailable < failed_left
            or partial_left + failed_left + unavailable_left != provider_reasons + service_unavailable):
        _fail("INVALID_SUMMARY")
    for name, rate in value["outcomeRates"].items():
        expected = None if not value["accepted"] else value["settled"][name] / value["accepted"]
        if rate != {"numerator": value["settled"][name], "denominator": value["accepted"], "fraction": expected}:
            _fail("INVALID_SUMMARY")
    for row in [value["internalAttempts"], *value["providerAttemptsByFamily"].values()]:
        if row["started"] != sum(row[k] for k in _ATTEMPT_FIELDS[1:]):
            _fail("INVALID_SUMMARY")
        if "costKnownProviderAttempts" in row and (
                row["costKnownProviderAttempts"] + row["costUnknownProviderAttempts"] != row["started"]
                or row["requestUnitsKnownProviderAttempts"] + row["requestUnitsUnknownProviderAttempts"] != row["started"]
                or row["costKnownProviderAttempts"] > row["started"] - row["awaitingFinish"]
                or row["requestUnitsKnownProviderAttempts"] > row["started"] - row["awaitingFinish"]
                or row["knownEstimatedCostMicrousd"] > 10**9 * min(row["costKnownProviderAttempts"], row["succeeded"] + row["failed"])
                or row["knownProviderRequestUnits"] > 10 * min(row["requestUnitsKnownProviderAttempts"], row["succeeded"] + row["failed"])):
            _fail("INVALID_SUMMARY")
    # Optional finite reason maps have one canonical sparse representation.
    value["terminalReasons"] = {k: v for k, v in value["terminalReasons"].items() if v}
    value["requestRejectionsByService"] = {
        service: {k: v for k, v in counts.items() if v}
        for service, counts in value["requestRejectionsByService"].items()}
    return value


def _numeric_paths(schema, prefix=""):
    if schema.get("type") == "integer":
        return [prefix]
    return [p for name, child in schema.get("properties", {}).items()
            for p in _numeric_paths(child, prefix + "/" + name)]


_PATHS = tuple(p for field in _STAT_FIELDS for p in _numeric_paths(SUMMARY_SCHEMA["properties"][field], "/" + field))
_HEX = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_REVISION_SCHEMA = _object({
    "schemaVersion": {"const": "1.0.0-operations-revision-candidate.1"},
    "revision": {"type": "integer", "minimum": 1, "maximum": 2147483647},
    "report": SUMMARY_SCHEMA, "reportSha256": _HEX,
    "previousRecordSha256": {"anyOf": [_HEX, {"type": "null"}]}, "recordSha256": _HEX,
    "generatedAtEpoch": _count(), "expiresAtEpoch": _count(),
    "plannedRunLocalDate": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
    "numericDelta": _object({p: {"type": "integer", "minimum": -(10**13), "maximum": 10**13} for p in _PATHS}, False),
})
_REVISION = Draft202012Validator(_REVISION_SCHEMA)


def _content(report):
    return {k: v for k, v in report.items() if k != "asOfEpoch"}


def _value(report, path):
    value = report
    for part in path.lstrip("/").split("/"):
        if part not in value:
            return 0
        value = value[part]
    return value


def _deltas(before, after):
    delta = {p: _value(after, p) - _value(before, p) for p in _PATHS}
    return {p: v for p, v in delta.items() if v != 0}


def _date(epoch):
    try:
        return datetime.fromtimestamp(epoch, ZoneInfo(TIMEZONE)).date().isoformat()
    except (ValueError, OverflowError, OSError):
        _fail("INVALID_RUN")


def _prior(record):
    old = _validated(record, _REVISION, "INVALID_PREVIOUS_REVISION")
    try:
        normalized = validate_summary(old["report"])
    except RevisionFault:
        _fail("INVALID_PREVIOUS_REVISION")
    if (normalized != old["report"] or old["reportSha256"] != _sha(old["report"])
            or old["recordSha256"] != _sha({k: v for k, v in old.items() if k != "recordSha256"})
            or old["expiresAtEpoch"] != old["report"]["windowStartEpoch"] + RETENTION_SECONDS
            or old["generatedAtEpoch"] < old["report"]["asOfEpoch"]
            or old["generatedAtEpoch"] >= old["report"]["windowStartEpoch"] + CORRECTION_SECONDS
            or old["plannedRunLocalDate"] != _date(old["generatedAtEpoch"])
            or (old["revision"] == 1) != (old["previousRecordSha256"] is None)
            or old["revision"] == 1 and old["numericDelta"] != _deltas({}, old["report"])):
        _fail("INVALID_PREVIOUS_REVISION")
    return old


def plan_revision(previous, summary, *, generated_at_epoch):
    """Plan immutable CAS-ready changes, coalescing to at most one per local day.

    This has no store and cannot serialize concurrent callers. A future trusted
    adapter must atomically compare expectedPreviousSha256 and keep only one
    coalesced pending correction per cohort. No delivery key is sent here.
    """
    report = validate_summary(summary)
    if type(generated_at_epoch) is not int or generated_at_epoch < report["asOfEpoch"]:
        _fail("INVALID_RUN")
    start = report["windowStartEpoch"]
    if generated_at_epoch >= start + RETENTION_SECONDS:
        _fail("AGGREGATE_EXPIRED")
    if generated_at_epoch >= start + CORRECTION_SECONDS:
        _fail("CORRECTION_WINDOW_CLOSED")
    old = None if previous is None else _prior(previous)
    if old is not None:
        before = old["report"]
        if any(before[k] != report[k] for k in ("environment", "localDate", "windowStartEpoch", "windowEndEpoch")):
            _fail("INVALID_PREVIOUS_REVISION")
        if report["asOfEpoch"] < before["asOfEpoch"] or generated_at_epoch < old["generatedAtEpoch"]:
            _fail("STALE_SNAPSHOT")
        # Immutable facts cannot disappear on deletion, expiry or a partial scan.
        # Pending/unknown and gap counts may decrease as facts are reconciled.
        may_decrease = ("/pending", "/awaitingFinish", "/settlementUnknownChecks",
                        "/costUnknownProviderAttempts", "/requestUnitsUnknownProviderAttempts")
        for path in _PATHS:
            if path.startswith("/reconciliationGaps/") or path.endswith(may_decrease):
                continue
            if _value(report, path) < _value(before, path):
                _fail("AGGREGATE_REGRESSION")
        if _content(before) == _content(report):
            return {"decision": "NO_CHANGE", "record": old, "expectedPreviousSha256": old["recordSha256"]}
        if old["plannedRunLocalDate"] == _date(generated_at_epoch):
            return {"decision": "DEFER_TO_NEXT_DAILY", "record": old,
                    "expectedPreviousSha256": old["recordSha256"]}
        if old["revision"] == 2147483647:
            _fail("REVISION_EXHAUSTED")
    previous_report = {} if old is None else old["report"]
    record = {
        "schemaVersion": "1.0.0-operations-revision-candidate.1",
        "revision": 1 if old is None else old["revision"] + 1,
        "report": report, "reportSha256": _sha(report),
        "previousRecordSha256": None if old is None else old["recordSha256"],
        "generatedAtEpoch": generated_at_epoch, "expiresAtEpoch": start + RETENTION_SECONDS,
        "plannedRunLocalDate": _date(generated_at_epoch),
        "numericDelta": _deltas(previous_report, report),
    }
    record["recordSha256"] = _sha(record)
    return {"decision": "CREATE_REVISION", "record": deepcopy(record),
            "expectedPreviousSha256": None if old is None else old["recordSha256"]}
