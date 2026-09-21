import base64
import binascii
import json
from typing import Any

from config import (
    ALLOWED_ENTITY_TYPES,
    ALLOWED_SOURCE_TYPES,
    MAX_ENTITIES,
    MAX_REQUEST_BODY_BYTES,
    MAX_SANITIZED_TEXT_LENGTH,
    REQUEST_ID_PATTERN,
    SCHEMA_VERSION,
)
from errors import AppError
from shared_campaign_contracts import AppFeaturesContractError, validate_app_features


def parse_and_validate_event(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body")
    if body is None:
        raise _invalid_request("body", "Request body is required.")
    encoded = event.get("isBase64Encoded", False)
    if type(encoded) is not bool:
        raise _invalid_request("body", "isBase64Encoded must be a boolean.")
    try:
        if isinstance(body, str):
            if encoded:
                # Bound allocation before decoding; base64 whitespace is not accepted.
                if len(body) > 4 * ((MAX_REQUEST_BODY_BYTES + 2) // 3):
                    raise _invalid_request("body", f"Request body exceeds {MAX_REQUEST_BODY_BYTES} bytes.")
                raw = base64.b64decode(body, validate=True)
            else:
                raw = body.encode("utf-8", errors="strict")
            if len(raw) > MAX_REQUEST_BODY_BYTES:
                raise _invalid_request("body", f"Request body exceeds {MAX_REQUEST_BODY_BYTES} bytes.")
            payload = json.loads(raw.decode("utf-8", errors="strict"))
        elif isinstance(body, dict) and not encoded:
            # Internal adapter convenience only: there is no original wire body.
            raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(raw) > MAX_REQUEST_BODY_BYTES:
                raise _invalid_request("body", f"Request body exceeds {MAX_REQUEST_BODY_BYTES} bytes.")
            payload = body
        else:
            raise _invalid_request("body", "Request body must be a JSON object.")
    except (UnicodeError, ValueError, TypeError, RecursionError, binascii.Error):
        raise _invalid_request("body", "Request body must be valid UTF-8 JSON with valid encoding.") from None

    if not isinstance(payload, dict):
        raise _invalid_request("body", "Request body must be a JSON object.")
    # Escaped lone surrogates can survive json.loads even though the wire is UTF-8.
    # Validate scalar strings without reserializing/re-measuring the sized payload.
    def scalar_strings(value):
        if isinstance(value, str):
            value.encode("utf-8", errors="strict")
        elif isinstance(value, dict):
            for key, child in value.items():
                scalar_strings(key)
                scalar_strings(child)
        elif isinstance(value, list):
            for child in value:
                scalar_strings(child)
    try:
        scalar_strings(payload)
    except (UnicodeError, RecursionError):
        raise _invalid_request("body", "Request body contains invalid Unicode scalar values.") from None

    schema_version = _required_string(payload, "schemaVersion")
    if schema_version != SCHEMA_VERSION:
        raise AppError(
            "UNSUPPORTED_SCHEMA_VERSION",
            "The submitted request uses an unsupported schema version.",
            retryable=False,
            details=[{"field": "schemaVersion", "issue": f"Unsupported schema version '{schema_version}'."}],
        )

    request_id = _required_string(payload, "requestId")
    if not REQUEST_ID_PATTERN.fullmatch(request_id):
        raise _invalid_request(
            "requestId",
            "requestId must be 1-128 characters and contain only letters, numbers, period, underscore, colon, or hyphen.",
        )
    source_type = _required_string(payload, "sourceType")
    if source_type not in ALLOWED_SOURCE_TYPES:
        raise _invalid_request("sourceType", f"Unsupported source type '{source_type}'.")

    local_sanitization_applied = payload.get("localSanitizationApplied")
    if local_sanitization_applied is not True:
        raise _invalid_request("localSanitizationApplied", "localSanitizationApplied must be true.")

    sanitized_text = _required_string(payload, "sanitizedText")
    if len(sanitized_text) > MAX_SANITIZED_TEXT_LENGTH:
        raise _invalid_request("sanitizedText", f"sanitizedText exceeds {MAX_SANITIZED_TEXT_LENGTH} characters.")

    entities = payload.get("entities")
    if not isinstance(entities, list):
        raise _invalid_request("entities", "entities must be an array.")
    if len(entities) > MAX_ENTITIES:
        raise _invalid_request("entities", f"entities exceeds {MAX_ENTITIES} items.")

    validated_entities = []
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict):
            raise _invalid_request(f"entities[{index}]", "Each entity must be an object.")
        token = _required_string(entity, "token", location=f"entities[{index}]")
        entity_type = _required_string(entity, "type", location=f"entities[{index}]")
        if entity_type not in ALLOWED_ENTITY_TYPES:
            raise _invalid_request(f"entities[{index}].type", f"Unsupported entity type '{entity_type}'.")
        validated_entities.append({"token": token, "type": entity_type})

    campaign_consent_granted = payload.get("campaignConsentGranted", False)
    if not isinstance(campaign_consent_granted, bool):
        raise _invalid_request(
            "campaignConsentGranted",
            "campaignConsentGranted must be a boolean when provided.",
        )

    raw_app_features = payload.get("appFeatures")
    if raw_app_features is None:
        if campaign_consent_granted:
            raise _invalid_request(
                "appFeatures",
                "appFeatures is required when campaign consent is granted.",
            )
        app_features = None
    else:
        try:
            app_features = validate_app_features(raw_app_features)
        except AppFeaturesContractError as err:
            raise _invalid_request("appFeatures", str(err)) from err

    return {
        "schemaVersion": schema_version,
        "requestId": request_id,
        "sourceType": source_type,
        "localSanitizationApplied": True,
        "sanitizedText": sanitized_text,
        "entities": validated_entities,
        "campaignConsentGranted": campaign_consent_granted,
        "appFeatures": app_features,
    }


def _required_string(payload: dict[str, Any], field_name: str, *, location: str | None = None) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise _invalid_request(location or field_name, f"{field_name} is required.")
    return value.strip()


def _invalid_request(field: str, issue: str) -> AppError:
    return AppError(
        "INVALID_REQUEST",
        "The submitted request is invalid.",
        retryable=False,
        details=[{"field": field, "issue": issue}],
    )
