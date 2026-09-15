from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import json
import time

import boto3

import config
from config import DEVICE_BINDINGS_INACTIVE_RETENTION_DAYS, DEVICE_BINDINGS_TABLE_NAME
from errors import AppError

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DEVICE_BINDINGS_TABLE_NAME) if DEVICE_BINDINGS_TABLE_NAME else None
control_table = (
    dynamodb.Table(config.DEVICE_RECOVERY_CONTROL_TABLE_NAME)
    if config.DEVICE_RECOVERY_CONTROL_TABLE_NAME else None
)
users_table = (
    dynamodb.Table(config.USERS_TABLE_NAME) if config.USERS_TABLE_NAME else None
)
deletion_ledger_table = (
    dynamodb.Table(config.DELETION_LEDGER_TABLE_NAME)
    if config.DELETION_LEDGER_TABLE_NAME else None
)
dynamodb_client = boto3.client("dynamodb")


def process_recovery(*, account_id: str, action: str, binding_fingerprint: str | None, operator_id: str) -> dict:
    _require_table()

    now = datetime.now(UTC)
    pointer, active = _pointer_and_active(account_id)

    if action == "RESET_ACTIVE_BINDING":
        return _reset_active_binding(
            account_id=account_id, pointer=pointer, active=active,
            operator_id=operator_id, now=now,
        )

    return _recover_binding(
        account_id=account_id,
        binding_fingerprint=binding_fingerprint,
        pointer=pointer,
        active=active,
        operator_id=operator_id,
        now=now,
    )


def process_self_recovery(*, account_id: str, payload: dict, now_epoch=None) -> dict:
    _require_table()
    if control_table is None:
        raise AppError("SERVER_UNAVAILABLE", "The recovery control table is not configured.")
    _assert_recovery_authority_active(account_id)
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    operation_id = payload["operationId"]
    payload_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    previous = control_table.get_item(
        Key={"PK": f"USER#{account_id}", "SK": f"RECOVERY#{operation_id}"},
        ConsistentRead=True,
    ).get("Item")
    if previous:
        return _replay_receipt(previous, payload_hash)

    now = datetime.fromtimestamp(now_epoch, UTC)
    now_iso = now.isoformat()
    pointer = table.get_item(
        Key={"PK": f"USER#{account_id}", "SK": "ACTIVE_BINDING"}, ConsistentRead=True
    ).get("Item")
    active = None
    if pointer:
        state_version = _positive_version(pointer.get("stateVersion"))
        if (
            pointer.get("recordType") != "ACTIVE_BINDING_POINTER"
            or not isinstance(pointer.get("bindingFingerprint"), str)
            or state_version is None
        ):
            raise AppError("SERVER_UNAVAILABLE", "The active binding pointer is invalid.")
        pointer = dict(pointer, stateVersion=state_version)
        if pointer["bindingFingerprint"] != "NONE":
            active = _get_binding(account_id, pointer["bindingFingerprint"])
            if not active or active.get("status") != "ACTIVE":
                raise AppError("SERVER_UNAVAILABLE", "The active binding pointer is inconsistent.")
    else:
        active = _get_single_legacy_active(account_id)

    target = _get_binding(account_id, payload["bindingFingerprint"])
    active_item = _build_replacement_active_item(
        account_id=account_id, payload=payload, existing=target, now_iso=now_iso
    )
    operations = _recovery_authority_checks(account_id)
    operations.append(_rate_limit_update(account_id, now_epoch))
    operations.extend(_binding_replacement_operations(
        account_id=account_id, pointer=pointer, active=active,
        target=target, active_item=active_item, now=now,
    ))
    expires_at = now_epoch + config.DEVICE_RECOVERY_RECEIPT_RETENTION_DAYS * 86400
    receipt = {
        "PK": f"USER#{account_id}", "SK": f"RECOVERY#{operation_id}",
        "recordType": "DEVICE_RECOVERY_RECEIPT", "schemaVersion": 1,
        "operationId": operation_id, "operation": "REPLACE_ACTIVE_BINDING",
        "payloadHash": payload_hash, "result": "RECOVERED", "status": "COMPLETE",
        "bindingFingerprint": payload["bindingFingerprint"],
        "completedAtEpoch": now_epoch, "expiresAt": expires_at,
    }
    audit_expires = now_epoch + config.DEVICE_RECOVERY_AUDIT_RETENTION_DAYS * 86400
    audit = {
        "PK": f"USER#{account_id}",
        "SK": f"AUDIT#{now_epoch:010d}#{operation_id}",
        "recordType": "DEVICE_RECOVERY_AUDIT", "schemaVersion": 1,
        "operationId": operation_id, "actorType": "SELF",
        "action": "REPLACE_ACTIVE_BINDING", "result": "RECOVERED",
        "bindingFingerprint": payload["bindingFingerprint"],
        "occurredAtEpoch": now_epoch, "expiresAt": audit_expires,
    }
    operations.extend([
        {"Put": _put(config.DEVICE_RECOVERY_CONTROL_TABLE_NAME, receipt)},
        {"Put": _put(config.DEVICE_RECOVERY_CONTROL_TABLE_NAME, audit)},
    ])
    try:
        dynamodb_client.transact_write_items(TransactItems=operations)
    except Exception as err:
        if _error_code(err) != "TransactionCanceledException":
            raise
        previous = control_table.get_item(
            Key={"PK": receipt["PK"], "SK": receipt["SK"]}, ConsistentRead=True
        ).get("Item")
        if previous:
            return _replay_receipt(previous, payload_hash)
        rate = control_table.get_item(
            Key=_rate_key(account_id, now_epoch), ConsistentRead=True
        ).get("Item")
        if rate and int(rate.get("requestCount", 0)) >= config.DEVICE_RECOVERY_RATE_MAX_REQUESTS:
            raise AppError("RATE_LIMITED", "Too many recovery attempts.", retryable=True) from err
        raise AppError("CONFLICT", "Device state changed during recovery.", retryable=True) from err
    return _public_self_receipt(receipt)


