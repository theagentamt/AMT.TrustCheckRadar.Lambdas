import json
from typing import Any

from config import (
    ACCOUNT_ID_PATTERN,
    ALLOWED_ACTIONS,
    ALLOWED_PLATFORMS,
    BINDING_FINGERPRINT_PATTERN,
    OS_VERSION_PATTERN,
)
from errors import AppError


def parse_and_validate_event(event: dict[str, Any]) -> dict[str, str | None]:
    body = event.get("body")
    if body is None:
        raise _invalid_request("body", "Request body is required.")

    if isinstance(body, str):
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

    action = _required_string(payload, "action")
    if action not in ALLOWED_ACTIONS:
        raise _invalid_request("action", f"Unsupported action '{action}'.")

    account_id = _required_string(payload, "accountId")
    if not ACCOUNT_ID_PATTERN.fullmatch(account_id):
        raise _invalid_request("accountId", "accountId contains unsupported characters or is too long.")

    binding_fingerprint = None
    raw_fingerprint = payload.get("bindingFingerprint")
    if raw_fingerprint is not None:
        if not isinstance(raw_fingerprint, str) or not raw_fingerprint.strip():
            raise _invalid_request("bindingFingerprint", "bindingFingerprint must be a non-empty string when provided.")
        binding_fingerprint = raw_fingerprint.strip()
        if not BINDING_FINGERPRINT_PATTERN.fullmatch(binding_fingerprint):
            raise _invalid_request(
                "bindingFingerprint",
                "bindingFingerprint must be 1-256 characters and contain only letters, numbers, period, underscore, colon, or hyphen.",
            )

    if action == "RECOVER_BINDING" and not binding_fingerprint:
        raise _invalid_request("bindingFingerprint", "bindingFingerprint is required for RECOVER_BINDING.")

    expected = {"action", "accountId"} | ({"bindingFingerprint"} if action == "RECOVER_BINDING" else set())
    if set(payload) != expected:
        raise _invalid_request("body", "Request fields do not match the support recovery contract.")

    return {
        "action": action,
        "accountId": account_id,
        "bindingFingerprint": binding_fingerprint,
    }


def parse_and_validate_self_event(event: dict[str, Any]) -> dict[str, str]:
    body = event.get("body")
    try:
        payload = json.loads(body) if isinstance(body, str) else body
    except (TypeError, ValueError) as err:
        raise _invalid_request("body", "Request body must be valid JSON.") from err
    expected = {
        "schemaVersion", "operationId", "action", "bindingFingerprint",
        "platform", "osVersion",
    }
    if not isinstance(payload, dict) or set(payload) != expected or payload.get("schemaVersion") != 1:
        raise _invalid_request("body", "Request fields do not match the self-recovery contract.")
    if payload.get("action") != "REPLACE_ACTIVE_BINDING":
        raise _invalid_request("action", "Only REPLACE_ACTIVE_BINDING is supported.")
    import uuid
    try:
        operation = uuid.UUID(payload.get("operationId"))
    except (TypeError, ValueError, AttributeError) as err:
        raise _invalid_request("operationId", "operationId must be a canonical UUIDv4.") from err
    if operation.version != 4 or str(operation) != payload["operationId"]:
        raise _invalid_request("operationId", "operationId must be a canonical UUIDv4.")
    fingerprint = _required_string(payload, "bindingFingerprint")
    if not BINDING_FINGERPRINT_PATTERN.fullmatch(fingerprint):
        raise _invalid_request("bindingFingerprint", "bindingFingerprint is invalid.")
    platform = _required_string(payload, "platform")
    if platform not in ALLOWED_PLATFORMS:
        raise _invalid_request("platform", "platform must be ios or android.")
    os_version = _required_string(payload, "osVersion")
    if not OS_VERSION_PATTERN.fullmatch(os_version):
        raise _invalid_request("osVersion", "osVersion is invalid.")
    query = event.get("queryStringParameters") or {}
    if not isinstance(query, dict) or query:
        raise _invalid_request("query", "Self-recovery does not accept query parameters.")
    return {
        "schemaVersion": 1, "operationId": payload["operationId"],
        "action": payload["action"], "bindingFingerprint": fingerprint,
        "platform": platform, "osVersion": os_version,
    }


def _required_string(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise _invalid_request(field_name, f"{field_name} is required.")
    return value.strip()


def _invalid_request(field: str, issue: str) -> AppError:
    return AppError(
        "INVALID_REQUEST",
        "The submitted request is invalid.",
        retryable=False,
        details=[{"field": field, "issue": issue}],
    )
