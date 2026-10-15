"""Typed content-free projection of settled governed assessment receipts."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import hmac
import json
import re

VERSION = "1.0.0-governed-history-candidate.1"
INDEX_NAME = "GSI2"
RETENTION_SECONDS = 7 * 24 * 60 * 60
MESSAGE_VERSION = "1.0.0-message-candidate.3"
URL_VERSION = "1.0.0-candidate.2"
RESULT = re.compile(r"gh1_([0-9]{1,12})_([0-9a-f]{32})")
INDEX_SORT = re.compile(r"GOVERNED#([0-9]{12})#([0-9a-f]{32})")
SOURCE_TYPES = {"pasted_text", "ocr", "mixed", "standalone_url", "message_url", "qr_url"}
FRESHNESS_STATUSES = {"current", "historical", "not_applicable"}


def plain(value):
    if isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value():
            raise ValueError("GOVERNED_HISTORY_INVALID")
        return int(value)
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def admission_metadata(payload, accepted_at):
    if type(accepted_at) is not int or accepted_at < 0 or type(payload) is not dict:
        raise ValueError("GOVERNED_HISTORY_INVALID")
    if payload.get("messageTransportVersion") == MESSAGE_VERSION:
        target = payload.get("target")
        source = target.get("sourceType") if type(target) is dict else None
        result_type = "message"
    elif payload.get("urlTransportVersion") == URL_VERSION:
        source = payload.get("entryPoint")
        result_type = "url"
    else:
        return {}
    if source not in SOURCE_TYPES:
        raise ValueError("GOVERNED_HISTORY_INVALID")
    return {"governedHistoryAcceptedAtEpoch": accepted_at,
            "governedHistorySourceType": source,
            "governedHistoryResultType": result_type}


def settlement_fields(row, result_summary, *, receipt_id, charged_checks, processing_outcome, settled_at,
                      retention_seconds=RETENTION_SECONDS):
    row = plain(row)
    if type(row) is not dict or result_summary is None:
        return {}
    message = row.get("messageTransportVersion") == MESSAGE_VERSION
    url = row.get("urlTransportVersion") == URL_VERSION
    if not (message or url):
        return {}
    accepted = row.get("governedHistoryAcceptedAtEpoch")
    source = row.get("governedHistorySourceType")
    result_type = row.get("governedHistoryResultType")
    if (type(accepted) is not int or accepted < 0 or type(settled_at) is not int or settled_at < accepted
            or retention_seconds != RETENTION_SECONDS or source not in SOURCE_TYPES
            or result_type != ("message" if message else "url")
            or not isinstance(receipt_id, str) or not re.fullmatch(r"[0-9a-f]{32}", receipt_id)
            or type(charged_checks) is not int or charged_checks not in (0, 1)):
        raise ValueError("GOVERNED_HISTORY_INVALID")
    outcome = plain(deepcopy(result_summary))
    if type(outcome) is not dict or outcome.get("processingOutcome") != processing_outcome:
        raise ValueError("GOVERNED_HISTORY_INVALID")
    outcome.pop("checkId", None)
    deadline = settled_at + RETENTION_SECONDS
    result_id = f"gh1_{settled_at}_{receipt_id}"
    projection = {
        "schemaVersion": 1,
        "transportVersion": VERSION,
        "resultId": result_id,
        "resultType": result_type,
        "sourceType": source,
        "acceptedAtEpoch": accepted,
        "settledAtEpoch": settled_at,
        "assessmentTransportVersion": MESSAGE_VERSION if message else URL_VERSION,
        "processingOutcome": processing_outcome,
        "accounting": {"state": "charged" if charged_checks else "not_charged",
                       "chargedChecks": charged_checks, "receiptId": receipt_id},
        "outcome": outcome,
        "expiresAtEpoch": deadline,
    }
    validate_projection(projection)
    digest = hashlib.sha256(canonical(projection)).hexdigest()
    return {"governedHistory": projection, "governedHistoryDigest": digest,
            "GSI2PK": row["PK"], "GSI2SK": f"GOVERNED#{settled_at:012d}#{receipt_id}",
            "expiresAt": deadline, "retentionDeadlineEpoch": deadline}


def validate_projection(value):
    value = plain(value)
    keys = {"schemaVersion", "transportVersion", "resultId", "resultType", "sourceType",
            "acceptedAtEpoch", "settledAtEpoch", "assessmentTransportVersion", "processingOutcome",
            "accounting", "outcome", "expiresAtEpoch"}
    if type(value) is not dict or set(value) != keys:
        raise ValueError("GOVERNED_HISTORY_INVALID")
    match = RESULT.fullmatch(value.get("resultId", "")) if isinstance(value.get("resultId"), str) else None
    accepted, settled, expiry = (value.get(name) for name in
                                 ("acceptedAtEpoch", "settledAtEpoch", "expiresAtEpoch"))
    if (type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1
            or value.get("transportVersion") != VERSION or not match
            or type(accepted) is not int or type(settled) is not int or type(expiry) is not int
            or accepted < 0 or settled < accepted or expiry != settled + RETENTION_SECONDS
            or int(match.group(1)) != settled or value.get("sourceType") not in SOURCE_TYPES
            or value.get("resultType") not in ("message", "url")):
        raise ValueError("GOVERNED_HISTORY_INVALID")
    accounting = value.get("accounting")
    if (type(accounting) is not dict or set(accounting) != {"state", "chargedChecks", "receiptId"}
            or accounting.get("receiptId") != match.group(2)
            or type(accounting.get("chargedChecks")) is not int or accounting["chargedChecks"] not in (0, 1)
            or accounting.get("state") != ("charged" if accounting["chargedChecks"] else "not_charged")):
        raise ValueError("GOVERNED_HISTORY_INVALID")
    outcome = value.get("outcome")
    if type(outcome) is not dict or "checkId" in outcome or outcome.get("processingOutcome") != value.get("processingOutcome"):
        raise ValueError("GOVERNED_HISTORY_INVALID")
    if value["resultType"] == "message":
        if value["assessmentTransportVersion"] != MESSAGE_VERSION or value["sourceType"] not in {"pasted_text", "ocr", "mixed"}:
            raise ValueError("GOVERNED_HISTORY_INVALID")
        from shared_message_contract.validation_v3 import validate_summary
        validate_summary(outcome | {"checkId": "history"}, "history", value["processingOutcome"])
    else:
        if value["assessmentTransportVersion"] != URL_VERSION or value["sourceType"] not in {"standalone_url", "message_url", "qr_url"}:
            raise ValueError("GOVERNED_HISTORY_INVALID")
        from shared_check_authority.summary import validate_summary
        validate_summary(outcome | {"checkId": "history"}, "history", value["processingOutcome"])
    return value


def validate_stored_receipt(row, *, now):
    row = plain(row)
    required = {"PK", "SK", "recordType", "state", "governedHistory", "governedHistoryDigest",
                "GSI2PK", "GSI2SK", "expiresAt", "retentionDeadlineEpoch"}
    if (type(row) is not dict or not required <= set(row) or row.get("recordType") != "V1_CHECK_RECEIPT"
            or row.get("state") != "SETTLED" or row.get("GSI2PK") != row.get("PK")
            or type(now) is not int or type(row.get("expiresAt")) is not int
            or row["expiresAt"] <= now or row.get("retentionDeadlineEpoch") != row["expiresAt"]):
        raise ValueError("GOVERNED_HISTORY_INVALID")
    projection = validate_projection(row["governedHistory"])
    expected_sort = f"GOVERNED#{projection['settledAtEpoch']:012d}#{projection['accounting']['receiptId']}"
    if (row.get("GSI2SK") != expected_sort or not INDEX_SORT.fullmatch(expected_sort)
            or row["expiresAt"] != projection["expiresAtEpoch"]
            or not isinstance(row.get("governedHistoryDigest"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", row["governedHistoryDigest"])
            or not hmac.compare_digest(row["governedHistoryDigest"], hashlib.sha256(canonical(projection)).hexdigest())):
        raise ValueError("GOVERNED_HISTORY_INVALID")
    return projection


def present_projection(value, now):
    value = validate_projection(value)
    projected = deepcopy(value)
    if projected["resultType"] == "message":
        from shared_message_contract.validation_v3 import present
        outcome = present(projected["outcome"] | {"checkId": "history"}, now)
        evidence = outcome["evidence"]
        freshness = ("historical" if any(item["freshness"] == "expired" for item in evidence)
                     else "current" if evidence else "not_applicable")
        projected["presentation"] = {
            "freshnessStatus": freshness,
            "verdict": outcome["verdict"],
            "processingOutcome": outcome["processingOutcome"],
            "coverage": outcome["coverage"],
            "reasonCodes": outcome["limitationCodes"],
            "messageKey": outcome["messageKey"],
            "nextAction": outcome["nextAction"],
        }
    elif (projected["outcome"].get("verdict") == "high_risk"
          and projected["outcome"].get("lookupValidUntil") is not None):
        from shared_lookup_freshness import current
        if not current(projected["outcome"]["lookupObservedAt"], projected["outcome"]["lookupValidUntil"], now):
            projected["presentation"] = {
                "freshnessStatus": "historical", "verdict": "unknown",
                "processingOutcome": "partial", "coverage": "limited",
                "reasonCodes": ["PROVIDER_EVIDENCE_EXPIRED"],
                "messageKey": None, "nextAction": None,
            }
        else:
            projected["presentation"] = _original_presentation(projected, "current")
    else:
        projected["presentation"] = _original_presentation(projected, "not_applicable")
    return validate_public_projection(projected)


def _original_presentation(value, freshness):
    outcome = value["outcome"]
    return {
        "freshnessStatus": freshness,
        "verdict": outcome["verdict"],
        "processingOutcome": outcome["processingOutcome"],
        "coverage": outcome["coverage"],
        "reasonCodes": outcome["reasonCodes"],
        "messageKey": None,
        "nextAction": None,
    }


def validate_public_projection(value):
    value = plain(value)
    if type(value) is not dict or set(value) != ({
            "schemaVersion", "transportVersion", "resultId", "resultType", "sourceType",
            "acceptedAtEpoch", "settledAtEpoch", "assessmentTransportVersion", "processingOutcome",
            "accounting", "outcome", "expiresAtEpoch", "presentation"}):
        raise ValueError("GOVERNED_HISTORY_INVALID")
    stored = {key: item for key, item in value.items() if key != "presentation"}
    validate_projection(stored)
    presentation = value["presentation"]
    exact = {"freshnessStatus", "verdict", "processingOutcome", "coverage", "reasonCodes",
             "messageKey", "nextAction"}
    if (type(presentation) is not dict or set(presentation) != exact
            or presentation.get("freshnessStatus") not in FRESHNESS_STATUSES
            or presentation.get("verdict") not in ("unknown", "suspicious", "high_risk", "no_known_threat_detected")
            or presentation.get("processingOutcome") not in ("invalid_input", "blocked", "unsupported", "unavailable", "inconclusive", "partial", "complete")
            or presentation.get("coverage") not in ("not_assessed", "limited", "supported_checks_complete")
            or type(presentation.get("reasonCodes")) is not list or len(presentation["reasonCodes"]) > 8
            or len(set(presentation["reasonCodes"])) != len(presentation["reasonCodes"])
            or any(not isinstance(code, str) or not re.fullmatch(r"[A-Z_]{1,80}", code)
                   for code in presentation["reasonCodes"])):
        raise ValueError("GOVERNED_HISTORY_INVALID")
    if value["resultType"] == "message":
        if not isinstance(presentation["messageKey"], str) or not isinstance(presentation["nextAction"], str):
            raise ValueError("GOVERNED_HISTORY_INVALID")
    elif presentation["messageKey"] is not None or presentation["nextAction"] is not None:
        raise ValueError("GOVERNED_HISTORY_INVALID")
    return value
