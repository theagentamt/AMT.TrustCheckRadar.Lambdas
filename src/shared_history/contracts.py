from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
import math
import re
import unicodedata

from .errors import HistoryError

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
ALLOWED_SOURCE_TYPES = {"ocr", "pasted_text", "mixed"}
ALLOWED_RISK_LEVELS = {"low", "medium", "high"}
PROHIBITED_KEYS = {
    "sanitizedText", "text", "rawText", "ocrText", "image", "images",
    "attachment", "attachments", "snippet", "entities", "originalValue",
}
HISTORY_ITEM_FIELDS = {
    "PK", "SK", "recordType", "schemaVersion", "recordVersion", "requestId",
    "historyGeneration", "recognitionGeneration", "acceptedSequence",
    "acceptedAtEpochMs", "completedAtEpochMs", "sourceType", "assessment",
    "expiresAt", "expiryBucket",
}


def build_history_items(*, account_id, payload_hash, accepted, payload, response, now_epoch, settings):
    """Build exact content and control items without copying request content."""
    request_id = _request_id(response.get("requestId"))
    if payload.get("requestId") != request_id:
        raise HistoryError("SERVER_UNAVAILABLE", "The analysis and History request IDs do not match.")
    source_type = payload.get("sourceType")
    if source_type not in ALLOWED_SOURCE_TYPES:
        raise HistoryError("SERVER_UNAVAILABLE", "The History source type is invalid.")
    assessment = _assessment(response, settings)
    generation = _positive_int(accepted.get("historyGeneration"), "historyGeneration", minimum=0)
    recognition_generation = _positive_int(
        accepted.get("recognitionGeneration"), "recognitionGeneration", minimum=0
    )
    sequence = _positive_int(accepted.get("acceptedSequence"), "acceptedSequence", minimum=1)
    accepted_at = _positive_int(accepted.get("acceptedAtEpochMs"), "acceptedAtEpochMs", minimum=1)
    completed_at_ms = int(now_epoch) * 1000
    expires_at = int(now_epoch) + settings.retention_days * 86400
    content_pk = f"USER#{account_id}#HISTORY#{generation}"
    content_sk = f"COMPLETE#{completed_at_ms:013d}#{request_id}"
    content = {
        "PK": content_pk,
        "SK": content_sk,
        "recordType": "HISTORY",
        "schemaVersion": settings.schema_version,
        "recordVersion": 1,
        "requestId": request_id,
        "historyGeneration": generation,
        "recognitionGeneration": recognition_generation,
        "acceptedSequence": sequence,
        "acceptedAtEpochMs": accepted_at,
        "completedAtEpochMs": completed_at_ms,
        "sourceType": source_type,
        "assessment": assessment,
        "expiresAt": expires_at,
        "expiryBucket": _expiry_bucket("HISTORY", expires_at, request_id),
    }
    dedup_expires_at = int(now_epoch) + settings.dedup_retention_days * 86400
    locator = {
        "PK": f"USER#{account_id}",
        "SK": f"REQUEST#{request_id}",
        "recordType": "REQUEST",
        "schemaVersion": settings.schema_version,
        "status": "ACTIVE",
        "requestId": request_id,
        "payloadHash": _sha256(payload_hash),
        "historyGeneration": generation,
        "acceptedSequence": sequence,
        "contentSortKey": content_sk,
        "completedAtEpochMs": completed_at_ms,
        "contentExpiresAt": expires_at,
        "expiresAt": dedup_expires_at,
        "expiryBucket": _expiry_bucket("CONTROL", dedup_expires_at, request_id),
    }
    return content, locator


def response_from_history_item(item: dict, settings=None) -> dict:
    if not isinstance(item, dict) or set(item) != HISTORY_ITEM_FIELDS:
        raise HistoryError("SERVER_UNAVAILABLE", "Stored History record fields are invalid.")
    _request_id(item.get("requestId"))
    if item.get("recordType") != "HISTORY" or item.get("sourceType") not in ALLOWED_SOURCE_TYPES:
        raise HistoryError("SERVER_UNAVAILABLE", "Stored History record type is invalid.")
    assessment = item.get("assessment")
    if not isinstance(assessment, dict) or set(assessment) != {
        "schemaVersion", "scamScore", "riskLevel", "confidence", "summary", "signals", "recommendedActions"
    }:
        raise HistoryError("SERVER_UNAVAILABLE", "Stored History assessment is invalid.")
    response = {
        "schemaVersion": "1.0",
        "requestId": item.get("requestId"),
        "scamScore": _json_number(assessment.get("scamScore")),
        "riskLevel": assessment.get("riskLevel"),
        "confidence": _json_number(assessment.get("confidence")),
        "summary": assessment.get("summary"),
        "signals": list(assessment.get("signals") or []),
        "recommendedActions": list(assessment.get("recommendedActions") or []),
    }
    if settings is not None:
        validated = _assessment(response, settings)
        response.update({key: _json_compatible(value) for key, value in validated.items()})
    return response


