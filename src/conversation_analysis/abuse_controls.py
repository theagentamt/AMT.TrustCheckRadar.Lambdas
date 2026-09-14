from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
import re
import time
import uuid

import boto3
from botocore.exceptions import ClientError

from config import (
    ANALYSIS_ABUSE_TABLE_NAME,
    PROCESSING_LEASE_SECONDS,
    RATE_LIMIT_MAX_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS,
    REQUEST_ID_TTL_SECONDS,
)
from errors import AppError
from shared_history import HistoryError, HistorySettings, response_from_history_item

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

    raise AppError(
        "UNAUTHORIZED",
        "A trusted caller identity is required for analysis requests.",
        retryable=False,
    )


def check_or_lock_request(identity: str, request_id: str, payload: dict, now_epoch: int | None = None):
    _require_table()
    now_epoch = now_epoch or int(time.time())
    identity_key = _hashed_identity(identity)
    payload_hash = _payload_hash(payload)
    existing = _get_request_record(identity_key, request_id)
    if existing:
        if int(existing.get("expiresAt", 0)) > now_epoch or HistorySettings.from_env().durable_replay_enabled:
            _assert_matching_payload(existing, payload_hash)
        status = existing.get("status")
        if status == "COMPLETED_ERASED":
            _result_unavailable()
        if status == "RESULT_READY" and isinstance(existing.get("response"), dict):
            result = {
                "state": "result_ready",
                "payloadHash": payload_hash,
                "response": _to_json_compatible(existing["response"]),
                "statisticsEventId": existing.get("statisticsEventId"),
            }
            for field in ("campaignAuthorization", "historyAuthorization"):
                if isinstance(existing.get(field), dict):
                    result[field] = _to_json_compatible(existing[field])
            return result
        if status == "RETRYABLE":
            return _take_over_retryable(identity_key, request_id, payload_hash, existing, now_epoch)
    if existing and int(existing.get("expiresAt", 0)) <= now_epoch:
        durable = _durable_replay(identity, request_id, payload_hash, now_epoch)
        if durable is not None:
            return durable
        return _replace_expired_request(
            identity_key,
            request_id,
            payload_hash,
            existing,
            now_epoch,
        )
    if existing and int(existing.get("expiresAt", 0)) > now_epoch:
        status = existing.get("status")
        if status == "COMPLETED" and isinstance(existing.get("response"), dict):
            durable = _durable_replay(identity, request_id, payload_hash, now_epoch)
            if durable is not None:
                return durable
            return {
                "state": "completed",
                "payloadHash": payload_hash,
                "response": _to_json_compatible(existing["response"]),
            }
        lease_expires_at = int(existing.get("leaseExpiresAt", 0))
        if status == "PROCESSING" and lease_expires_at > now_epoch:
            retry_after = max(1, lease_expires_at - now_epoch)
            raise AppError(
                "REQUEST_IN_PROGRESS",
                "This analysis request is already processing.",
                retryable=True,
                details=[{"retryAfterSeconds": retry_after}],
            )
        if status == "PROCESSING":
            return _take_over_expired_lease(
                identity_key,
                request_id,
                payload_hash,
                existing,
                now_epoch,
            )
        raise AppError(
            "SERVER_UNAVAILABLE",
            "The stored request state is invalid.",
            retryable=False,
        )

    durable = _durable_replay(identity, request_id, payload_hash, now_epoch)
    if durable is not None:
        return durable
    lease_token = str(uuid.uuid4())
    try:
        table.put_item(
            Item={
                "PK": f"ANALYSIS#REQUEST#{identity_key}",
                "SK": request_id,
                "status": "PROCESSING",
                "payloadHash": payload_hash,
                "leaseToken": lease_token,
                "leaseExpiresAt": now_epoch + PROCESSING_LEASE_SECONDS,
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
                "REQUEST_IN_PROGRESS",
                "This analysis request is already processing.",
                retryable=True,
                details=[{"retryAfterSeconds": PROCESSING_LEASE_SECONDS}],
            ) from err
        raise
    return {
        "state": "processing",
        "payloadHash": payload_hash,
        "leaseToken": lease_token,
    }


def enforce_rate_limit(identity: str, now_epoch: int | None = None):
    _require_table()
    now_epoch = now_epoch or int(time.time())
    window_start = now_epoch - (now_epoch % RATE_LIMIT_WINDOW_SECONDS)
    expires_at = window_start + RATE_LIMIT_WINDOW_SECONDS
    identity_key = _hashed_identity(identity)

    response = table.update_item(
        Key={"PK": f"ANALYSIS#RATE#{identity_key}", "SK": str(window_start)},
        UpdateExpression="ADD requestCount :one SET expiresAt = :expires_at, #ttl = :expires_at, updatedAt = :updated_at",
        ExpressionAttributeNames={"#ttl": "ttl"},
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


def store_result(
    identity: str,
    request_id: str,
    payload_hash: str,
    lease_token: str,
    response: dict,
    statistics_event_id: str | None = None,
    campaign_authorization: dict | None = None,
    history_authorization: dict | None = None,
    now_epoch: int | None = None,
):
    _require_table()
    now_epoch = now_epoch or int(time.time())
    identity_key = _hashed_identity(identity)
    update_expression = (
        "SET #status = :result_ready, #response = :response, resultReadyAt = :result_ready_at, "
        "updatedAt = :updated_at, expiresAt = :expires_at, #ttl = :expires_at"
    )
    expression_values = {
        ":processing": "PROCESSING",
        ":result_ready": "RESULT_READY",
        ":payload_hash": payload_hash,
        ":lease_token": lease_token,
        ":response": _to_dynamodb_compatible(response),
        ":result_ready_at": _iso_now(),
        ":updated_at": _iso_now(),
        ":expires_at": now_epoch + REQUEST_ID_TTL_SECONDS,
    }
    if statistics_event_id:
        update_expression += ", statisticsEventId = :statistics_event_id"
        expression_values[":statistics_event_id"] = statistics_event_id
    if campaign_authorization:
        update_expression += ", campaignAuthorization = :campaign_authorization"
        expression_values[":campaign_authorization"] = _to_dynamodb_compatible(campaign_authorization)
    if history_authorization:
        update_expression += ", historyAuthorization = :history_authorization"
        expression_values[":history_authorization"] = _to_dynamodb_compatible(history_authorization)
    update_expression += " REMOVE leaseToken, leaseExpiresAt"
    table.update_item(
        Key={"PK": f"ANALYSIS#REQUEST#{identity_key}", "SK": request_id},
        UpdateExpression=update_expression,
        ConditionExpression=(
            "#status = :processing AND payloadHash = :payload_hash AND leaseToken = :lease_token"
        ),
        ExpressionAttributeNames={"#status": "status", "#response": "response", "#ttl": "ttl"},
        ExpressionAttributeValues=expression_values,
    )


def release_request(identity: str, request_id: str, lease_token: str | None = None):
    if not table:
        return
    identity_key = _hashed_identity(identity)
    existing = _get_request_record(identity_key, request_id)
    if lease_token and isinstance((existing or {}).get("historyAuthorization"), dict):
        try:
            table.update_item(
                Key={"PK": f"ANALYSIS#REQUEST#{identity_key}", "SK": request_id},
                UpdateExpression="SET #status = :retryable, updatedAt = :updated_at REMOVE leaseToken, leaseExpiresAt",
                ConditionExpression="#status = :processing AND leaseToken = :lease_token AND attribute_exists(historyAuthorization)",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":retryable": "RETRYABLE", ":processing": "PROCESSING",
                    ":lease_token": lease_token, ":updated_at": _iso_now(),
                },
            )
            return
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
    kwargs = {"Key": {"PK": f"ANALYSIS#REQUEST#{identity_key}", "SK": request_id}}
    if lease_token:
        kwargs |= {
            "ConditionExpression": "#status = :processing AND leaseToken = :lease_token",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": {
                ":processing": "PROCESSING",
                ":lease_token": lease_token,
            },
        }
    try:
        table.delete_item(**kwargs)
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise


