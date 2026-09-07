from datetime import UTC, datetime
import logging
from uuid import UUID

import boto3

from .config import (
    ENTITLEMENTS_TABLE_NAME,
    ENVIRONMENT,
    FREE_MONTHLY_SCAN_LIMIT,
    PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT,
    PRO_MONTHLY_SCAN_LIMIT,
    SUPPORTED_SUBSCRIPTION_STATUSES,
    USERS_TABLE_NAME,
)

LOGGER = logging.getLogger(__name__)

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(ENTITLEMENTS_TABLE_NAME) if ENTITLEMENTS_TABLE_NAME else None
participation_table = dynamodb.Table(USERS_TABLE_NAME) if USERS_TABLE_NAME else None

DEFAULT_ENTITLEMENT_SK = "ENTITLEMENT"
GOOGLE_PLAY_PRO_SK = "ENTITLEMENT#google_play#trustcheck_radar_pro_monthly"
CAMPAIGN_PARTICIPATION_SK = "CAMPAIGN_PARTICIPATION"


class EntitlementStoreNotConfiguredError(RuntimeError):
    pass


def load_entitlement(account_id: str, now_iso: str | None = None, platform: str | None = None, product_id: str | None = None) -> dict:
    require_table()
    now_iso = now_iso or _iso_now()
    sk_candidates = _candidate_entitlement_sks(platform, product_id)
    for sk in sk_candidates:
        response = table.get_item(Key={"PK": f"USER#{account_id}", "SK": sk})
        item = response.get("Item")
        if item:
            return apply_campaign_participation_allowance(
                account_id,
                _normalize_entitlement(account_id, item, now_iso, sk),
            )
    return apply_campaign_participation_allowance(
        account_id,
        _default_entitlement(account_id, now_iso, sk_candidates[0]),
    )


def apply_google_play_subscription(entitlement: dict, verification: dict, payload: dict, now_iso: str | None = None) -> dict:
    now_iso = now_iso or _iso_now()
    updated = _normalize_entitlement(entitlement["accountId"], entitlement, now_iso, GOOGLE_PLAY_PRO_SK)
    prior_billing_period_start = updated.get("billingPeriodStartUtc")
    updated["SK"] = GOOGLE_PLAY_PRO_SK
    updated["updatedAt"] = now_iso
    updated["lastVerifiedAtUtc"] = now_iso
    updated["platform"] = payload["platform"]
    updated["productId"] = payload["productId"]
    updated["subscriptionStatus"] = verification["normalizedStatus"]
    updated["billingPeriodStartUtc"] = verification.get("billingPeriodStartUtc")
    updated["billingPeriodEndUtc"] = verification.get("billingPeriodEndUtc")
    updated["isAccessGranted"] = verification["isAccessGranted"]
    updated["purchaseTokenHash"] = payload.get("purchaseTokenHash")
    updated["orderId"] = payload.get("orderId")

    if verification["isAccessGranted"]:
        updated["entitlementTier"] = "PRO"
        updated["monthlyScanLimit"] = PRO_MONTHLY_SCAN_LIMIT
        if prior_billing_period_start != verification.get("billingPeriodStartUtc"):
            updated["remainingMonthlyScans"] = PRO_MONTHLY_SCAN_LIMIT
        else:
            updated["remainingMonthlyScans"] = min(
                PRO_MONTHLY_SCAN_LIMIT,
                int(updated.get("remainingMonthlyScans", PRO_MONTHLY_SCAN_LIMIT)),
            )
    else:
        updated["entitlementTier"] = "FREE"
        updated["monthlyScanLimit"] = FREE_MONTHLY_SCAN_LIMIT
        updated["remainingMonthlyScans"] = min(
            FREE_MONTHLY_SCAN_LIMIT,
            int(updated.get("remainingMonthlyScans", FREE_MONTHLY_SCAN_LIMIT)),
        )

    return apply_campaign_participation_allowance(updated["accountId"], updated)


def save_entitlement(entitlement: dict) -> dict:
    require_table()
    table.put_item(Item=entitlement)
    return entitlement