def public_history_item(item: dict, settings=None) -> dict:
    return {
        "requestId": item["requestId"],
        "sourceType": item["sourceType"],
        "acceptedAtEpochMs": int(item["acceptedAtEpochMs"]),
        "completedAtEpochMs": int(item["completedAtEpochMs"]),
        "assessment": response_from_history_item(item, settings),
    }


def validate_no_prohibited_fields(value, path="record"):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in PROHIBITED_KEYS:
                raise HistoryError("SERVER_UNAVAILABLE", f"Prohibited History field at {path}.{key}.")
            validate_no_prohibited_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            validate_no_prohibited_fields(child, f"{path}[{index}]")


def _assessment(response, settings):
    if not isinstance(response, dict) or set(response) != {
        "schemaVersion", "requestId", "scamScore", "riskLevel", "confidence",
        "summary", "signals", "recommendedActions",
    }:
        raise HistoryError("SERVER_UNAVAILABLE", "The analysis response is not the exact retained contract.")
    if response["schemaVersion"] != "1.0":
        raise HistoryError("SERVER_UNAVAILABLE", "The analysis response version is unsupported.")
    score = _bounded_number(response["scamScore"], 0, 100, "scamScore")
    confidence = _bounded_number(response["confidence"], 0, 1, "confidence")
    if response["riskLevel"] not in ALLOWED_RISK_LEVELS:
        raise HistoryError("SERVER_UNAVAILABLE", "The retained risk level is invalid.")
    summary = _bounded_text(response["summary"], settings.max_summary_bytes, "summary")
    signals = _bounded_text_list(response["signals"], settings, "signals")
    actions = _bounded_text_list(response["recommendedActions"], settings, "recommendedActions")
    assessment = {
        "schemaVersion": "1.0",
        "scamScore": score,
        "riskLevel": response["riskLevel"],
        "confidence": confidence,
        "summary": summary,
        "signals": signals,
        "recommendedActions": actions,
    }
    validate_no_prohibited_fields(assessment)
    return assessment


def _bounded_text_list(value, settings, field):
    if not isinstance(value, list) or len(value) > settings.max_list_items:
        raise HistoryError("SERVER_UNAVAILABLE", f"The retained {field} list is invalid.")
    result = [_bounded_text(item, settings.max_text_field_bytes, field) for item in value]
    if len(set(result)) != len(result):
        raise HistoryError("SERVER_UNAVAILABLE", f"The retained {field} list contains duplicates.")
    return result


def _bounded_text(value, max_bytes, field):
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > max_bytes:
        raise HistoryError("SERVER_UNAVAILABLE", f"The retained {field} value is invalid.")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise HistoryError("SERVER_UNAVAILABLE", f"The retained {field} value contains control characters.")
    if any(unicodedata.category(char) == "Cf" for char in value):
        raise HistoryError("SERVER_UNAVAILABLE", f"The retained {field} value contains formatting controls.")
    return value


def _bounded_number(value, minimum, maximum, field):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise HistoryError("SERVER_UNAVAILABLE", f"The retained {field} value is invalid.")
    number = float(value)
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise HistoryError("SERVER_UNAVAILABLE", f"The retained {field} value is invalid.")
    return Decimal(str(value))


def _json_number(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else float(value)
    return value


def _json_compatible(value):
    if isinstance(value, Decimal):
        return _json_number(value)
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    return value


def validate_request_id(value):
    return _request_id(value)


def _request_id(value):
    if not isinstance(value, str) or not REQUEST_ID_PATTERN.fullmatch(value):
        raise HistoryError("SERVER_UNAVAILABLE", "The History request ID is invalid.")
    return value


def _positive_int(value, field, minimum):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise HistoryError("SERVER_UNAVAILABLE", f"The {field} value is invalid.")
    return value


def _sha256(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise HistoryError("SERVER_UNAVAILABLE", "The durable payload hash is invalid.")
    return value


def _expiry_bucket(prefix, expires_at, request_id):
    hour = datetime.fromtimestamp(expires_at, UTC).strftime("%Y%m%d%H")
    shard = int(hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:2], 16) % 16
    return f"{prefix}#{hour}#{shard:02d}"