def _get_request_record(identity_key: str, request_id: str):
    response = table.get_item(Key={"PK": f"ANALYSIS#REQUEST#{identity_key}", "SK": request_id})
    return response.get("Item")


def _take_over_expired_lease(
    identity_key: str,
    request_id: str,
    payload_hash: str,
    existing: dict,
    now_epoch: int,
) -> dict:
    lease_token = str(uuid.uuid4())
    try:
        table.update_item(
            Key={"PK": f"ANALYSIS#REQUEST#{identity_key}", "SK": request_id},
            UpdateExpression=(
                "SET leaseToken = :lease_token, leaseExpiresAt = :lease_expires_at, "
                "updatedAt = :updated_at, expiresAt = :expires_at, #ttl = :expires_at"
            ),
            ConditionExpression=(
                "#status = :processing AND payloadHash = :payload_hash "
                "AND leaseExpiresAt = :previous_lease_expires_at"
            ),
            ExpressionAttributeNames={"#status": "status", "#ttl": "ttl"},
            ExpressionAttributeValues={
                ":processing": "PROCESSING",
                ":payload_hash": payload_hash,
                ":previous_lease_expires_at": int(existing.get("leaseExpiresAt", 0)),
                ":lease_token": lease_token,
                ":lease_expires_at": now_epoch + PROCESSING_LEASE_SECONDS,
                ":updated_at": _iso_now(),
                ":expires_at": now_epoch + REQUEST_ID_TTL_SECONDS,
            },
        )
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise AppError(
                "REQUEST_IN_PROGRESS",
                "This analysis request is already processing.",
                retryable=True,
                details=[{"retryAfterSeconds": PROCESSING_LEASE_SECONDS}],
            ) from err
        raise
    return {
        "state": "processing",
        "payloadHash": payload_hash,
        "leaseToken": lease_token,
        "takeover": True,
        **({"historyAuthorization": _to_json_compatible(existing["historyAuthorization"])} if isinstance(existing.get("historyAuthorization"), dict) else {}),
    }