def build_entitlement_snapshot(entitlement: dict) -> dict:
    normalized = _normalize_entitlement(entitlement["accountId"], entitlement)
    return {
        "tier": normalized["entitlementTier"].lower(),
        "status": normalized["subscriptionStatus"],
        "platform": normalized.get("platform"),
        "productId": normalized.get("productId"),
        "billingPeriodStartUtc": normalized.get("billingPeriodStartUtc"),
        "billingPeriodEndUtc": normalized.get("billingPeriodEndUtc"),
        "lastVerifiedAtUtc": normalized.get("lastVerifiedAtUtc"),
        "isAccessGranted": normalized["isAccessGranted"],
        "monthlyScanLimit": normalized["monthlyScanLimit"],
        "remainingMonthlyScans": normalized["remainingMonthlyScans"],
        "remainingCredits": normalized["remainingCredits"],
    }


def build_usage_snapshot(entitlement_snapshot: dict) -> dict:
    monthly_limit = int(entitlement_snapshot.get("monthlyScanLimit", PRO_MONTHLY_SCAN_LIMIT if entitlement_snapshot["tier"] == "pro" else FREE_MONTHLY_SCAN_LIMIT))
    remaining_count = int(entitlement_snapshot["remainingMonthlyScans"])
    used_count = max(0, monthly_limit - remaining_count)
    period_start = entitlement_snapshot.get("billingPeriodStartUtc")
    period_key = period_start[:7] if isinstance(period_start, str) and len(period_start) >= 7 else _iso_now()[:7]
    return {
        "periodKey": period_key,
        "monthlyLimit": monthly_limit,
        "usedCount": used_count,
        "remainingCount": remaining_count,
    }


def require_table() -> None:
    if not table:
        raise EntitlementStoreNotConfiguredError("The entitlements table is not configured.")


def load_campaign_participation(account_id: str) -> dict:
    if not participation_table:
        return {"state": "not_enrolled", "stateVersion": 0}
    response = participation_table.get_item(
        Key={"PK": f"USER#{account_id}", "SK": CAMPAIGN_PARTICIPATION_SK},
        ConsistentRead=True,
    )
    item = response.get("Item")
    if not item or item.get("state") not in {"enrolled", "withdrawal_pending", "withdrawn"}:
        return {"state": "not_enrolled", "stateVersion": 0}
    try:
        state_version = int(item.get("stateVersion", 0))
    except (TypeError, ValueError):
        state_version = 0
    if item.get("state") == "enrolled" and (
        not _is_uuid4(item.get("consentEpochId"))
        or not isinstance(item.get("noticeVersion"), str)
        or not item["noticeVersion"]
        or isinstance(item.get("stateVersion"), bool)
        or state_version < 1
        or state_version != item.get("stateVersion")
        or (ENVIRONMENT and item.get("environment") != ENVIRONMENT)
    ):
        return {"state": "not_enrolled", "stateVersion": 0}
    return dict(item)


def apply_campaign_participation_allowance(account_id: str, entitlement: dict) -> dict:
    adjusted = dict(entitlement)
    if adjusted.get("entitlementTier") == "PRO":
        return adjusted
    participation = load_campaign_participation(account_id)
    target_limit = (
        PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT
        if participation.get("state") == "enrolled"
        else FREE_MONTHLY_SCAN_LIMIT
    )
    old_limit = max(0, int(adjusted.get("monthlyScanLimit", FREE_MONTHLY_SCAN_LIMIT)))
    remaining = max(0, int(adjusted.get("remainingMonthlyScans", old_limit)))
    used = max(0, old_limit - remaining)
    adjusted["monthlyScanLimit"] = target_limit
    adjusted["remainingMonthlyScans"] = max(0, target_limit - used)
    return adjusted


def _candidate_entitlement_sks(platform: str | None, product_id: str | None) -> list[str]:
    candidates = []
    if platform == "google_play" and product_id == "trustcheck_radar_pro_monthly":
        candidates.append(GOOGLE_PLAY_PRO_SK)
    elif platform or product_id:
        candidates.append(f"ENTITLEMENT#{platform or 'unknown'}#{product_id or 'unknown'}")
    else:
        candidates.append(GOOGLE_PLAY_PRO_SK)
    if DEFAULT_ENTITLEMENT_SK not in candidates:
        candidates.append(DEFAULT_ENTITLEMENT_SK)
    return candidates


