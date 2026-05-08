import json
from typing import Any

from config import ALLOWED_PLATFORMS, BINDING_FINGERPRINT_PATTERN, OS_VERSION_PATTERN
from errors import AppError


def parse_and_validate_event(event: dict[str, Any]) -> dict[str, str]:
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

    binding_fingerprint = _required_string(payload, "bindingFingerprint")
    if not BINDING_FINGERPRINT_PATTERN.fullmatch(binding_fingerprint):
        raise _invalid_request(
            "bindingFingerprint",
            "bindingFingerprint must be 1-256 characters and contain only letters, numbers, period, underscore, colon, or hyphen.",
        )

    platform = _required_string(payload, "platform").lower()
    if platform not in ALLOWED_PLATFORMS:
        raise _invalid_request("platform", f"Unsupported platform '{platform}'.")

    os_version = _required_string(payload, "osVersion")
    if not OS_VERSION_PATTERN.fullmatch(os_version):
        raise _invalid_request("osVersion", "osVersion contains unsupported characters or is too long.")

    return {
        "bindingFingerprint": binding_fingerprint,
        "platform": platform,
        "osVersion": os_version.strip(),
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
