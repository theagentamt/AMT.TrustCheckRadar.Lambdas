import hashlib
import logging
import time
import uuid

import boto3
from botocore.exceptions import ClientError

from config import (
    ANALYSIS_ABUSE_TABLE_NAME,
    APP_ENVIRONMENT,
    CAMPAIGN_OBSERVATION_RETENTION_HOURS,
    CAMPAIGN_OUTBOX_TABLE_NAME,
    CAMPAIGN_SCHEMA_VERSION,
    ENTITLEMENTS_TABLE_NAME,
    REQUEST_ID_TTL_SECONDS,
    SCAN_RATE_LIMIT_MAX_REQUESTS,
    SCAN_RATE_LIMIT_WINDOW_SECONDS,
)
from errors import AppError
from shared_entitlements import (
    EntitlementStoreNotConfiguredError,
    build_entitlement_snapshot,
    load_entitlement,
    save_entitlement,
)

LOGGER = logging.getLogger(__name__)

dynamodb = boto3.resource("dynamodb")
dynamodb_client = boto3.client("dynamodb")
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
    now_iso = now_iso or _iso_now()
    entitlement = _build_consumed_entitlement(access_grant, request_id, now_iso)
    try:
        save_entitlement(entitlement)
    except EntitlementStoreNotConfiguredError as err:
        raise AppError("SERVER_UNAVAILABLE", str(err), retryable=False) from err
    return entitlement


def commit_scan_and_request(
    access_grant: dict,
    request_id: str,
    payload_hash: str,
    response: dict,
    campaign_payload: dict | None = None,
    statistics_event_id: str | None = None,
    now_epoch: int | None = None,
    now_iso: str | None = None,
) -> dict:
    _require_abuse_table()
    if not ENTITLEMENTS_TABLE_NAME:
        raise AppError("SERVER_UNAVAILABLE", "The entitlements table is not configured.", retryable=False)
    if response.get("requestId") != request_id:
        raise AppError("SERVER_UNAVAILABLE", "The analysis result does not match its request.", retryable=False)
    now_epoch = now_epoch or int(time.time())
    now_iso = now_iso or _iso_now()
    current = dict(access_grant["entitlement"])
    updated = _build_consumed_entitlement(access_grant, request_id, now_iso)
    account_id = access_grant["accountId"]
    account_hash = _hashed_account_id(account_id)
    request_key = {
        "PK": f"ANALYSIS#REQUEST#{account_hash}",
        "SK": request_id,
    }
    consumption_key = {
        "PK": f"ANALYSIS#CONSUMPTION#{account_hash}",
        "SK": request_id,
    }
    expires_at = now_epoch + REQUEST_ID_TTL_SECONDS
    transaction = [
        {
            "Update": {
                "TableName": ANALYSIS_ABUSE_TABLE_NAME,
                "Key": _serialize_item(request_key),
                "UpdateExpression": (
                    "SET #status = :completed, completedAt = :completed_at, "
                    "updatedAt = :updated_at, expiresAt = :expires_at, #ttl = :expires_at"
                ),
                "ConditionExpression": (
                    "#status = :result_ready AND payloadHash = :payload_hash"
                ),
                "ExpressionAttributeNames": {
                    "#status": "status",
                    "#ttl": "ttl",
                },
                "ExpressionAttributeValues": _serialize_item(
                    {
                        ":completed": "COMPLETED",
                        ":completed_at": now_iso,
                        ":updated_at": now_iso,
                        ":expires_at": expires_at,
                        ":result_ready": "RESULT_READY",
                        ":payload_hash": payload_hash,
                    }
                ),
            }
        },
        {
            "Put": {
                "TableName": ANALYSIS_ABUSE_TABLE_NAME,
                "Item": _serialize_item(
                    consumption_key
                    | {
                        "accountIdHash": account_hash,
                        "consumptionType": access_grant["consumptionType"],
                        "createdAt": now_iso,
                        "expiresAt": expires_at,
                        "ttl": expires_at,
                    }
                ),
                "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
            }
        },
        {
            "Put": {
                "TableName": ENTITLEMENTS_TABLE_NAME,
                "Item": _serialize_item(updated),
                "ConditionExpression": (
                    "attribute_not_exists(PK) OR "
                    "(remainingMonthlyScans = :monthly AND remainingCredits = :credits "
                    "AND updatedAt = :updated_at)"
                ),
                "ExpressionAttributeValues": _serialize_item(
                    {
                        ":monthly": int(current["remainingMonthlyScans"]),
                        ":credits": int(current["remainingCredits"]),
                        ":updated_at": current["updatedAt"],
                    }
                ),
            }
        },
    ]
    if campaign_payload and campaign_payload.get("campaignConsentGranted"):
        if not CAMPAIGN_OUTBOX_TABLE_NAME or APP_ENVIRONMENT not in {"dev", "uat", "prod"}:
            raise AppError(
                "SERVER_UNAVAILABLE",
                "Campaign publishing is not configured for this environment.",
                retryable=False,
            )
        statistics_event_id = statistics_event_id or str(uuid.uuid4())
        outbox_expiry = now_epoch + min(CAMPAIGN_OBSERVATION_RETENTION_HOURS, 72) * 60 * 60
        transaction.append(
            {
                "Put": {
                    "TableName": CAMPAIGN_OUTBOX_TABLE_NAME,
                    "Item": _serialize_item(
                        {
                            "PK": f"EVENT#{statistics_event_id}",
                            "SK": "OBSERVATION_READY",
                            "schemaVersion": CAMPAIGN_SCHEMA_VERSION,
                            "recordVersion": 1,
                            "eventType": "campaign.observation.ready",
                            "environment": APP_ENVIRONMENT,
                            "statisticsEventId": statistics_event_id,
                            "accountId": account_id,
                            "campaignConsentGranted": True,
                            "observedAtEpoch": now_epoch,
                            "sourceType": campaign_payload["sourceType"],
                            "sanitizedText": campaign_payload["sanitizedText"],
                            "riskLevel": response.get("riskLevel", "unknown"),
                            "signalIds": response.get("signals", []),
                            "expiresAt": outbox_expiry,
                        }
                    ),
                    "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                }
            }
        )
    try:
        dynamodb_client.transact_write_items(TransactItems=transaction)
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "TransactionCanceledException":
            raise AppError(
                "REQUEST_IN_PROGRESS",
                "Account access changed while this analysis result was being committed.",
                retryable=True,
                details=[{"retryAfterSeconds": 1}],
            ) from err
        raise
    return updated


def _build_consumed_entitlement(
    access_grant: dict,
    request_id: str,
    now_iso: str,
) -> dict:
    entitlement = dict(access_grant["entitlement"])
    consumption_type = access_grant["consumptionType"]
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


def _serialize_item(value: dict) -> dict:
    return {key: _serialize_value(item) for key, item in value.items()}


def _serialize_value(value):
    if value is None:
        return {"NULL": True}
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, (int, float)):
        return {"N": str(value)}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, list):
        return {"L": [_serialize_value(item) for item in value]}
    if isinstance(value, dict):
        return {"M": _serialize_item(value)}
    raise TypeError(f"Unsupported DynamoDB value type: {type(value).__name__}")
