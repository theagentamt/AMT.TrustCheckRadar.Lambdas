"""Deterministic, non-publishing aggregate calculation for SEC237.

Inputs are a caller-supplied bounded projection snapshot, NOT proof of trusted
authority admission. No runtime calls this module. A future ingestion adapter
must verify authoritative receipt/deletion fences atomically before storage.
This kernel cannot attest producer completeness, deliver reports or persist
corrections. All public failures are finite codes; rejected bodies are not kept.
"""

from collections import Counter
from datetime import date, datetime, time, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator


TIMEZONE = "America/Chicago"
CORRECTION_SECONDS = 7 * 24 * 60 * 60
MAX_BATCH_FACTS = 10_000
MAX_FACT_BYTES = 4096
SERVICES = ("message_check", "url_check", "recovery_clarification")
OUTCOMES = (
    "complete", "partial", "inconclusive", "failed", "blocked",
    "invalid_input", "unsupported", "unavailable",
)
ACCESS_CLASSES = ("trial", "paid", "complimentary")
PROVIDERS = ("google_web_risk", "openai")
_ROOT = Path(__file__).parent
_EVENT = Draft202012Validator(json.loads((_ROOT / "event.schema.json").read_text()))
_REJECTION = Draft202012Validator(json.loads((_ROOT / "rejection.schema.json").read_text()))
_FAULTS = frozenset({
    "INVALID_ARGUMENT", "BATCH_BOUND_EXCEEDED", "INVALID_EVENT",
    "INVALID_REJECTION", "ENVIRONMENT_MISMATCH", "EVENT_ID_CONFLICT",
    "FACT_CONFLICT", "CHECK_CONTEXT_CONFLICT", "ATTEMPT_CONTEXT_CONFLICT",
    "INVALID_EVENT_TIME", "ATTEMPT_ORDINAL_CONFLICT", "STALE_REJECTION",
    "CORRECTION_WINDOW_CLOSED", "LINKAGE_EXPIRED",
    "REJECTION_CONTEXT_CONFLICT",
})


class ProjectionFault(ValueError):
    """No input, schema error text, identifier or arbitrary exception is exposed."""

    def __init__(self, code):
        if code not in _FAULTS:
            code = "INVALID_ARGUMENT"
        self.code = code
        super().__init__(code)


def _fail(code):
    raise ProjectionFault(code) from None


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _validated(value, validator, code):
    # Plain JSON values only. Bounds also prevent huge hostile strings from
    # flowing into validation messages or retained reconciliation state.
    if type(value) is not dict:
        _fail(code)
    try:
        wire = _canonical(value)
        snapshot = json.loads(wire)
        valid = len(wire.encode("utf-8")) <= MAX_FACT_BYTES and validator.is_valid(snapshot)
    except (ValueError, TypeError, RecursionError, OverflowError):
        _fail(code)
    if not valid:
        _fail(code)
    return snapshot


def day_bounds(local_day):
    """Half-open local-midnight UTC epoch bounds, including 23/25 hour days."""
    if type(local_day) is not str:
        _fail("INVALID_ARGUMENT")
    try:
        day = date.fromisoformat(local_day)
        if day.isoformat() != local_day:
            _fail("INVALID_ARGUMENT")
        zone = ZoneInfo(TIMEZONE)
        start = datetime.combine(day, time.min, zone)
        end = datetime.combine(day + timedelta(days=1), time.min, zone)
        return int(start.timestamp()), int(end.timestamp())
    except (ValueError, OverflowError):
        _fail("INVALID_ARGUMENT")


def _same(left, right, fields, code):
    if any(left[k] != right[k] for k in fields):
        _fail(code)


def _put_unique(target, key, value):
    old = target.get(key)
    if old is not None and old != value:
        _fail("FACT_CONFLICT")
    target[key] = value


def _attempt_counts():
    return {"started": 0, "awaitingFinish": 0, "succeeded": 0, "failed": 0, "denied": 0}


def _provider_counts():
    return {**_attempt_counts(), "costKnownProviderAttempts": 0,
            "costUnknownProviderAttempts": 0, "knownEstimatedCostMicrousd": 0,
            "requestUnitsKnownProviderAttempts": 0,
            "requestUnitsUnknownProviderAttempts": 0, "knownProviderRequestUnits": 0}


