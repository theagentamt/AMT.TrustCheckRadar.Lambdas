"""Authority-bound references with an independent acceptance-based deadline.

Only an actual receipt may supply source facts. This projection authenticates no
caller, persists/sends nothing, and never copies account/key/client/content data.
"""
from decimal import Decimal
import re
import secrets

RETENTION_SECONDS = 7 * 86400
_META_KEYS = {"schemaVersion", "logicalCheckRef", "acceptedEventId", "terminalEventId",
              "environment", "service", "inputKind", "accessClass", "acceptedAtEpoch", "expiresAtEpoch"}
_INPUTS = {"message_check": {"pasted_text", "ocr", "mixed"},
           "url_check": {"standalone_url", "message_url", "qr_url"},
           "recovery_clarification": {"recovery_clarification"}}
_UNIQUE = {"complete": "NONE", "inconclusive": "RESULT_INCONCLUSIVE", "blocked": "RESULT_BLOCKED",
           "invalid_input": "RESULT_INVALID", "unsupported": "RESULT_UNSUPPORTED"}
_PROVIDER = {"PROVIDER_UNAVAILABLE", "PROVIDER_AUTHORIZATION_FAILED", "PROVIDER_RATE_LIMITED",
             "PROVIDER_RESPONSE_INVALID", "TIME_BUDGET_EXHAUSTED"}
_REASONS = {**{k: {v} for k, v in _UNIQUE.items()}, "partial": _PROVIDER | {"COVERAGE_PARTIAL"},
            "failed": {"SETTLED_FAILED", "OPERATION_EXPIRED", "SERVICE_UNAVAILABLE"},
            "unavailable": _PROVIDER | {"SERVICE_DISABLED", "SERVICE_UNAVAILABLE"}}


class OperationsReceiptFault(ValueError):
    def __init__(self):
        super().__init__("OPERATIONS_RECEIPT_INVALID")


def _fail():
    raise OperationsReceiptFault() from None


def _int(value):
    if type(value) is int and 0 <= value <= 10**13:
        return value
    if (isinstance(value, Decimal) and value.is_finite() and 0 <= value <= 10**13
            and value == value.to_integral_value()):
        return int(value)
    _fail()


def _metadata(value):
    if type(value) is not dict or set(value) != _META_KEYS:
        _fail()
    m = dict(value)
    for key in ("schemaVersion", "acceptedAtEpoch", "expiresAtEpoch"):
        m[key] = _int(m[key])
    try:
        if (m["schemaVersion"] != 1 or not re.fullmatch(r"lc1_[0-9a-f]{32}", m["logicalCheckRef"])
                or not re.fullmatch(r"op1_[0-9a-f]{32}", m["acceptedEventId"])
                or not re.fullmatch(r"op1_[0-9a-f]{32}", m["terminalEventId"])
                or m["acceptedEventId"] == m["terminalEventId"]
                or m["environment"] not in ("dev", "uat", "prod")
                or m["service"] not in _INPUTS or m["inputKind"] not in _INPUTS[m["service"]]
                or m["accessClass"] not in ("trial", "paid", "complimentary")
                or not 0 <= m["acceptedAtEpoch"] < m["expiresAtEpoch"] <= m["acceptedAtEpoch"] + RETENTION_SECONDS):
            _fail()
    except (TypeError, KeyError):
        _fail()
    return m


def retirement_metadata(row):
    """Capture existing metadata for bounded removal; never mint a reference."""
    if type(row) is not dict:
        _fail()
    return _metadata(row.get("operationsReceipt"))


def admission_fields(payload, *, accepted_at, receipt_deadline, access_class, environment,
                     random_hex=lambda: secrets.token_hex(16)):
    """Mint only with a new admission; replay must retain original references."""
    accepted, deadline = _int(accepted_at), _int(receipt_deadline)
    if type(payload) is not dict:
        _fail()
    entry = payload.get("entryPoint")
    if entry == "message":
        target = payload.get("target")
        kind = target.get("sourceType") if type(target) is dict else None
        if kind not in ("pasted_text", "ocr", "mixed"):
            _fail()
        service = "message_check"
    elif entry == "recovery":
        service, kind = "recovery_clarification", "recovery_clarification"
    elif entry in ("standalone_url", "message_url", "qr_url"):
        service, kind = "url_check", entry
    else:
        _fail()
    try:
        m = {"schemaVersion": 1, "logicalCheckRef": "lc1_" + random_hex(),
             "acceptedEventId": "op1_" + random_hex(), "terminalEventId": "op1_" + random_hex(),
             "environment": environment, "service": service, "inputKind": kind, "accessClass": access_class,
             "acceptedAtEpoch": accepted, "expiresAtEpoch": min(deadline, accepted + RETENTION_SECONDS)}
        return {"operationsReceipt": _metadata(m)}
    except Exception:
        _fail()