def _take_over_retryable(identity_key, request_id, payload_hash, existing, now_epoch):
    lease_token = str(uuid.uuid4())
    try:
        table.update_item(
            Key={"PK": f"ANALYSIS#REQUEST#{identity_key}", "SK": request_id},
            UpdateExpression="SET #status = :processing, leaseToken = :lease_token, leaseExpiresAt = :lease_expires_at, updatedAt = :updated_at, expiresAt = :expires_at, #ttl = :expires_at",
            ConditionExpression="#status = :retryable AND payloadHash = :payload_hash",
            ExpressionAttributeNames={"#status": "status", "#ttl": "ttl"},
            ExpressionAttributeValues={
                ":processing": "PROCESSING", ":retryable": "RETRYABLE", ":payload_hash": payload_hash,
                ":lease_token": lease_token, ":lease_expires_at": now_epoch + PROCESSING_LEASE_SECONDS,
                ":updated_at": _iso_now(), ":expires_at": now_epoch + REQUEST_ID_TTL_SECONDS,
            },
        )
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise AppError("REQUEST_IN_PROGRESS", "This analysis request changed while retrying.", retryable=True) from err
        raise
    return {
        "state": "processing", "payloadHash": payload_hash, "leaseToken": lease_token,
        "historyAuthorization": _to_json_compatible(existing["historyAuthorization"]), "takeover": True,
    }


