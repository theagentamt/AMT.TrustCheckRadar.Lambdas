from datetime import UTC, datetime
import hashlib
import logging
import time

import boto3

from config import (
    ANALYSIS_ABUSE_TABLE_NAME,
    ENTITLEMENTS_TABLE_NAME,
    FREE_MONTHLY_SCAN_LIMIT,
    PRO_MONTHLY_SCAN_LIMIT,
    SCAN_RATE_LIMIT_MAX_REQUESTS,
    SCAN_RATE_LIMIT_WINDOW_SECONDS,
)
from errors import AppError

LOGGER = logging.getLogger(__name__)

dynamodb = boto3.resource("dynamodb")
entitlements_table = dynamodb.Table(ENTITLEMENTS_TABLE_NAME) if ENTITLEMENTS_TABLE_NAME else None
abuse_table = dynamodb.Table(ANALYSIS_ABUSE_TABLE_NAME) if ANALYSIS_ABUSE_TABLE_NAME else None


def prepare_scan_access(account_id: str, now_epoch: int | None = None) -> dict:
    _require_tables()
    now_epoch = now_epoch or int(time.time())
    _enforce_scan_rate_limit(account_id, now_epoch)

    existing = _get_entitlement(account_id)
    entitlement = _normalize_entitlement(account_id, existing)

    if entitlement["remainingMonthlyScans"] > 0:
        return {"accountId": account_id, "consumptionType": "monthly", "entitlement": entitlement}

    if entitlement["remainingCredits"] > 0:
        return {"accountId": account_id, "consumptionType": "credit", "entitlement": entitlement}

    LOGGER.warning("Entitlement exhausted for accountId=%s", account_id)
    raise AppError(
        "ENTITLEMENT_EXHAUSTED",
        "No remaining scans or credits are available for this account.",
        retryable=False,
    )


def consume_scan_access(access_grant: dict, now_iso: str | None = None) -> dict:
    _require_tables()
    entitlement = dict(access_grant["entitlement"])
    consumption_type = access_grant["consumptionType"]
    now_iso = now_iso or _iso_now()

    if consumption_type == "monthly":
        if entitlement["remainingMonthlyScans"] <= 0:
            raise AppError(
                "ENTITLEMENT_EXHAUSTED",
                "No remaining monthly scans are available for this account.",
                retryable=False,
            )
        entitlement["remainingMonthlyScans"] -= 1
    elif consumption_type == "credit":
        if entitlement["remainingCredits"] <= 0:
            raise AppError(
                "ENTITLEMENT_EXHAUSTED",
                "No remaining credits are available for this account.",
                retryable=False,
            )
        entitlement["remainingCredits"] -= 1
    else:
        LOGGER.warning(
            "Unexpected scan consumption type for accountId=%s consumptionType=%s",
            access_grant["accountId"],
            consumption_type,
        )
        raise AppError("SERVER_UNAVAILABLE", "The scan access configuration is invalid.", retryable=False)

    entitlement["updatedAt"] = now_iso
    entitlement["lastScanAt"] = now_iso
    entitlement["lastScanConsumptionType"] = consumption_type
    entitlements_table.put_item(Item=entitlement)
    return entitlement


def _get_entitlement(account_id: str) -> dict | None:
    response = entitlements_table.get_item(Key={"PK": f"USER#{account_id}", "SK": "ENTITLEMENT"})
    return response.get("Item")


def _normalize_entitlement(account_id: str, entitlement: dict | None) -> dict:
    now_iso = _iso_now()
    if not entitlement:
        return _default_entitlement(account_id, now_iso)

    normalized = dict(entitlement)
    tier = str(normalized.get("entitlementTier", "FREE")).upper()
    if tier not in {"FREE", "PRO"}:
        LOGGER.warning("Unexpected entitlement tier for accountId=%s tier=%s", account_id, tier)
        tier = "FREE"

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
    normalized["SK"] = "ENTITLEMENT"
    normalized["accountId"] = account_id
    normalized["entitlementTier"] = tier
    normalized["monthlyScanLimit"] = monthly_limit
    normalized["remainingMonthlyScans"] = remaining_monthly
    normalized["remainingCredits"] = remaining_credits
    normalized.setdefault("createdAt", now_iso)
    normalized.setdefault("updatedAt", now_iso)
    return normalized


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


def _enforce_scan_rate_limit(account_id: str, now_epoch: int) -> None:
    window_start = now_epoch - (now_epoch % SCAN_RATE_LIMIT_WINDOW_SECONDS)
    expires_at = window_start + SCAN_RATE_LIMIT_WINDOW_SECONDS
    account_key = _hashed_account_id(account_id)
    key = {"PK": f"ANALYSIS#SCAN_RATE#{account_key}", "SK": str(window_start)}

    response = abuse_table.update_item(
        Key=key,
        UpdateExpression="ADD requestCount :one SET expiresAt = :expires_at, ttl = :expires_at, updatedAt = :updated_at",
        ExpressionAttributeValues={
            ":one": 1,
            ":expires_at": expires_at,
            ":updated_at": _iso_now(),
        },
        ReturnValues="UPDATED_NEW",
    )
    request_count = int(response["Attributes"]["requestCount"])
    if request_count > SCAN_RATE_LIMIT_MAX_REQUESTS:
        LOGGER.warning(
            "Scan abuse cap exceeded for accountId=%s requestCount=%s windowStart=%s",
            account_id,
            request_count,
            window_start,
        )
        raise AppError(
            "RATE_LIMITED",
            "Too many scans have been submitted for this account. Please retry later.",
            retryable=True,
        )


def _require_tables() -> None:
    if not entitlements_table:
        raise AppError("SERVER_UNAVAILABLE", "The entitlements table is not configured.", retryable=False)
    if not abuse_table:
        raise AppError("SERVER_UNAVAILABLE", "The analysis abuse-control table is not configured.", retryable=False)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _hashed_account_id(account_id: str) -> str:
    return hashlib.sha256(account_id.encode("utf-8")).hexdigest()
