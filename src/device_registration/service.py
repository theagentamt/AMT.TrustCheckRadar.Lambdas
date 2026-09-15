from datetime import UTC, datetime, timedelta
from decimal import Decimal

import boto3

import config
from config import DEVICE_BINDINGS_INACTIVE_RETENTION_DAYS, DEVICE_BINDINGS_TABLE_NAME
from errors import AppError

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DEVICE_BINDINGS_TABLE_NAME) if DEVICE_BINDINGS_TABLE_NAME else None
dynamodb_client = boto3.client("dynamodb")


def register_device(*, account_id: str, binding_fingerprint: str, platform: str, os_version: str) -> dict:
    _require_table()

    now = datetime.now(UTC)
    now_iso = now.isoformat()

    existing = _get_binding(account_id, binding_fingerprint)
    pointer = table.get_item(
        Key={"PK": f"USER#{account_id}", "SK": "ACTIVE_BINDING"}, ConsistentRead=True
    ).get("Item")
    active = _active_from_pointer_or_legacy(account_id, pointer)
    activated = _build_active_item(
        account_id=account_id,
        binding_fingerprint=binding_fingerprint,
        platform=platform,
        os_version=os_version,
        now_iso=now_iso,
        existing=existing or (active if active and active.get("bindingFingerprint") == binding_fingerprint else None),
    )
    current_fingerprint = active.get("bindingFingerprint") if active else None
    pointer_fingerprint = pointer.get("bindingFingerprint") if pointer else None
    decision = (
        "KNOWN" if current_fingerprint == binding_fingerprint
        else "SWITCH" if active
        else "KNOWN" if existing else "NEW"
    )
    transaction = _authority_conditions(account_id)
    pointer_key = {"PK": f"USER#{account_id}", "SK": "ACTIVE_BINDING"}
    if pointer:
        version = _positive_version(pointer.get("stateVersion"))
        if version is None or pointer_fingerprint not in {current_fingerprint, "NONE"}:
            raise AppError("SERVER_UNAVAILABLE", "The active binding pointer is invalid.", retryable=False)
        transaction.append({"Update": {
            "TableName": DEVICE_BINDINGS_TABLE_NAME,
            "Key": _serialize(pointer_key),
            "UpdateExpression": "SET bindingFingerprint = :target, stateVersion = :next, updatedAt = :now",
            "ConditionExpression": "bindingFingerprint = :current AND stateVersion = :version",
            "ExpressionAttributeValues": _serialize({
                ":target": binding_fingerprint, ":next": version + 1, ":now": now_iso,
                ":current": pointer_fingerprint, ":version": version,
            }),
        }})
    else:
        transaction.append({"Put": {
            "TableName": DEVICE_BINDINGS_TABLE_NAME,
            "Item": _serialize({
                **pointer_key, "recordType": "ACTIVE_BINDING_POINTER", "schemaVersion": 1,
                "bindingFingerprint": binding_fingerprint, "stateVersion": 1, "updatedAt": now_iso,
            }),
            "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
        }})
    if active and current_fingerprint != binding_fingerprint:
        inactive = _build_inactive_item(active, now)
        transaction.append({"Put": {
            "TableName": DEVICE_BINDINGS_TABLE_NAME,
            "Item": _serialize(inactive),
            "ConditionExpression": "#status = :active AND bindingFingerprint = :current",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": _serialize({":active": "ACTIVE", ":current": current_fingerprint}),
        }})
    target_put = {"TableName": DEVICE_BINDINGS_TABLE_NAME, "Item": _serialize(activated)}
    if existing:
        target_put.update({
            "ConditionExpression": "#status = :previous",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": _serialize({":previous": existing.get("status")}),
        })
    else:
        target_put["ConditionExpression"] = "attribute_not_exists(PK) AND attribute_not_exists(SK)"
    transaction.append({"Put": target_put})
    try:
        dynamodb_client.transact_write_items(TransactItems=transaction)
    except Exception as err:
        if getattr(err, "response", {}).get("Error", {}).get("Code") == "TransactionCanceledException":
            raise AppError("CONFLICT", "Device state changed during registration.", retryable=True) from err
        raise
    return _build_response(decision, activated)


def _get_binding(account_id: str, binding_fingerprint: str):
    response = table.get_item(Key={"PK": f"USER#{account_id}", "SK": f"DEVICE#{binding_fingerprint}"})
    return response.get("Item")


def _get_active_binding(account_id: str):
    response = table.query(
        IndexName="GSI1",
        KeyConditionExpression="GSI1PK = :gsi1pk",
        ExpressionAttributeValues={":gsi1pk": f"USER#{account_id}#ACTIVE"},
        Limit=2,
        ScanIndexForward=False,
    )
    items = response.get("Items") or []
    if len(items) > 1:
        raise AppError("SERVER_UNAVAILABLE", "Multiple active bindings require operator repair.", retryable=False)
    return items[0] if items else None


def _active_from_pointer_or_legacy(account_id, pointer):
    if not pointer:
        return _get_active_binding(account_id)
    if pointer.get("recordType") != "ACTIVE_BINDING_POINTER" or _positive_version(pointer.get("stateVersion")) is None:
        raise AppError("SERVER_UNAVAILABLE", "The active binding pointer is invalid.", retryable=False)
    if pointer.get("bindingFingerprint") == "NONE":
        return None
    active = _get_binding(account_id, pointer.get("bindingFingerprint"))
    if not active or active.get("status") != "ACTIVE":
        raise AppError("SERVER_UNAVAILABLE", "The active binding pointer is inconsistent.", retryable=False)
    return active


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


def _positive_version(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value() or value < 1:
            return None
        return int(value)
    return None


def _authority_conditions(account_id):
    return [
        {"ConditionCheck": {
            "TableName": config.USERS_TABLE_NAME,
            "Key": _serialize({"PK": f"USER#{account_id}", "SK": "PROFILE"}),
            "ConditionExpression": "#status = :active AND ageVerified = :true AND #sub = :account",
            "ExpressionAttributeNames": {"#status": "status", "#sub": "sub"},
            "ExpressionAttributeValues": _serialize({
                ":active": "ACTIVE", ":true": True, ":account": account_id,
            }),
        }},
        {"ConditionCheck": {
            "TableName": config.DELETION_LEDGER_TABLE_NAME,
            "Key": _serialize({"PK": f"ACCOUNT#{account_id}", "SK": "ACCOUNT_DELETION"}),
            "ConditionExpression": "attribute_not_exists(PK)",
        }},
    ]


def _serialize(item):
    return {key: _serialize_value(value) for key, value in item.items()}


def _serialize_value(value):
    if value is None:
        return {"NULL": True}
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, int):
        return {"N": str(value)}
    if isinstance(value, str):
        return {"S": value}
    raise TypeError(type(value).__name__)