def reconcile_rejection_snapshot(previous, incoming):
    """Pure monotonic replacement rule; a store must apply it with atomic CAS.

    No retention timestamp is generated or renewed here. The canonical key is
    identical for both facts; replay does not turn a cumulative count into a
    delta. This function alone does not provide durable or concurrent safety.
    """
    new = _validated(incoming, _REJECTION, "INVALID_REJECTION")
    if previous is None:
        return new
    old = _validated(previous, _REJECTION, "INVALID_REJECTION")
    key = tuple(k for k in new if k != "rejectedCount")
    _same(old, new, key, "REJECTION_CONTEXT_CONFLICT")
    if new["rejectedCount"] < old["rejectedCount"]:
        _fail("STALE_REJECTION")
    return new


def aggregate_day(events, rejection_snapshots, *, local_day, environment, as_of_epoch):
    """Calculate one bounded, in-window cohort; never declare live completeness.

    Exact replay is a no-op. Conflicting same-phase facts abort the calculation,
    not last-write-wins. Missing accepted/start facts are reported as incomplete
    coverage and never manufactured. Callers must preserve prior unlinkable
    aggregate revisions when linkable corrections expire; this function refuses
    a new full calculation outside the conservative cohort correction window.
    """
    if (type(events) is not list or type(rejection_snapshots) is not list
            or environment not in ("dev", "uat", "prod")
            or type(as_of_epoch) is not int or as_of_epoch < 0):
        _fail("INVALID_ARGUMENT")
    if len(events) + len(rejection_snapshots) > MAX_BATCH_FACTS:
        _fail("BATCH_BOUND_EXCEEDED")
    start, end = day_bounds(local_day)
    if as_of_epoch < start:
        _fail("INVALID_ARGUMENT")
    if as_of_epoch >= start + CORRECTION_SECONDS:
        _fail("CORRECTION_WINDOW_CLOSED")

    ids, checks, attempts = {}, {}, {}
    for raw in events:
        event = _validated(raw, _EVENT, "INVALID_EVENT")
        if event["environment"] != environment:
            _fail("ENVIRONMENT_MISMATCH")
        if event["eventTimeEpoch"] > as_of_epoch:
            _fail("INVALID_EVENT_TIME")
        event_id = event["eventId"]
        if event_id in ids:
            if ids[event_id] != event:
                _fail("EVENT_ID_CONFLICT")
            continue
        ids[event_id] = event
        kind = event["eventType"]
        if kind.startswith("check_"):
            fact = checks.setdefault(event["logicalCheckRef"], {})
            _put_unique(fact, kind, event)
        else:
            fact = attempts.setdefault(event["attemptRef"], {})
            _put_unique(fact, kind, event)

    # An alternative event ID cannot manufacture a second fact for a phase.
    # The complete canonical fact includes its identity as well as its content.
    service_counts = {s: {"accepted": 0, "pending": 0,
                         "settled": dict.fromkeys(OUTCOMES, 0)} for s in SERVICES}
    access_counts = {a: {"accepted": 0, "complete": 0, "deductedChecks": 0,
                         "noDeductionChecks": 0, "settlementUnknownChecks": 0}
                     for a in ACCESS_CLASSES}
    reason_counts = Counter()
    missing_accepted = 0
    for fact in checks.values():
        accepted = fact.get("check_accepted")
        terminal = fact.get("check_terminal")
        if accepted is None:
            missing_accepted += 1
            continue
        accepted_at = accepted["eventTimeEpoch"]
        if as_of_epoch >= accepted_at + CORRECTION_SECONDS:
            _fail("LINKAGE_EXPIRED")
        if terminal is not None:
            _same(accepted, terminal, ("environment", "trafficClass", "service",
                                      "inputKind", "logicalCheckRef", "accessClass"),
                  "CHECK_CONTEXT_CONFLICT")
            if terminal["eventTimeEpoch"] < accepted_at:
                _fail("INVALID_EVENT_TIME")
        if not start <= accepted_at < end:
            continue
        service = service_counts[accepted["service"]]
        access = access_counts[accepted["accessClass"]]
        service["accepted"] += 1
        access["accepted"] += 1
        if terminal is None:
            service["pending"] += 1
            access["settlementUnknownChecks"] += 1
        else:
            outcome = terminal["processingOutcome"]
            service["settled"][outcome] += 1
            reason_counts[terminal["reasonCode"]] += 1
            access["complete"] += int(outcome == "complete")
            if terminal["accountingState"] == "charged":
                access["deductedChecks"] += terminal["chargedChecks"]
            else:
                access["noDeductionChecks"] += 1

    provider = {p: _provider_counts() for p in PROVIDERS}
    internal = _attempt_counts()
    missing_start = missing_attempt_accepted = 0
    ordinals = {}
    for attempt_ref, fact in attempts.items():
        began = fact.get("attempt_started")
        finished = fact.get("attempt_finished")
        if began is None:
            missing_start += 1
            continue
        if finished is not None:
            _same(began, finished, ("environment", "trafficClass", "service", "inputKind",
                                    "logicalCheckRef", "attemptRef", "attemptKind",
                                    "component", "attemptOrdinal", "providerFamily"),
                  "ATTEMPT_CONTEXT_CONFLICT")
            if finished["eventTimeEpoch"] < began["eventTimeEpoch"]:
                _fail("INVALID_EVENT_TIME")
        accepted = checks.get(began["logicalCheckRef"], {}).get("check_accepted")
        if accepted is None:
            missing_attempt_accepted += 1
            continue
        _same(accepted, began, ("service", "inputKind"), "ATTEMPT_CONTEXT_CONFLICT")
        if began["eventTimeEpoch"] < accepted["eventTimeEpoch"]:
            _fail("INVALID_EVENT_TIME")
        terminal = checks.get(began["logicalCheckRef"], {}).get("check_terminal")
        if terminal is not None and began["eventTimeEpoch"] > terminal["eventTimeEpoch"]:
            _fail("INVALID_EVENT_TIME")
        key = (began["logicalCheckRef"], began["attemptOrdinal"])
        if key in ordinals and ordinals[key] != attempt_ref:
            _fail("ATTEMPT_ORDINAL_CONFLICT")
        ordinals[key] = attempt_ref
        if not start <= began["eventTimeEpoch"] < end:
            continue
        count = internal if began["attemptKind"] == "internal_component" else provider[began["providerFamily"]]
        count["started"] += 1
        count["awaitingFinish" if finished is None else finished["attemptOutcome"]] += 1
        if began["attemptKind"] == "provider_call":
            cost = None if finished is None else finished["estimatedCostMicrousd"]
            units = None if finished is None else finished["providerRequestUnits"]
            count["costKnownProviderAttempts" if cost is not None else "costUnknownProviderAttempts"] += 1
            count["requestUnitsKnownProviderAttempts" if units is not None else "requestUnitsUnknownProviderAttempts"] += 1
            if cost is not None:
                count["knownEstimatedCostMicrousd"] += cost
            if units is not None:
                count["knownProviderRequestUnits"] += units

    rejections = {}
    for raw in rejection_snapshots:
        rejection = _validated(raw, _REJECTION, "INVALID_REJECTION")
        if rejection["environment"] != environment:
            _fail("ENVIRONMENT_MISMATCH")
        window = rejection["windowStartEpoch"]
        if window % 300 or window + 300 > as_of_epoch:
            _fail("INVALID_EVENT_TIME")
        key = (window, rejection["service"], rejection["reasonCode"])
        old = rejections.get(key)
        # Only the reconciled head belongs in a projection snapshot. Different
        # counts require explicit monotonic ingestion, never summation or
        # arrival-order arbitration inside a daily calculation.
        if old is not None and old != rejection["rejectedCount"]:
            _fail("REJECTION_CONTEXT_CONFLICT")
        rejections[key] = rejection["rejectedCount"]
    rejected = {s: Counter() for s in SERVICES}
    for (window, service, reason), count in rejections.items():
        if start <= window < end:
            rejected[service][reason] += count

    settled = {outcome: sum(s["settled"][outcome] for s in service_counts.values()) for outcome in OUTCOMES}
    accepted_total = sum(s["accepted"] for s in service_counts.values())
    pending = sum(s["pending"] for s in service_counts.values())
    return {
        "schemaVersion": "1.0.0-operations-summary-candidate.1",
        "environment": environment, "trafficClass": "customer",
        "localDate": local_day, "timezone": TIMEZONE,
        "windowStartEpoch": start, "windowEndEpoch": end, "asOfEpoch": as_of_epoch,
        "producerCoverage": "not_qualified", "localIntakeCoverage": "not_collected",
        "providerPriceCatalogCoverage": "not_qualified",
        "awsCostCoverage": "unavailable", "storeFeesAndRevenueCoverage": "unavailable",
        "accepted": accepted_total, "pending": pending, "settled": settled,
        "outcomeRates": {k: {"numerator": v, "denominator": accepted_total,
                             "fraction": None if not accepted_total else v / accepted_total}
                         for k, v in settled.items()},
        "byService": service_counts, "byAccessClass": access_counts,
        "terminalReasons": dict(sorted(reason_counts.items())),
        "providerAttemptsByFamily": provider, "internalAttempts": internal,
        "requestRejectionsByService": {k: dict(sorted(v.items())) for k, v in rejected.items()},
        "reconciliationGaps": {"terminalWithoutAccepted": missing_accepted,
                               "finishWithoutStart": missing_start,
                               "attemptWithoutAccepted": missing_attempt_accepted},
        "deliveryPerformed": False,
    }