def _normalize_entitlement(account_id: str, entitlement: dict | None, now_iso: str | None = None, default_sk: str | None = None) -> dict:
    now_iso = now_iso or _iso_now()
    if not entitlement:
        return _default_entitlement(account_id, now_iso, default_sk or DEFAULT_ENTITLEMENT_SK)

    normalized = dict(entitlement)
    tier = str(normalized.get("entitlementTier", "FREE")).upper()
    if tier not in {"FREE", "PRO"}:
        LOGGER.warning("Unexpected entitlement tier for accountId=%s tier=%s", account_id, tier)
        tier = "FREE"

    subscription_status = _normalize_subscription_status(
        normalized.get("subscriptionStatus"),
        default="active" if tier == "PRO" else "expired",
        account_id=account_id,
    )

    monthly_limit = _coerce_non_negative_int(
        normalized.get("monthlyScanLimit"),
        PRO_MONTHLY_SCAN_LIMIT if tier == "PRO" else FREE_MONTHLY_SCAN_LIMIT,
        field_name="monthlyScanLimit",
        account_id=account_id,
    )
    remaining_monthly = _coerce_non_negative_int(
        normalized.get("remainingMonthlyScans"),
        monthly_limit,
        field_name="remainingMonthlyScans",
        account_id=account_id,
    )
    remaining_credits = _coerce_non_negative_int(
        normalized.get("remainingCredits"),
        0,
        field_name="remainingCredits",
        account_id=account_id,
    )

    if remaining_monthly > monthly_limit:
        LOGGER.warning(
            "remainingMonthlyScans exceeds monthlyScanLimit for accountId=%s remaining=%s limit=%s",
            account_id,
            remaining_monthly,
            monthly_limit,
        )
        remaining_monthly = monthly_limit

    normalized["PK"] = f"USER#{account_id}"
    normalized["SK"] = normalized.get("SK") or default_sk or DEFAULT_ENTITLEMENT_SK
    normalized["accountId"] = account_id
    normalized["entitlementTier"] = tier
    normalized["subscriptionStatus"] = subscription_status
    normalized["monthlyScanLimit"] = monthly_limit
    normalized["remainingMonthlyScans"] = remaining_monthly
    normalized["remainingCredits"] = remaining_credits
    normalized["platform"] = _normalize_optional_string(normalized.get("platform"))
    normalized["productId"] = _normalize_optional_string(normalized.get("productId"))
    normalized["billingPeriodStartUtc"] = _normalize_optional_string(normalized.get("billingPeriodStartUtc"))
    normalized["billingPeriodEndUtc"] = _normalize_optional_string(normalized.get("billingPeriodEndUtc"))
    normalized["lastVerifiedAtUtc"] = _normalize_optional_string(normalized.get("lastVerifiedAtUtc") or normalized.get("lastVerifiedAt"))
    normalized["isAccessGranted"] = bool(normalized.get("isAccessGranted", tier == "PRO" and subscription_status in {"active", "grace", "canceled"}))
    normalized.setdefault("createdAt", now_iso)
    normalized.setdefault("updatedAt", now_iso)
    return normalized


def _default_entitlement(account_id: str, now_iso: str, sk: str) -> dict:
    return {
        "PK": f"USER#{account_id}",
        "SK": sk,
        "accountId": account_id,
        "entitlementTier": "FREE",
        "subscriptionStatus": "expired",
        "platform": "google_play" if sk == GOOGLE_PLAY_PRO_SK else None,
        "productId": "trustcheck_radar_pro_monthly" if sk == GOOGLE_PLAY_PRO_SK else None,
        "billingPeriodStartUtc": None,
        "billingPeriodEndUtc": None,
        "lastVerifiedAtUtc": None,
        "isAccessGranted": False,
        "monthlyScanLimit": FREE_MONTHLY_SCAN_LIMIT,
        "remainingMonthlyScans": FREE_MONTHLY_SCAN_LIMIT,
        "remainingCredits": 0,
        "createdAt": now_iso,
        "updatedAt": now_iso,
    }


def _normalize_subscription_status(value, *, default: str, account_id: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        LOGGER.warning("Invalid subscription status for accountId=%s value=%s", account_id, value)
        return default
    normalized = value.strip().lower()
    if normalized not in SUPPORTED_SUBSCRIPTION_STATUSES:
        LOGGER.warning("Unsupported subscription status for accountId=%s status=%s", account_id, normalized)
        return default
    return normalized


def _normalize_optional_string(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None


def _coerce_non_negative_int(value, default: int, *, field_name: str, account_id: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        if value is not None:
            LOGGER.warning("Invalid entitlement field for accountId=%s field=%s value=%s", account_id, field_name, value)
        return default

    if parsed < 0:
        LOGGER.warning("Negative entitlement field for accountId=%s field=%s value=%s", account_id, field_name, parsed)
        return 0
    return parsed


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _is_uuid4(value) -> bool:
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return parsed.version == 4 and str(parsed) == value
