from datetime import UTC, datetime

import boto3

from config import ENTITLEMENTS_TABLE_NAME
from errors import AppError
from verification import verify_purchase

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(ENTITLEMENTS_TABLE_NAME) if ENTITLEMENTS_TABLE_NAME else None

FREE_MONTHLY_SCAN_LIMIT = 5


def process_purchase_handoff(*, account_id: str, payload: dict) -> dict:
    _require_table()

    verification = verify_purchase(payload)
    verification_status = verification["status"]
    now_iso = datetime.now(UTC).isoformat()

    existing = _get_entitlement(account_id)
    current = _default_entitlement(account_id, now_iso) if not existing else dict(existing)

    if verification_status == "accepted":
        updated = _apply_accepted_purchase(current, payload["productId"], now_iso)
        table.put_item(Item=updated)
        return _build_response("accepted", verification["reason"], updated)

    if verification_status == "pending":
        return _build_response("pending", verification["reason"], current)

    if verification_status == "rejected":
        return _build_response("rejected", verification["reason"], current)

    return _build_response("failed_retryable", verification["reason"], current)


def _apply_accepted_purchase(entitlement: dict, product_id: str, now_iso: str) -> dict:
    updated = dict(entitlement)
    updated["updatedAt"] = now_iso
    updated["lastVerifiedAt"] = now_iso

    if product_id == "pro_monthly":
        updated["entitlementTier"] = "PRO"
        updated["monthlyScanLimit"] = 100
        updated["remainingMonthlyScans"] = 100
    elif product_id == "credits_20":
        updated["remainingCredits"] = int(updated.get("remainingCredits", 0)) + 20

    return updated


def _build_response(verification_status: str, verification_reason: str, entitlement: dict) -> dict:
    return {
        "verificationStatus": verification_status,
        "verificationReason": verification_reason,
        "entitlement": {
            "tier": entitlement["entitlementTier"],
            "remainingMonthlyScans": entitlement["remainingMonthlyScans"],
            "remainingCredits": entitlement["remainingCredits"],
        },
    }


def _get_entitlement(account_id: str):
    response = table.get_item(Key={"PK": f"USER#{account_id}", "SK": "ENTITLEMENT"})
    return response.get("Item")


def _default_entitlement(account_id: str, now_iso: str) -> dict:
    return {
        "PK": f"USER#{account_id}",
        "SK": "ENTITLEMENT",
        "accountId": account_id,
        "entitlementTier": "FREE",
        "monthlyScanLimit": FREE_MONTHLY_SCAN_LIMIT,
        "remainingMonthlyScans": FREE_MONTHLY_SCAN_LIMIT,
        "remainingCredits": 0,
        "createdAt": now_iso,
        "updatedAt": now_iso,
        "lastVerifiedAt": None,
    }


def _require_table():
    if not table:
        raise AppError("SERVER_UNAVAILABLE", "The entitlements table is not configured.", retryable=False)
