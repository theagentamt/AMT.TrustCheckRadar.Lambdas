from errors import AppError
from config import ENTITLEMENT_PLATFORM, ENTITLEMENT_PRODUCT_ID, ENTITLEMENT_USAGE_PERIOD_MODE
from shared_entitlements import (
    EntitlementStoreNotConfiguredError,
    build_entitlement_snapshot,
    build_usage_snapshot,
    load_entitlement,
)
from shared_entitlements.service import table as entitlements_table


def get_entitlement_snapshot(account_id: str) -> dict:
    try:
        entitlement = load_entitlement(
            account_id,
            platform=ENTITLEMENT_PLATFORM,
            product_id=ENTITLEMENT_PRODUCT_ID,
        )
    except EntitlementStoreNotConfiguredError as err:
        raise AppError("SERVER_UNAVAILABLE", str(err), retryable=False) from err

    entitlement_snapshot = build_entitlement_snapshot(entitlement)
    usage_snapshot = _build_usage_snapshot(account_id, entitlement_snapshot)

    return {
        "entitlement": {
            "tier": entitlement_snapshot["tier"],
            "status": entitlement_snapshot["status"],
            "platform": entitlement_snapshot["platform"],
            "productId": entitlement_snapshot["productId"],
            "billingPeriodStart": entitlement_snapshot["billingPeriodStartUtc"],
            "billingPeriodEnd": entitlement_snapshot["billingPeriodEndUtc"],
            "isAccessGranted": entitlement_snapshot["isAccessGranted"],
        },
        "usage": usage_snapshot,
    }


def _build_usage_snapshot(account_id: str, entitlement_snapshot: dict) -> dict:
    derived_usage = build_usage_snapshot(entitlement_snapshot)
    usage_item = _load_usage_item(account_id, entitlement_snapshot, derived_usage["periodKey"])

    if usage_item:
        monthly_limit = _coerce_int(
            usage_item.get("monthlyLimit", usage_item.get("limit")),
            derived_usage["monthlyLimit"],
        )
        used_count = _coerce_int(usage_item.get("usedCount"), derived_usage["usedCount"])
        remaining_count = _coerce_int(
            usage_item.get("remainingCount", usage_item.get("remaining")),
            max(0, monthly_limit - used_count),
        )
    else:
        monthly_limit = derived_usage["monthlyLimit"]
        used_count = derived_usage["usedCount"]
        remaining_count = derived_usage["remainingCount"]

    return {
        "periodMode": ENTITLEMENT_USAGE_PERIOD_MODE,
        "periodKey": derived_usage["periodKey"],
        "limit": monthly_limit,
        "usedCount": used_count,
        "remaining": remaining_count,
    }


def _load_usage_item(account_id: str, entitlement_snapshot: dict, fallback_period_key: str) -> dict | None:
    if not entitlements_table:
        raise AppError("SERVER_UNAVAILABLE", "The entitlements table is not configured.", retryable=False)

    candidate_keys = []
    billing_period_start = entitlement_snapshot.get("billingPeriodStartUtc")
    if isinstance(billing_period_start, str) and billing_period_start:
        candidate_keys.append(billing_period_start)
    if fallback_period_key not in candidate_keys:
        candidate_keys.append(fallback_period_key)

    for period_key in candidate_keys:
        response = entitlements_table.get_item(Key={"PK": f"USER#{account_id}", "SK": f"USAGE#{period_key}"})
        item = response.get("Item")
        if item:
            return item
    return None


def _coerce_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)

