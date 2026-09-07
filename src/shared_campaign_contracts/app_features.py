from __future__ import annotations

from decimal import Decimal
import json
import math
import re


APP_FEATURE_MAX_BYTES = 32 * 1024
APP_FEATURE_FIELDS = frozenset(
    {
        "schemaVersion",
        "extractorVersion",
        "languageId",
        "taxonomyBucket",
        "vector",
        "lexicalFingerprint",
        "signalIds",
        "indicatorIds",
        "confidence",
    }
)
STABLE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SIGNAL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{16}$")


class AppFeaturesContractError(ValueError):
    """Raised when app-provided campaign features violate the V1 contract."""


def validate_app_features(value) -> dict:
    if not isinstance(value, dict):
        raise AppFeaturesContractError("appFeatures must be an object")
    if set(value) != APP_FEATURE_FIELDS:
        raise AppFeaturesContractError("appFeatures fields are invalid")
    if isinstance(value["schemaVersion"], bool) or value["schemaVersion"] != 1:
        raise AppFeaturesContractError("appFeatures schemaVersion is unsupported")

    extractor_version = value["extractorVersion"]
    try:
        extractor_version_bytes = extractor_version.encode("utf-8") if isinstance(extractor_version, str) else b""
    except UnicodeEncodeError as err:
        raise AppFeaturesContractError("appFeatures extractorVersion is invalid") from err
    if (
        not isinstance(extractor_version, str)
        or not extractor_version
        or len(extractor_version_bytes) > 64
    ):
        raise AppFeaturesContractError("appFeatures extractorVersion is invalid")

    language_id = _stable_id(value["languageId"], "languageId")
    taxonomy_bucket = _stable_id(value["taxonomyBucket"], "taxonomyBucket")
    vector = _number_list(value["vector"], "vector", minimum_items=1, maximum_items=384)
    lexical_fingerprint = _unique_string_list(
        value["lexicalFingerprint"],
        "lexicalFingerprint",
        maximum_items=32,
        pattern=FINGERPRINT_PATTERN,
    )
    signal_ids = _unique_string_list(
        value["signalIds"],
        "signalIds",
        maximum_items=16,
        pattern=SIGNAL_ID_PATTERN,
    )
    indicator_ids = _unique_string_list(
        value["indicatorIds"],
        "indicatorIds",
        maximum_items=16,
        pattern=SIGNAL_ID_PATTERN,
    )
    confidence = _number(value["confidence"], "confidence", minimum=0.0, maximum=1.0)

    normalized = {
        "schemaVersion": 1,
        "extractorVersion": extractor_version,
        "languageId": language_id,
        "taxonomyBucket": taxonomy_bucket,
        "vector": vector,
        "lexicalFingerprint": lexical_fingerprint,
        "signalIds": signal_ids,
        "indicatorIds": indicator_ids,
        "confidence": confidence,
    }
    if len(canonical_app_features_json(normalized).encode("utf-8")) > APP_FEATURE_MAX_BYTES:
        raise AppFeaturesContractError("appFeatures exceeds 32768 bytes")
    return normalized


def canonical_app_features_json(value: dict) -> str:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        rendered.encode("utf-8")
        return rendered
    except (TypeError, ValueError, UnicodeEncodeError) as err:
        raise AppFeaturesContractError("appFeatures cannot be canonically encoded") from err


def _stable_id(value, field: str) -> str:
    if not isinstance(value, str) or not STABLE_ID_PATTERN.fullmatch(value):
        raise AppFeaturesContractError(f"appFeatures {field} is invalid")
    return value


def _number_list(value, field: str, *, minimum_items: int, maximum_items: int) -> list[float]:
    if not isinstance(value, list) or not minimum_items <= len(value) <= maximum_items:
        raise AppFeaturesContractError(f"appFeatures {field} length is invalid")
    return [_number(item, field, minimum=-1.0, maximum=1.0) for item in value]


def _number(value, field: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise AppFeaturesContractError(f"appFeatures {field} must contain numbers")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise AppFeaturesContractError(f"appFeatures {field} number is out of range")
    return number


def _unique_string_list(value, field: str, *, maximum_items: int, pattern) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise AppFeaturesContractError(f"appFeatures {field} length is invalid")
    if any(not isinstance(item, str) or not pattern.fullmatch(item) for item in value):
        raise AppFeaturesContractError(f"appFeatures {field} contains an invalid value")
    if len(set(value)) != len(value):
        raise AppFeaturesContractError(f"appFeatures {field} contains duplicates")
    return list(value)
