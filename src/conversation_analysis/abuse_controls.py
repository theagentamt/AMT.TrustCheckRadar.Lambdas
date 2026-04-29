from datetime import UTC, datetime
import hashlib
import re
import time

import boto3
from botocore.exceptions import ClientError

from config import ANALYSIS_ABUSE_TABLE_NAME, RATE_LIMIT_MAX_REQUESTS, RATE_LIMIT_WINDOW_SECONDS, REQUEST_ID_TTL_SECONDS
from errors import AppError

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(ANALYSIS_ABUSE_TABLE_NAME) if ANALYSIS_ABUSE_TABLE_NAME else None


def extract_identity(event: dict) -> str:
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}

    jwt_claims = (authorizer.get("jwt") or {}).get("claims")
    if isinstance(jwt_claims, dict):
        sub = jwt_claims.get("sub")
        if isinstance(sub, str) and sub.strip():
            return _normalize_identity(sub)

    legacy_claims = authorizer.get("claims")
    if isinstance(legacy_claims, dict):
        sub = legacy_claims.get("sub")
        if isinstance(sub, str) and sub.strip():
            return _normalize_identity(sub)

    principal_id = authorizer.get("principalId")
    if isinstance(principal_id, str) and principal_id.strip():
        return _normalize_identity(principal_id)

    source_ip = ((request_context.get("identity") or {}).get("sourceIp") or _header_value(event, "x-forwarded-for"))
    if isinstance(source_ip, str) and source_ip.strip():
        return _normalize_identity(source_ip.split(",")[0].strip())

    return "anonymous"


def check_or_lock_request(identity: str, request_id: str, now_epoch: int | None = None):
    _require_table()
    now_epoch = now_epoch or int(time.time())
    identity_key = _hashed_identity(identity)
    existing = _get_request_record(identity_key, request_id)
    if existing and int(existing.get("expiresAt", 0)) > now_epoch:
        raise AppError(
            "RATE_LIMITED",
            "A matching analysis request is already in progress or was recently completed. Please retry shortly.",
            retryable=True,
        )

    try:
        table.put_item(
            Item={
                "PK": f"ANALYSIS#REQUEST#{identity_key}",
                "SK": request_id,
                "status": "IN_PROGRESS",
                "createdAt": _iso_now(),
                "updatedAt": _iso_now(),
                "expiresAt": now_epoch + REQUEST_ID_TTL_SECONDS,
                "ttl": now_epoch + REQUEST_ID_TTL_SECONDS,
            },
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise AppError(
                "RATE_LIMITED",
                "A matching analysis request is already in progress or was recently completed. Please retry shortly.",
                retryable=True,
            ) from err
        raise
    return {"state": "locked"}


def enforce_rate_limit(identity: str, now_epoch: int | None = None):
    _require_table()
    now_epoch = now_epoch or int(time.time())
    window_start = now_epoch - (now_epoch % RATE_LIMIT_WINDOW_SECONDS)
    expires_at = window_start + RATE_LIMIT_WINDOW_SECONDS
    identity_key = _hashed_identity(identity)

    response = table.update_item(
        Key={"PK": f"ANALYSIS#RATE#{identity_key}", "SK": str(window_start)},
        UpdateExpression="ADD requestCount :one SET expiresAt = :expires_at, ttl = :expires_at, updatedAt = :updated_at",
        ExpressionAttributeValues={
            ":one": 1,
            ":expires_at": expires_at,
            ":updated_at": _iso_now(),
        },
        ReturnValues="UPDATED_NEW",
    )
    request_count = int(response["Attributes"]["requestCount"])
    if request_count > RATE_LIMIT_MAX_REQUESTS:
        raise AppError(
            "RATE_LIMITED",
            "Too many analysis requests have been submitted. Please retry later.",
            retryable=True,
        )


def complete_request(identity: str, request_id: str, now_epoch: int | None = None):
    _require_table()
    now_epoch = now_epoch or int(time.time())
    identity_key = _hashed_identity(identity)
    table.update_item(
        Key={"PK": f"ANALYSIS#REQUEST#{identity_key}", "SK": request_id},
        UpdateExpression="SET #status = :status, completedAt = :completed_at, updatedAt = :updated_at, expiresAt = :expires_at, ttl = :expires_at",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":status": "COMPLETED",
            ":completed_at": _iso_now(),
            ":updated_at": _iso_now(),
            ":expires_at": now_epoch + REQUEST_ID_TTL_SECONDS,
        },
    )


def release_request(identity: str, request_id: str):
    if not table:
        return
    identity_key = _hashed_identity(identity)
    table.delete_item(Key={"PK": f"ANALYSIS#REQUEST#{identity_key}", "SK": request_id})


def _get_request_record(identity_key: str, request_id: str):
    response = table.get_item(Key={"PK": f"ANALYSIS#REQUEST#{identity_key}", "SK": request_id})
    return response.get("Item")


def _normalize_identity(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._:@-]", "_", value.strip())
    return cleaned[:200] or "anonymous"


def _hashed_identity(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _header_value(event: dict, name: str):
    headers = event.get("headers") or {}
    return headers.get(name) or headers.get(name.title())


def _require_table():
    if not table:
        raise AppError("SERVER_UNAVAILABLE", "The analysis abuse-control table is not configured.", retryable=False)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()
