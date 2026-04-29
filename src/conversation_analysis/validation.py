import json
from typing import Any

from config import ALLOWED_SOURCE_TYPES, MAX_ENTITIES, MAX_REQUEST_BODY_BYTES, MAX_SANITIZED_TEXT_LENGTH, SCHEMA_VERSION
from errors import AppError


def parse_and_validate_event(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body")
    if body is None:
        raise AppError("INVALID_REQUEST", "The submitted request is invalid.", retryable=False, details=[{"field": "body", "issue": "Request body is required."}])

    if isinstance(body, str):
        if len(body.encode("utf-8")) > MAX_REQUEST_BODY_BYTES:
            raise _invalid_request("body", f"Request body exceeds {MAX_REQUEST_BODY_BYTES} bytes.")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as err:
            raise _invalid_request("body", "Request body must be valid JSON.") from err
    elif isinstance(body, dict):
        payload = body
    else:
        raise _invalid_request("body", "Request body must be a JSON object.")

    if not isinstance(payload, dict):
        raise _invalid_request("body", "Request body must be a JSON object.")

    schema_version = _required_string(payload, "schemaVersion")
    if schema_version != SCHEMA_VERSION:
        raise AppError(
            "UNSUPPORTED_SCHEMA_VERSION",
            "The submitted request uses an unsupported schema version.",
            retryable=False,
            details=[{"field": "schemaVersion", "issue": f"Unsupported schema version '{schema_version}'."}],
        )

    request_id = _required_string(payload, "requestId")
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
        validated_entities.append({"token": token, "type": entity_type})

    return {
        "schemaVersion": schema_version,
        "requestId": request_id,
        "sourceType": source_type,
        "localSanitizationApplied": True,
        "sanitizedText": sanitized_text,
        "entities": validated_entities,
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