def _reset_active_binding(*, account_id: str, pointer: dict | None, active: dict | None, operator_id: str, now: datetime) -> dict:
    operations = _recovery_authority_checks(account_id)
    if not active:
        if pointer is None:
            operations.append({"Put": _put(config.DEVICE_BINDINGS_TABLE_NAME, _empty_pointer(account_id, now))})
            _transact_binding_change(operations)
        return {
            "result": "NO_ACTIVE_BINDING",
            "accountId": account_id,
            "bindingFingerprint": None,
            "status": None,
            "operatorId": operator_id,
            "recoveredAt": now.isoformat(),
        }

    inactive = _build_inactive_item(active, now)
    operations.extend(_clear_binding_operations(account_id, pointer, active, inactive, now))
    _transact_binding_change(operations)

    return {
        "result": "CLEARED",
        "accountId": account_id,
        "bindingFingerprint": inactive["bindingFingerprint"],
        "status": inactive["status"],
        "operatorId": operator_id,
        "recoveredAt": now.isoformat(),
    }


def _recover_binding(*, account_id: str, binding_fingerprint: str | None, pointer: dict | None, active: dict | None, operator_id: str, now: datetime) -> dict:
    target = _get_binding(account_id, binding_fingerprint)
    if not target:
        raise AppError("NOT_FOUND", "No device binding was found for the provided bindingFingerprint.", retryable=False)

    if active and active.get("bindingFingerprint") == binding_fingerprint:
        refreshed = _build_active_item(target, now)
        operations = _recovery_authority_checks(account_id)
        operations.extend(_binding_replacement_operations(
            account_id=account_id, pointer=pointer, active=active,
            target=target, active_item=refreshed, now=now,
        ))
        _transact_binding_change(operations)
        return {
            "result": "ALREADY_ACTIVE",
            "accountId": account_id,
            "bindingFingerprint": refreshed["bindingFingerprint"],
            "status": refreshed["status"],
            "operatorId": operator_id,
            "recoveredAt": now.isoformat(),
        }

    activated = _build_active_item(target, now)
    operations = _recovery_authority_checks(account_id)
    operations.extend(_binding_replacement_operations(
        account_id=account_id, pointer=pointer, active=active,
        target=target, active_item=activated, now=now,
    ))
    _transact_binding_change(operations)
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


def _get_single_legacy_active(account_id: str):
    response = table.query(
        IndexName="GSI1",
        KeyConditionExpression="GSI1PK = :gsi1pk",
        ExpressionAttributeValues={":gsi1pk": f"USER#{account_id}#ACTIVE"},
        Limit=2,
        ScanIndexForward=False,
    )
    items = response.get("Items") or []
    if len(items) > 1:
        raise AppError("SERVER_UNAVAILABLE", "Multiple active bindings require operator repair.")
    return items[0] if items else None


