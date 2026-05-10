import json
from typing import Any

from config import ACCOUNT_ID_PATTERN, ALLOWED_ACTIONS, BINDING_FINGERPRINT_PATTERN
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

    return {
        "action": action,
        "accountId": account_id,
        "bindingFingerprint": binding_fingerprint,
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
