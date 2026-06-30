from datetime import UTC, datetime

from config import GOOGLE_PLAY_PRO_PRODUCT_ID, VERIFICATION_MODE
from google_play_client import GooglePlayRejectedError, GooglePlayRetryableError, fetch_subscription_purchase


def verify_purchase(payload: dict) -> dict:
    if payload["platform"] == "google_play":
        return _verify_google_play_purchase(payload)

    return {
        "status": "failed_retryable",
        "reason": "Verification adapter is not implemented for the requested platform.",
        "normalizedStatus": None,
        "isAccessGranted": False,
        "billingPeriodStartUtc": None,
        "billingPeriodEndUtc": None,
    }


def _verify_google_play_purchase(payload: dict) -> dict:
    if VERIFICATION_MODE not in {"google_play", "live"}:
        return {
            "status": "failed_retryable",
            "reason": f"Unsupported verification mode '{VERIFICATION_MODE}'.",
            "normalizedStatus": None,
            "isAccessGranted": False,
            "billingPeriodStartUtc": None,
            "billingPeriodEndUtc": None,
        }

    try:
        response = fetch_subscription_purchase(
            package_name=payload["packageName"],
            purchase_token=payload["purchaseToken"],
        )
    except GooglePlayRejectedError as err:
        return {
            "status": "rejected",
            "reason": str(err),
            "normalizedStatus": "expired",
            "isAccessGranted": False,
            "billingPeriodStartUtc": None,
            "billingPeriodEndUtc": None,
        }
    except GooglePlayRetryableError as err:
        return {
            "status": "failed_retryable",
            "reason": str(err),
            "normalizedStatus": None,
            "isAccessGranted": False,
            "billingPeriodStartUtc": None,
            "billingPeriodEndUtc": None,
        }

    line_items = response.get("lineItems") or []
    if not line_items:
        return {
            "status": "rejected",
            "reason": "Google Play response did not include any subscription line items.",
            "normalizedStatus": "expired",
            "isAccessGranted": False,
            "billingPeriodStartUtc": None,
            "billingPeriodEndUtc": None,
        }

    matching_item = None
    for item in line_items:
        if item.get("productId") == GOOGLE_PLAY_PRO_PRODUCT_ID:
            matching_item = item
            break
    if not matching_item:
        return {
            "status": "rejected",
            "reason": "Google Play purchase token does not match the expected subscription product.",
            "normalizedStatus": "expired",
            "isAccessGranted": False,
            "billingPeriodStartUtc": None,
            "billingPeriodEndUtc": None,
        }

    normalized_status = _normalize_subscription_state(response.get("subscriptionState"))
    billing_period_start = _normalize_google_timestamp(matching_item.get("startTime") or response.get("startTime"))
    billing_period_end = _normalize_google_timestamp(matching_item.get("expiryTime") or response.get("expiryTime"))
    is_access_granted = _is_access_granted(normalized_status, billing_period_end)

    if normalized_status == "pending":
        status = "pending"
        reason = "Google Play reports the subscription purchase as pending."
    elif is_access_granted:
        status = "accepted"
        reason = "Google Play verified the subscription purchase."
    else:
        status = "rejected"
        reason = f"Google Play reports the subscription status as {normalized_status}."

    return {
        "status": status,
        "reason": reason,
        "normalizedStatus": normalized_status,
        "isAccessGranted": is_access_granted,
        "billingPeriodStartUtc": billing_period_start,
        "billingPeriodEndUtc": billing_period_end,
    }


def _normalize_subscription_state(value: str | None) -> str:
    state = (value or "").strip().upper()
    return {
        "SUBSCRIPTION_STATE_ACTIVE": "active",
        "SUBSCRIPTION_STATE_IN_GRACE_PERIOD": "grace",
        "SUBSCRIPTION_STATE_ON_HOLD": "hold",
        "SUBSCRIPTION_STATE_PAUSED": "paused",
        "SUBSCRIPTION_STATE_CANCELED": "canceled",
        "SUBSCRIPTION_STATE_EXPIRED": "expired",
        "SUBSCRIPTION_STATE_PENDING": "pending",
    }.get(state, "expired")


def _normalize_google_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        try:
            parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
            return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
        except ValueError:
            return cleaned
    return cleaned


def _is_access_granted(normalized_status: str, billing_period_end_utc: str | None) -> bool:
    if normalized_status in {"active", "grace"}:
        return True
    if normalized_status != "canceled" or not billing_period_end_utc:
        return False
    try:
        expiry = datetime.fromisoformat(billing_period_end_utc.replace("Z", "+00:00"))
    except ValueError:
        return False
    return expiry > datetime.now(UTC)
