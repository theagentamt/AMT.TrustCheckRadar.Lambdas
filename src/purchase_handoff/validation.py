import json
from datetime import datetime
from typing import Any

from config import (
    ALLOWED_PLATFORMS,
    ALLOWED_PURCHASE_STATES,
    GOOGLE_PLAY_PACKAGE_NAME,
    GOOGLE_PLAY_PRO_PRODUCT_ID,
    ORDER_ID_PATTERN,
    PACKAGE_NAME_PATTERN,
    SUPPORTED_PRODUCTS,
    TOKEN_PATTERN,
)
from errors import AppError


def parse_and_validate_event(event: dict[str, Any]) -> dict[str, Any]:
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

    platform = _required_string(payload, "platform").lower()
    if platform not in ALLOWED_PLATFORMS:
        raise _invalid_request("platform", f"Unsupported platform '{platform}'.")

    product_id = _required_string(payload, "productId")
    if product_id != GOOGLE_PLAY_PRO_PRODUCT_ID or product_id not in SUPPORTED_PRODUCTS:
        raise _invalid_request("productId", f"Unsupported productId '{product_id}'.")

    purchase_token = _required_token(payload.get("purchaseToken"), "purchaseToken")
    order_id = _optional_pattern_string(payload.get("orderId"), "orderId", ORDER_ID_PATTERN)
    purchase_time = _optional_timestamp(payload.get("purchaseTime"), "purchaseTime")
    purchase_state = _optional_purchase_state(payload.get("purchaseState"))
    package_name = _optional_package_name(payload.get("packageName"), "packageName")
    subscription_metadata = payload.get("subscriptionMetadata")
    if subscription_metadata is not None and not isinstance(subscription_metadata, dict):
        raise _invalid_request("subscriptionMetadata", "subscriptionMetadata must be an object when provided.")

    expected_package_name = GOOGLE_PLAY_PACKAGE_NAME
    if package_name and expected_package_name and package_name != expected_package_name:
        raise _invalid_request("packageName", "packageName does not match the configured Google Play package.")

    effective_package_name = package_name or expected_package_name
    if not effective_package_name:
        raise _invalid_request("packageName", "packageName is required when no configured Google Play package is available.")

    return {
        "platform": platform,
        "productId": product_id,
        "purchaseToken": purchase_token,
        "orderId": order_id,
        "purchaseTime": purchase_time,
        "purchaseState": purchase_state,
        "packageName": effective_package_name,
        "subscriptionMetadata": subscription_metadata or {},
    }


def _required_string(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise _invalid_request(field_name, f"{field_name} is required.")
    return value.strip()


def _required_token(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid_request(field_name, f"{field_name} is required.")
    cleaned = value.strip()
    if not TOKEN_PATTERN.fullmatch(cleaned):
        raise _invalid_request(field_name, f"{field_name} contains unsupported characters.")
    return cleaned


def _optional_pattern_string(value: Any, field_name: str, pattern) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _invalid_request(field_name, f"{field_name} must be a non-empty string when provided.")
    cleaned = value.strip()
    if not pattern.fullmatch(cleaned):
        raise _invalid_request(field_name, f"{field_name} contains unsupported characters.")
    return cleaned


def _optional_timestamp(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _invalid_request(field_name, f"{field_name} must be a non-empty string when provided.")
    cleaned = value.strip()
    try:
        datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError as err:
        raise _invalid_request(field_name, f"{field_name} must be a valid ISO-8601 timestamp.") from err
    return cleaned


def _optional_purchase_state(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _invalid_request("purchaseState", "purchaseState must be a non-empty string when provided.")
    normalized = value.strip().upper()
    if normalized not in ALLOWED_PURCHASE_STATES:
        raise _invalid_request("purchaseState", f"Unsupported purchaseState '{normalized}'.")
    return normalized


def _optional_package_name(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _invalid_request(field_name, f"{field_name} must be a non-empty string when provided.")
    cleaned = value.strip()
    if not PACKAGE_NAME_PATTERN.fullmatch(cleaned):
        raise _invalid_request(field_name, f"{field_name} is not a valid package name.")
    return cleaned


def _invalid_request(field: str, issue: str) -> AppError:
    return AppError(
        "INVALID_REQUEST",
        "The submitted request is invalid.",
        retryable=False,
        details=[{"field": field, "issue": issue}],
    )
