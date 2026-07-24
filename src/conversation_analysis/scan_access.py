import hashlib
import logging
import time

import boto3

from config import ANALYSIS_ABUSE_TABLE_NAME, SCAN_RATE_LIMIT_MAX_REQUESTS, SCAN_RATE_LIMIT_WINDOW_SECONDS
from errors import AppError
from shared_entitlements import (
    EntitlementStoreNotConfiguredError,
    build_entitlement_snapshot,
    load_entitlement,
    save_entitlement,
)

LOGGER = logging.getLogger(__name__)

dynamodb = boto3.resource("dynamodb")
abuse_table = dynamodb.Table(ANALYSIS_ABUSE_TABLE_NAME) if ANALYSIS_ABUSE_TABLE_NAME else None


def prepare_scan_access(account_id: str, now_epoch: int | None = None) -> dict:
    _require_abuse_table()
    now_epoch = now_epoch or int(time.time())
    _enforce_scan_rate_limit(account_id, now_epoch)

    try:
        entitlement = load_entitlement(account_id)
    except EntitlementStoreNotConfiguredError as err:
        raise AppError("SERVER_UNAVAILABLE", str(err), retryable=False) from err

    snapshot = build_entitlement_snapshot(entitlement)

    if snapshot["remainingMonthlyScans"] > 0:
        return {"accountId": account_id, "consumptionType": "monthly", "entitlement": entitlement, "snapshot": snapshot}

    if snapshot["remainingCredits"] > 0:
        return {"accountId": account_id, "consumptionType": "credit", "entitlement": entitlement, "snapshot": snapshot}

    LOGGER.warning("Entitlement exhausted for accountId=%s", account_id)
    raise AppError(
        "ENTITLEMENT_EXHAUSTED",
        "No remaining scans or credits are available for this account.",
        retryable=False,
    )


def consume_scan_access(access_grant: dict, request_id: str, now_iso: str | None = None) -> dict:
    entitlement = dict(access_grant["entitlement"])
    consumption_type = access_grant["consumptionType"]
    now_iso = now_iso or _iso_now()

    if entitlement.get("lastScanRequestId") == request_id:
        return entitlement

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
    entitlement["lastScanRequestId"] = request_id
    entitlement["lastScanConsumptionType"] = consumption_type
    try:
        save_entitlement(entitlement)
    except EntitlementStoreNotConfiguredError as err:
        raise AppError("SERVER_UNAVAILABLE", str(err), retryable=False) from err
    return entitlement


def _enforce_scan_rate_limit(account_id: str, now_epoch: int) -> None:
    window_start = now_epoch - (now_epoch % SCAN_RATE_LIMIT_WINDOW_SECONDS)
    expires_at = window_start + SCAN_RATE_LIMIT_WINDOW_SECONDS
    account_key = _hashed_account_id(account_id)
    key = {"PK": f"ANALYSIS#SCAN_RATE#{account_key}", "SK": str(window_start)}

    response = abuse_table.update_item(
        Key=key,
        UpdateExpression="ADD requestCount :one SET expiresAt = :expires_at, #ttl = :expires_at, updatedAt = :updated_at",
        ExpressionAttributeNames={
            "#ttl": "ttl",
        },
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


def _require_abuse_table() -> None:
    if not abuse_table:
        raise AppError("SERVER_UNAVAILABLE", "The analysis abuse-control table is not configured.", retryable=False)


def _iso_now() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()


def _hashed_account_id(account_id: str) -> str:
    return hashlib.sha256(account_id.encode("utf-8")).hexdigest()