def _durable_replay(identity, request_id, payload_hash, now_epoch):
    settings = HistorySettings.from_env()
    if not settings.durable_replay_enabled:
        return None
    try:
        settings.validate_durable_replay()
    except HistoryError as err:
        raise AppError("SERVER_UNAVAILABLE", err.message, retryable=err.retryable) from err
    control = dynamodb.Table(settings.control_table_name)
    locator = control.get_item(
        Key={"PK": f"USER#{identity}", "SK": f"REQUEST#{request_id}"}, ConsistentRead=True
    ).get("Item")
    if not locator:
        return None
    if locator.get("payloadHash") != payload_hash:
        raise AppError("IDEMPOTENCY_CONFLICT", "The request ID is already bound to different analysis content.", retryable=False)
    state = control.get_item(
        Key={"PK": f"USER#{identity}", "SK": "STATE"}, ConsistentRead=True
    ).get("Item")
    if (
        not state
        or state.get("accountStatus") != "ACTIVE"
        or locator.get("status") != "ACTIVE"
        or int(locator.get("historyGeneration", -1)) != int(state.get("historyGeneration", -2))
        or int(locator.get("contentExpiresAt", 0)) <= now_epoch
    ):
        _result_unavailable()
    content = dynamodb.Table(settings.content_table_name).get_item(
        Key={
            "PK": f"USER#{identity}#HISTORY#{int(locator['historyGeneration'])}",
            "SK": locator.get("contentSortKey"),
        },
        ConsistentRead=True,
    ).get("Item")
    if not content or int(content.get("expiresAt", 0)) <= now_epoch:
        _result_unavailable()
    try:
        response = response_from_history_item(content, settings)
    except HistoryError as err:
        raise AppError("SERVER_UNAVAILABLE", err.message, retryable=False) from err
    return {"state": "completed", "payloadHash": payload_hash, "response": response}


def _result_unavailable():
    raise AppError(
        "RESULT_UNAVAILABLE",
        "This request was completed but its retained result is no longer available.",
        retryable=False,
    )


def _replace_expired_request(
    identity_key: str,
    request_id: str,
    payload_hash: str,
    existing: dict,
    now_epoch: int,
) -> dict:
    lease_token = str(uuid.uuid4())
    previous_expires_at = int(existing.get("expiresAt", 0))
    try:
        table.update_item(
            Key={"PK": f"ANALYSIS#REQUEST#{identity_key}", "SK": request_id},
            UpdateExpression=(
                "SET #status = :processing, payloadHash = :payload_hash, "
                "leaseToken = :lease_token, leaseExpiresAt = :lease_expires_at, "
                "createdAt = :created_at, updatedAt = :updated_at, "
                "expiresAt = :expires_at, #ttl = :expires_at "
                "REMOVE #response, resultReadyAt, completedAt"
            ),
            ConditionExpression="expiresAt = :previous_expires_at",
            ExpressionAttributeNames={
                "#status": "status",
                "#response": "response",
                "#ttl": "ttl",
            },
            ExpressionAttributeValues={
                ":processing": "PROCESSING",
                ":payload_hash": payload_hash,
                ":lease_token": lease_token,
                ":lease_expires_at": now_epoch + PROCESSING_LEASE_SECONDS,
                ":created_at": _iso_now(),
                ":updated_at": _iso_now(),
                ":expires_at": now_epoch + REQUEST_ID_TTL_SECONDS,
                ":previous_expires_at": previous_expires_at,
            },
        )
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise AppError(
                "REQUEST_IN_PROGRESS",
                "This analysis request changed while an expired record was being replaced.",
                retryable=True,
                details=[{"retryAfterSeconds": 1}],
            ) from err
        raise
    return {
        "state": "processing",
        "payloadHash": payload_hash,
        "leaseToken": lease_token,
        "replacedExpired": True,
    }


def _assert_matching_payload(existing: dict, payload_hash: str) -> None:
    if existing.get("payloadHash") != payload_hash:
        raise AppError(
            "IDEMPOTENCY_CONFLICT",
            "The request ID is already bound to different analysis content.",
            retryable=False,
        )


def _payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_identity(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._:@-]", "_", value.strip())
    return cleaned[:200] or "anonymous"


def _hashed_identity(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _require_table():
    if not table:
        raise AppError("SERVER_UNAVAILABLE", "The analysis abuse-control table is not configured.", retryable=False)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _to_dynamodb_compatible(value):
    if isinstance(value, dict):
        return {key: _to_dynamodb_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_dynamodb_compatible(item) for item in value]
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def _to_json_compatible(value):
    if isinstance(value, dict):
        return {key: _to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_json_compatible(item) for item in value]
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    return value
