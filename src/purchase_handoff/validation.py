import json
from typing import Any

from config import ALLOWED_PLATFORMS, ALLOWED_PURCHASE_STATES, STRING_PATTERN, SUPPORTED_PRODUCTS
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

    product_id = _required_string(payload, "productId")
    if product_id not in SUPPORTED_PRODUCTS:
        raise _invalid_request("productId", f"Unsupported productId '{product_id}'.")

    platform = _required_string(payload, "platform").lower()
    if platform not in ALLOWED_PLATFORMS:
        raise _invalid_request("platform", f"Unsupported platform '{platform}'.")

    purchase_state = _required_string(payload, "purchaseState").lower()
    if purchase_state not in ALLOWED_PURCHASE_STATES:
        raise _invalid_request("purchaseState", f"Unsupported purchaseState '{purchase_state}'.")

    proof = payload.get("proof")
    if not isinstance(proof, dict):
        raise _invalid_request("proof", "proof must be an object.")

    transaction_id = _optional_string(proof.get("transactionId"), "proof.transactionId")
    purchase_token = _optional_string(proof.get("purchaseToken"), "proof.purchaseToken")
    receipt = _optional_string(proof.get("receipt"), "proof.receipt")
    purchase_time = _optional_string(proof.get("purchaseTime"), "proof.purchaseTime")
    subscription_period = _optional_string(proof.get("subscriptionPeriod"), "proof.subscriptionPeriod")

    if not any([transaction_id, purchase_token, receipt]):
        raise _invalid_request("proof", "At least one store proof field is required.")

    return {
        "productId": product_id,
        "platform": platform,
        "purchaseState": purchase_state,
        "proof": {
            "transactionId": transaction_id,
            "purchaseToken": purchase_token,
            "receipt": receipt,
            "purchaseTime": purchase_time,
            "subscriptionPeriod": subscription_period,
        },
    }


def _required_string(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise _invalid_request(field_name, f"{field_name} is required.")
    cleaned = value.strip()
    if not STRING_PATTERN.fullmatch(cleaned):
        raise _invalid_request(field_name, f"{field_name} contains unsupported characters.")
    return cleaned


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _invalid_request(field_name, f"{field_name} must be a non-empty string when provided.")
    cleaned = value.strip()
    if len(cleaned) > 2048:
        raise _invalid_request(field_name, f"{field_name} is too long.")
    return cleaned


def _invalid_request(field: str, issue: str) -> AppError:
    return AppError(
        "INVALID_REQUEST",
        "The submitted request is invalid.",
        retryable=False,
        details=[{"field": field, "issue": issue}],
    )
