from datetime import UTC, datetime, timedelta

import boto3

from config import DEVICE_BINDINGS_INACTIVE_RETENTION_DAYS, DEVICE_BINDINGS_TABLE_NAME
from errors import AppError

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DEVICE_BINDINGS_TABLE_NAME) if DEVICE_BINDINGS_TABLE_NAME else None


def register_device(*, account_id: str, binding_fingerprint: str, platform: str, os_version: str) -> dict:
    _require_table()

    now = datetime.now(UTC)
    now_iso = now.isoformat()

    existing = _get_binding(account_id, binding_fingerprint)
    active = _get_active_binding(account_id)

    if active and active.get("bindingFingerprint") == binding_fingerprint:
        updated = _build_active_item(
            account_id=account_id,
            binding_fingerprint=binding_fingerprint,
            platform=platform,
            os_version=os_version,
            now_iso=now_iso,
            existing=existing or active,
        )
        table.put_item(Item=updated)
        return _build_response("KNOWN", updated)

    if active and active.get("bindingFingerprint") != binding_fingerprint:
        inactive = _build_inactive_item(active, now)
        table.put_item(Item=inactive)

        activated = _build_active_item(
            account_id=account_id,
            binding_fingerprint=binding_fingerprint,
            platform=platform,
            os_version=os_version,
            now_iso=now_iso,
            existing=existing,
        )
        table.put_item(Item=activated)
        return _build_response("SWITCH", activated)

    if existing:
        activated = _build_active_item(
            account_id=account_id,
            binding_fingerprint=binding_fingerprint,
            platform=platform,
            os_version=os_version,
            now_iso=now_iso,
            existing=existing,
        )
        table.put_item(Item=activated)
        return _build_response("KNOWN", activated)

    created = _build_active_item(
        account_id=account_id,
        binding_fingerprint=binding_fingerprint,
        platform=platform,
        os_version=os_version,
        now_iso=now_iso,
        existing=None,
    )
    table.put_item(Item=created)
    return _build_response("NEW", created)


def _get_binding(account_id: str, binding_fingerprint: str):
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


def _build_active_item(*, account_id: str, binding_fingerprint: str, platform: str, os_version: str, now_iso: str, existing: dict | None):
    return {
        "PK": f"USER#{account_id}",
        "SK": f"DEVICE#{binding_fingerprint}",
        "accountId": account_id,
        "bindingFingerprint": binding_fingerprint,
        "platform": platform,
        "osVersion": os_version,
        "status": "ACTIVE",
        "firstSeenAt": (existing or {}).get("firstSeenAt", now_iso),
        "lastSeenAt": now_iso,
        "deactivatedAt": None,
        "GSI1PK": f"USER#{account_id}#ACTIVE",
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


def _build_response(decision: str, item: dict) -> dict:
    return {
        "decision": decision,
        "accountId": item["accountId"],
        "bindingFingerprint": item["bindingFingerprint"],
        "status": item["status"],
        "firstSeenAt": item["firstSeenAt"],
        "lastSeenAt": item["lastSeenAt"],
        "deactivatedAt": item["deactivatedAt"],
    }


def _require_table():
    if not table:
        raise AppError("SERVER_UNAVAILABLE", "The device bindings table is not configured.", retryable=False)