def _pointer_and_active(account_id):
    pointer = table.get_item(
        Key={"PK": f"USER#{account_id}", "SK": "ACTIVE_BINDING"}, ConsistentRead=True
    ).get("Item")
    if not pointer:
        return None, _get_single_legacy_active(account_id)
    state_version = _positive_version(pointer.get("stateVersion"))
    if (
        pointer.get("recordType") != "ACTIVE_BINDING_POINTER"
        or not isinstance(pointer.get("bindingFingerprint"), str)
        or state_version is None
    ):
        raise AppError("SERVER_UNAVAILABLE", "The active binding pointer is invalid.")
    pointer = dict(pointer, stateVersion=state_version)
    if pointer["bindingFingerprint"] == "NONE":
        return pointer, None
    active = _get_binding(account_id, pointer["bindingFingerprint"])
    if not active or active.get("status") != "ACTIVE":
        raise AppError("SERVER_UNAVAILABLE", "The active binding pointer is inconsistent.")
    return pointer, active


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


def _build_replacement_active_item(*, account_id, payload, existing, now_iso):
    return {
        "PK": f"USER#{account_id}",
        "SK": f"DEVICE#{payload['bindingFingerprint']}",
        "accountId": account_id,
        "bindingFingerprint": payload["bindingFingerprint"],
        "platform": payload["platform"],
        "osVersion": payload["osVersion"],
        "status": "ACTIVE",
        "firstSeenAt": (existing or {}).get("firstSeenAt", now_iso),
        "lastSeenAt": now_iso,
        "deactivatedAt": None,
        "GSI1PK": f"USER#{account_id}#ACTIVE",
        "GSI1SK": now_iso,
    }


def _binding_replacement_operations(*, account_id, pointer, active, target, active_item, now):
    pointer_key = {"PK": f"USER#{account_id}", "SK": "ACTIVE_BINDING"}
    operations = []
    target_fingerprint = active_item["bindingFingerprint"]
    current_fingerprint = active.get("bindingFingerprint") if active else None
    pointer_fingerprint = pointer.get("bindingFingerprint") if pointer else None
    if pointer:
        if current_fingerprint == target_fingerprint:
            operations.append({"ConditionCheck": {
                "TableName": config.DEVICE_BINDINGS_TABLE_NAME,
                "Key": _serialize(pointer_key),
                "ConditionExpression": "bindingFingerprint = :current AND stateVersion = :version",
                "ExpressionAttributeValues": _serialize({
                    ":current": pointer_fingerprint, ":version": pointer["stateVersion"],
                }),
            }})
        else:
            operations.append({"Update": {
                "TableName": config.DEVICE_BINDINGS_TABLE_NAME,
                "Key": _serialize(pointer_key),
                "UpdateExpression": "SET bindingFingerprint = :target, stateVersion = :next, updatedAt = :now",
                "ConditionExpression": "bindingFingerprint = :current AND stateVersion = :version",
                "ExpressionAttributeValues": _serialize({
                    ":target": target_fingerprint, ":next": pointer["stateVersion"] + 1,
                    ":now": active_item["lastSeenAt"], ":current": pointer_fingerprint,
                    ":version": pointer["stateVersion"],
                }),
            }})
    else:
        operations.append({"Put": _put(config.DEVICE_BINDINGS_TABLE_NAME, {
            **pointer_key, "recordType": "ACTIVE_BINDING_POINTER", "schemaVersion": 1,
            "bindingFingerprint": target_fingerprint, "stateVersion": 1,
            "updatedAt": active_item["lastSeenAt"],
        })})
    if active and current_fingerprint != target_fingerprint:
        inactive = _build_inactive_item(active, now)
        operations.append({"Put": {
            "TableName": config.DEVICE_BINDINGS_TABLE_NAME,
            "Item": _serialize(inactive),
            "ConditionExpression": "#status = :active AND bindingFingerprint = :current",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": _serialize({
                ":active": "ACTIVE", ":current": current_fingerprint,
            }),
        }})
    target_write = {
        "TableName": config.DEVICE_BINDINGS_TABLE_NAME,
        "Item": _serialize(active_item),
    }
    if target:
        target_write.update({
            "ConditionExpression": "#status = :previous",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": _serialize({":previous": target.get("status")}),
        })
    else:
        target_write["ConditionExpression"] = "attribute_not_exists(PK) AND attribute_not_exists(SK)"
    operations.append({"Put": target_write})
    return operations


