from datetime import UTC, datetime, timedelta

import boto3

from config import DEVICE_BINDINGS_INACTIVE_RETENTION_DAYS, DEVICE_BINDINGS_TABLE_NAME
from errors import AppError

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DEVICE_BINDINGS_TABLE_NAME) if DEVICE_BINDINGS_TABLE_NAME else None


def process_recovery(*, account_id: str, action: str, binding_fingerprint: str | None, operator_id: str) -> dict:
    _require_table()

    now = datetime.now(UTC)
    active = _get_active_binding(account_id)

    if action == "RESET_ACTIVE_BINDING":
        return _reset_active_binding(account_id=account_id, active=active, operator_id=operator_id, now=now)

    return _recover_binding(
        account_id=account_id,
        binding_fingerprint=binding_fingerprint,
        active=active,
        operator_id=operator_id,
        now=now,
    )


def _reset_active_binding(*, account_id: str, active: dict | None, operator_id: str, now: datetime) -> dict:
    if not active:
        return {
            "result": "NO_ACTIVE_BINDING",
            "accountId": account_id,
            "bindingFingerprint": None,
            "status": None,
            "operatorId": operator_id,
            "recoveredAt": now.isoformat(),
        }

    inactive = _build_inactive_item(active, now)
    table.put_item(Item=inactive)

    return {
        "result": "CLEARED",
        "accountId": account_id,
        "bindingFingerprint": inactive["bindingFingerprint"],
        "status": inactive["status"],
        "operatorId": operator_id,
        "recoveredAt": now.isoformat(),
    }


def _recover_binding(*, account_id: str, binding_fingerprint: str | None, active: dict | None, operator_id: str, now: datetime) -> dict:
    target = _get_binding(account_id, binding_fingerprint)
    if not target:
        raise AppError("NOT_FOUND", "No device binding was found for the provided bindingFingerprint.", retryable=False)

    if active and active.get("bindingFingerprint") == binding_fingerprint:
        refreshed = _build_active_item(target, now)
        table.put_item(Item=refreshed)
        return {
            "result": "ALREADY_ACTIVE",
            "accountId": account_id,
            "bindingFingerprint": refreshed["bindingFingerprint"],
            "status": refreshed["status"],
            "operatorId": operator_id,
            "recoveredAt": now.isoformat(),
        }

    if active:
        inactive = _build_inactive_item(active, now)
        table.put_item(Item=inactive)

    activated = _build_active_item(target, now)
    table.put_item(Item=activated)
    return {
        "result": "RECOVERED",
        "accountId": account_id,
        "bindingFingerprint": activated["bindingFingerprint"],
        "status": activated["status"],
        "operatorId": operator_id,
        "recoveredAt": now.isoformat(),
    }


def _get_binding(account_id: str, binding_fingerprint: str | None):
    if not binding_fingerprint:
        return None
    response = table.get_item(Key={"PK": f"USER#{account_id}", "SK": f"DEVICE#{binding_fingerprint}"})
    return response.get("Item")


def _get_active_binding(account_id: str):
    response = table.query(
        IndexName="GSI1",
        KeyConditionExpression="GSI1PK = :gsi1pk",
        ExpressionAttributeValues={":gsi1pk": f"USER#{account_id}#ACTIVE"},
        Limit=1,
        ScanIndexForward=False,
    )
    items = response.get("Items") or []
    return items[0] if items else None


def _build_active_item(item: dict, now: datetime):
    now_iso = now.isoformat()
    return {
        "PK": item["PK"],
        "SK": item["SK"],
        "accountId": item["accountId"],
        "bindingFingerprint": item["bindingFingerprint"],
        "platform": item["platform"],
        "osVersion": item["osVersion"],
        "status": "ACTIVE",
        "firstSeenAt": item["firstSeenAt"],
        "lastSeenAt": now_iso,
        "deactivatedAt": None,
        "GSI1PK": f"USER#{item['accountId']}#ACTIVE",
        "GSI1SK": now_iso,
    }


def _build_inactive_item(item: dict, now: datetime):
    now_iso = now.isoformat()
    expires_at = int((now + timedelta(days=DEVICE_BINDINGS_INACTIVE_RETENTION_DAYS)).timestamp())
    updated = dict(item)
    updated["status"] = "INACTIVE"
    updated["deactivatedAt"] = now_iso
    updated["GSI1PK"] = f"USER#{item['accountId']}#INACTIVE"
    updated["GSI1SK"] = now_iso
    updated["expiresAt"] = expires_at
    return updated


def _require_table():
    if not table:
        raise AppError("SERVER_UNAVAILABLE", "The device bindings table is not configured.", retryable=False)