def settlement_fields(row, processing_outcome, *, reason_code=None, expired_recovery=False):
    """Retain a source-backed finite cause; ambiguous omitted causes stay unknown."""
    if type(row) is not dict or type(expired_recovery) is not bool:
        _fail()
    m = _metadata(row.get("operationsReceipt"))
    try:
        if processing_outcome not in _REASONS:
            _fail()
        if expired_recovery:
            if processing_outcome != "failed" or reason_code not in (None, "OPERATION_EXPIRED"):
                _fail()
            reason_code = "OPERATION_EXPIRED"
        if reason_code is None:
            reason_code = _UNIQUE.get(processing_outcome)
        if reason_code is not None and reason_code not in _REASONS[processing_outcome]:
            _fail()
        return {"capturedMetadata": m, "terminalReasonCode": reason_code}
    except (TypeError, KeyError):
        _fail()


def receipt_events(row, *, now_epoch):
    """Project source facts, not arbitrary caller attestation or ingestion.

    Future ingestion must atomically verify the same receipt and deletion fence.
    No provider cause, accounting decision or missing-source zero is inferred.
    """
    now = _int(now_epoch)
    if type(row) is not dict or row.get("recordType") != "V1_CHECK_RECEIPT" or now < 0:
        _fail()
    if "operationsReceipt" not in row:
        return {"coverage": "NOT_RECORDED", "events": []}
    m = _metadata(row["operationsReceipt"])
    if now < m["acceptedAtEpoch"]:
        _fail()
    if now >= m["expiresAtEpoch"]:
        return {"coverage": "EXPIRED", "events": []}
    if row.get("basis") != m["accessClass"] or row.get("state") not in ("ADMITTED", "SETTLED"):
        _fail()
    event = {"schemaVersion": "1.0.0-operations-candidate.1", "eventType": "check_accepted",
             "eventId": m["acceptedEventId"], "eventTimeEpoch": m["acceptedAtEpoch"],
             "environment": m["environment"], "trafficClass": "customer", "service": m["service"],
             "inputKind": m["inputKind"], "logicalCheckRef": m["logicalCheckRef"], "lifecycleRevision": 1,
             "processingOutcome": None, "accountingState": "pending", "chargedChecks": None,
             "accessClass": m["accessClass"], "reasonCode": "NONE", "attemptRef": None,
             "attemptKind": None, "component": None, "attemptOrdinal": None, "providerFamily": None,
             "attemptOutcome": None, "durationBucket": None, "providerRequestUnits": None, "estimatedCostMicrousd": None}
    if row["state"] == "ADMITTED":
        if any(row.get(key) is not None for key in ("chargedChecks", "processingOutcome", "receiptId", "assessmentEpoch")):
            _fail()
        return {"coverage": "ACCEPTED_ONLY", "events": [event]}
    outcome, reason = row.get("processingOutcome"), row.get("operationsTerminalReasonCode")
    try:
        charge, assessed = _int(row.get("chargedChecks")), _int(row.get("assessmentEpoch"))
        receipt = row.get("receiptId")
        valid_receipt = type(receipt) is str and (re.fullmatch(r"[0-9a-f]{32}", receipt)
            or outcome == "failed" and reason == "OPERATION_EXPIRED" and re.fullmatch(r"expired_[0-9a-f]{32}", receipt))
        if (outcome not in _REASONS or charge != int(outcome == "complete" and m["accessClass"] != "complimentary")
                or not valid_receipt
                or not m["acceptedAtEpoch"] <= assessed <= now or assessed >= m["expiresAtEpoch"]):
            _fail()
        if reason is None:
            return {"coverage": "TERMINAL_REASON_NOT_RECORDED", "events": [event]}
        if reason not in _REASONS[outcome]:
            _fail()
    except (TypeError, KeyError):
        _fail()
    terminal = {**event, "eventType": "check_terminal", "eventId": m["terminalEventId"],
                "eventTimeEpoch": assessed, "lifecycleRevision": 2, "processingOutcome": outcome,
                "accountingState": "charged" if charge else "not_charged", "chargedChecks": charge, "reasonCode": reason}
    return {"coverage": "CHECK_LIFECYCLE_RECORDED", "events": [event, terminal]}