def _clear_binding_operations(account_id, pointer, active, inactive, now):
    pointer_key = {"PK": f"USER#{account_id}", "SK": "ACTIVE_BINDING"}
    if pointer:
        pointer_operation = {"Update": {
            "TableName": config.DEVICE_BINDINGS_TABLE_NAME,
            "Key": _serialize(pointer_key),
            "UpdateExpression": "SET bindingFingerprint = :none, stateVersion = :next, updatedAt = :now",
            "ConditionExpression": "bindingFingerprint = :current AND stateVersion = :version",
            "ExpressionAttributeValues": _serialize({
                ":none": "NONE", ":next": pointer["stateVersion"] + 1,
                ":now": now.isoformat(), ":current": active["bindingFingerprint"],
                ":version": pointer["stateVersion"],
            }),
        }}
    else:
        pointer_operation = {"Put": _put(
            config.DEVICE_BINDINGS_TABLE_NAME, _empty_pointer(account_id, now)
        )}
    inactive_operation = {"Put": {
        "TableName": config.DEVICE_BINDINGS_TABLE_NAME,
        "Item": _serialize(inactive),
        "ConditionExpression": "#status = :active AND bindingFingerprint = :current",
        "ExpressionAttributeNames": {"#status": "status"},
        "ExpressionAttributeValues": _serialize({
            ":active": "ACTIVE", ":current": active["bindingFingerprint"],
        }),
    }}
    return [pointer_operation, inactive_operation]


def _empty_pointer(account_id, now):
    return {
        "PK": f"USER#{account_id}", "SK": "ACTIVE_BINDING",
        "recordType": "ACTIVE_BINDING_POINTER", "schemaVersion": 1,
        "bindingFingerprint": "NONE", "stateVersion": 1,
        "updatedAt": now.isoformat(),
    }


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


def _transact_binding_change(operations):
    try:
        dynamodb_client.transact_write_items(TransactItems=operations)
    except Exception as err:
        if _error_code(err) == "TransactionCanceledException":
            raise AppError("CONFLICT", "Device state changed during recovery.", retryable=True) from err
        raise


def _recovery_authority_checks(account_id):
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


def _assert_recovery_authority_active(account_id):
    if users_table is None or deletion_ledger_table is None:
        raise AppError("SERVER_UNAVAILABLE", "The account authority is unavailable.")
    profile = users_table.get_item(
        Key={"PK": f"USER#{account_id}", "SK": "PROFILE"},
        ConsistentRead=True,
    ).get("Item")
    deletion = deletion_ledger_table.get_item(
        Key={"PK": f"ACCOUNT#{account_id}", "SK": "ACCOUNT_DELETION"},
        ConsistentRead=True,
    ).get("Item")
    if (
        not profile
        or profile.get("sub") != account_id
        or profile.get("status") != "ACTIVE"
        or profile.get("ageVerified") is not True
        or deletion is not None
    ):
        raise AppError("FORBIDDEN", "The account is not eligible for device recovery.")


def _rate_key(account_id, now_epoch):
    window = now_epoch - now_epoch % config.DEVICE_RECOVERY_RATE_WINDOW_SECONDS
    return {"PK": f"USER#{account_id}", "SK": f"RATE#{window}"}


def _rate_limit_update(account_id, now_epoch):
    key = _rate_key(account_id, now_epoch)
    expires_at = now_epoch + config.DEVICE_RECOVERY_RATE_STATE_TTL_SECONDS
    return {"Update": {
        "TableName": config.DEVICE_RECOVERY_CONTROL_TABLE_NAME,
        "Key": _serialize(key),
        "UpdateExpression": "SET expiresAt = :expires_at ADD requestCount :one",
        "ConditionExpression": "attribute_not_exists(requestCount) OR requestCount < :maximum",
        "ExpressionAttributeValues": _serialize({
            ":expires_at": expires_at, ":one": 1,
            ":maximum": config.DEVICE_RECOVERY_RATE_MAX_REQUESTS,
        }),
    }}


def _replay_receipt(item, payload_hash):
    if item.get("payloadHash") != payload_hash:
        raise AppError("IDEMPOTENCY_CONFLICT", "operationId is bound to another recovery request.")
    if item.get("status") != "COMPLETE":
        raise AppError("REQUEST_IN_PROGRESS", "Recovery is still in progress.", retryable=True)
    return _public_self_receipt(item)


def _public_self_receipt(item):
    return {
        "schemaVersion": 1, "operationId": item["operationId"],
        "operation": item["operation"], "result": item["result"],
        "status": item["status"], "bindingFingerprint": item["bindingFingerprint"],
        "completedAtEpoch": int(item["completedAtEpoch"]),
    }


def _put(table_name, item):
    return {
        "TableName": table_name, "Item": _serialize(item),
        "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
    }


def _serialize(item):
    return {key: _serialize_value(value) for key, value in item.items()}


def _serialize_value(value):
    if value is None:
        return {"NULL": True}
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, (int, float)):
        return {"N": str(value)}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, dict):
        return {"M": _serialize(value)}
    raise TypeError(type(value).__name__)


def _error_code(err):
    return getattr(err, "response", {}).get("Error", {}).get("Code")


def _require_table():
    if not table:
        raise AppError("SERVER_UNAVAILABLE", "The device bindings table is not configured.", retryable=False)
