from datetime import UTC, datetime
import time
import uuid

import boto3
from botocore.exceptions import ClientError

import config
from errors import AppError
from shared_entitlements import EntitlementStoreNotConfiguredError, load_entitlement

PARTICIPATION_SK = "CAMPAIGN_PARTICIPATION"
RECEIPT_PREFIX = "CAMPAIGN_CONSENT#"
OPERATION_PREFIX = "CAMPAIGN_OPERATION#"

dynamodb = boto3.client("dynamodb")
resource = boto3.resource("dynamodb")
users_table = resource.Table(config.USERS_TABLE_NAME) if config.USERS_TABLE_NAME else None
entitlements_table = resource.Table(config.ENTITLEMENTS_TABLE_NAME) if config.ENTITLEMENTS_TABLE_NAME else None


def get_participation(account_id: str) -> dict:
    _require_tables()
    current = _load_current(account_id)
    entitlement = _load_entitlement(account_id)
    return _response(current, entitlement)


def update_participation(
    account_id: str,
    payload: dict,
    *,
    now_epoch: int | None = None,
    now_iso: str | None = None,
) -> dict:
    _require_tables()
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    now_iso = now_iso or datetime.fromtimestamp(now_epoch, UTC).isoformat().replace("+00:00", "Z")
    operation_id = payload["operationId"]
    action = payload["action"]

    replay = _load_operation(account_id, operation_id)
    if replay:
        _assert_replay_action(replay, action)
        return get_participation(account_id)

    current = _load_current(account_id)
    current_state = current.get("state", "not_enrolled")
    if action == "join":
        if current_state not in {"not_enrolled", "withdrawn"}:
            raise AppError("CONFLICT", "Campaign participation is already active or changing.")
        consent_epoch_id = str(uuid.uuid4())
        new_state = {
            "PK": f"USER#{account_id}",
            "SK": PARTICIPATION_SK,
            "schemaVersion": 1,
            "recordVersion": 1,
            "environment": config.ENVIRONMENT,
            "state": "enrolled",
            "stateVersion": int(current.get("stateVersion", 0)) + 1,
            "noticeVersion": config.NOTICE_VERSION,
            "policyVersion": config.POLICY_VERSION,
            "consentEpochId": consent_epoch_id,
            "effectiveFrom": now_iso,
            "updatedAt": now_iso,
            "lastOperationId": operation_id,
        }
        event_type = "campaign.participation.joined"
        target_free_limit = config.PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT
    else:
        if current_state != "enrolled":
            raise AppError("CONFLICT", "Campaign participation is not currently enrolled.")
        consent_epoch_id = current.get("consentEpochId")
        if not _is_uuid4(consent_epoch_id):
            raise AppError("SERVER_UNAVAILABLE", "Stored campaign participation state is invalid.")
        deletion_deadline_epoch = now_epoch + config.DELETION_SLA_HOURS * 3600
        deletion_deadline = datetime.fromtimestamp(deletion_deadline_epoch, UTC).isoformat().replace("+00:00", "Z")
        new_state = dict(current) | {
            "state": "withdrawal_pending",
            "stateVersion": int(current.get("stateVersion", 0)) + 1,
            "noticeVersion": config.NOTICE_VERSION,
            "policyVersion": config.POLICY_VERSION,
            "effectiveUntil": now_iso,
            "withdrawalRequestedAt": now_iso,
            "deletionDeadlineAt": deletion_deadline,
            "updatedAt": now_iso,
            "lastOperationId": operation_id,
        }
        event_type = "campaign.participation.withdrawal_requested"
        target_free_limit = config.FREE_MONTHLY_SCAN_LIMIT

    entitlement = _load_entitlement(account_id)
    adjusted_entitlement = _adjust_entitlement(entitlement, target_free_limit, now_iso)
    receipt = _receipt(
        account_id=account_id,
        state=new_state,
        event_type=event_type,
        operation_id=operation_id,
        occurred_at=now_iso,
        occurred_at_epoch=now_epoch,
        entitlement=adjusted_entitlement,
    )
    transaction = [
        _participation_write(current, new_state),
        {
            "Put": {
                "TableName": config.USERS_TABLE_NAME,
                "Item": _serialize_item(receipt),
                "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
            }
        },
        {
            "Put": {
                "TableName": config.USERS_TABLE_NAME,
                "Item": _serialize_item(
                    {
                        "PK": f"USER#{account_id}",
                        "SK": f"{OPERATION_PREFIX}{operation_id}",
                        "schemaVersion": 1,
                        "recordVersion": 1,
                        "action": action,
                        "operationId": operation_id,
                        "consentEpochId": consent_epoch_id,
                        "resultingState": new_state["state"],
                        "occurredAt": now_iso,
                        "expiresAt": now_epoch + config.AUDIT_RETENTION_DAYS * 86400,
                    }
                ),
                "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
            }
        },
        _entitlement_write(account_id, entitlement, adjusted_entitlement),
    ]
    if action == "withdraw":
        transaction.append(
            {
                "Put": {
                    "TableName": config.DELETION_LEDGER_TABLE_NAME,
                    "Item": _serialize_item(
                        {
                            "PK": f"ACCOUNT#{account_id}",
                            "SK": f"CAMPAIGN_WITHDRAWAL#{operation_id}",
                            "schemaVersion": 1,
                            "recordVersion": 1,
                            "environment": config.ENVIRONMENT,
                            "eventType": "campaign.consent.withdrawn",
                            "accountId": account_id,
                            "status": "PENDING",
                            "occurredAtEpoch": now_epoch,
                            "consentEpochId": consent_epoch_id,
                            "deleteByEpoch": now_epoch + config.DELETION_SLA_HOURS * 3600,
                            "operationId": operation_id,
                        }
                    ),
                    "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                }
            }
        )

    try:
        dynamodb.transact_write_items(TransactItems=transaction)
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") != "TransactionCanceledException":
            raise
        replay = _load_operation(account_id, operation_id)
        if replay:
            _assert_replay_action(replay, action)
            return get_participation(account_id)
        raise AppError("CONFLICT", "Campaign participation changed during this request.", retryable=True) from err
    return _response(new_state, adjusted_entitlement)


def _load_current(account_id: str) -> dict:
    response = users_table.get_item(
        Key={"PK": f"USER#{account_id}", "SK": PARTICIPATION_SK},
        ConsistentRead=True,
    )
    item = response.get("Item")
    if not item:
        return {"state": "not_enrolled", "stateVersion": 0}
    try:
        state_version = int(item.get("stateVersion"))
    except (TypeError, ValueError):
        state_version = 0
    if (
        isinstance(item.get("schemaVersion"), bool)
        or item.get("schemaVersion") != 1
        or isinstance(item.get("recordVersion"), bool)
        or item.get("recordVersion") != 1
        or item.get("environment") != config.ENVIRONMENT
        or item.get("state") not in {"enrolled", "withdrawal_pending", "withdrawn"}
        or isinstance(item.get("stateVersion"), bool)
        or state_version < 1
        or state_version != item.get("stateVersion")
        or not _is_uuid4(item.get("consentEpochId"))
        or not isinstance(item.get("noticeVersion"), str)
        or not item["noticeVersion"]
        or not isinstance(item.get("policyVersion"), str)
        or not item["policyVersion"]
    ):
        raise AppError("SERVER_UNAVAILABLE", "Stored campaign participation state is invalid.")
    return dict(item)


def _load_entitlement(account_id: str) -> dict:
    try:
        return load_entitlement(account_id)
    except EntitlementStoreNotConfiguredError as err:
        raise AppError("SERVER_UNAVAILABLE", str(err)) from err


def _load_operation(account_id: str, operation_id: str) -> dict | None:
    response = users_table.get_item(
        Key={"PK": f"USER#{account_id}", "SK": f"{OPERATION_PREFIX}{operation_id}"},
        ConsistentRead=True,
    )
    item = response.get("Item")
    if not item:
        return None
    if (
        isinstance(item.get("schemaVersion"), bool)
        or item.get("schemaVersion") != 1
        or isinstance(item.get("recordVersion"), bool)
        or item.get("recordVersion") != 1
        or item.get("operationId") != operation_id
        or item.get("action") not in {"join", "withdraw"}
        or not _is_uuid4(item.get("consentEpochId"))
    ):
        raise AppError("SERVER_UNAVAILABLE", "Stored campaign operation is invalid.")
    return dict(item)


def _assert_replay_action(receipt: dict, action: str) -> None:
    if receipt.get("action") != action:
        raise AppError("CONFLICT", "operationId was already used for another action.")


def _participation_write(current: dict, new_state: dict) -> dict:
    write = {
        "TableName": config.USERS_TABLE_NAME,
        "Item": _serialize_item(new_state),
    }
    if current.get("state") == "not_enrolled" and int(current.get("stateVersion", 0)) == 0:
        write["ConditionExpression"] = "attribute_not_exists(PK) AND attribute_not_exists(SK)"
    else:
        write["ConditionExpression"] = "#state = :state AND stateVersion = :state_version"
        write["ExpressionAttributeNames"] = {"#state": "state"}
        write["ExpressionAttributeValues"] = _serialize_item(
            {":state": current["state"], ":state_version": int(current["stateVersion"])}
        )
    return {"Put": write}


def _entitlement_write(account_id: str, current: dict, adjusted: dict) -> dict:
    key = {"PK": f"USER#{account_id}", "SK": adjusted["SK"]}
    stored = entitlements_table.get_item(Key=key, ConsistentRead=True).get("Item")
    write = {"TableName": config.ENTITLEMENTS_TABLE_NAME, "Item": _serialize_item(adjusted)}
    if stored:
        write["ConditionExpression"] = (
            "updatedAt = :updated_at AND monthlyScanLimit = :monthly_limit "
            "AND remainingMonthlyScans = :remaining"
        )
        write["ExpressionAttributeValues"] = _serialize_item(
            {
                ":updated_at": stored.get("updatedAt"),
                ":monthly_limit": int(stored.get("monthlyScanLimit", current["monthlyScanLimit"])),
                ":remaining": int(stored.get("remainingMonthlyScans", current["remainingMonthlyScans"])),
            }
        )
    else:
        write["ConditionExpression"] = "attribute_not_exists(PK) AND attribute_not_exists(SK)"
    return {"Put": write}


def _adjust_entitlement(entitlement: dict, target_free_limit: int, now_iso: str) -> dict:
    adjusted = dict(entitlement)
    if adjusted.get("entitlementTier") == "PRO":
        return adjusted
    old_limit = max(0, int(adjusted.get("monthlyScanLimit", config.FREE_MONTHLY_SCAN_LIMIT)))
    old_remaining = max(0, int(adjusted.get("remainingMonthlyScans", old_limit)))
    used = max(0, old_limit - old_remaining)
    adjusted["monthlyScanLimit"] = target_free_limit
    adjusted["remainingMonthlyScans"] = max(0, target_free_limit - used)
    adjusted["updatedAt"] = now_iso
    return adjusted


def _receipt(*, account_id, state, event_type, operation_id, occurred_at, occurred_at_epoch, entitlement) -> dict:
    return {
        "PK": f"USER#{account_id}",
        "SK": f"{RECEIPT_PREFIX}{state['consentEpochId']}#{occurred_at_epoch}#{operation_id}",
        "schemaVersion": 1,
        "recordVersion": 1,
        "eventType": event_type,
        "occurredAt": occurred_at,
        "noticeVersion": state["noticeVersion"],
        "policyVersion": state["policyVersion"],
        "consentEpochId": state["consentEpochId"],
        "operationId": operation_id,
        "resultingState": state["state"],
        "stateVersion": state["stateVersion"],
        "effectiveMonthlyScanLimit": int(entitlement["monthlyScanLimit"]),
        "expiresAt": occurred_at_epoch + config.AUDIT_RETENTION_DAYS * 86400,
    }


def _response(state: dict, entitlement: dict) -> dict:
    value = state.get("state", "not_enrolled")
    result = {
        "schemaVersion": 1,
        "state": value,
        "noticeVersion": state.get("noticeVersion", config.NOTICE_VERSION),
        "policyVersion": state.get("policyVersion", config.POLICY_VERSION),
        "baseFreeMonthlyScanLimit": config.FREE_MONTHLY_SCAN_LIMIT,
        "participatingFreeMonthlyScanLimit": config.PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT,
        "bonusMonthlyScans": config.PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT - config.FREE_MONTHLY_SCAN_LIMIT,
        "effectiveMonthlyScanLimit": int(entitlement["monthlyScanLimit"]),
    }
    for field in ("effectiveFrom", "effectiveUntil"):
        if state.get(field):
            result[field] = state[field]
    if value == "withdrawal_pending":
        result["withdrawalRequestedAt"] = state["withdrawalRequestedAt"]
        result["deletionDeadlineAt"] = state["deletionDeadlineAt"]
    return result


def _require_tables() -> None:
    if not users_table or not entitlements_table:
        raise AppError("SERVER_UNAVAILABLE", "Campaign participation storage is not configured.")


def _is_uuid4(value) -> bool:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return parsed.version == 4 and str(parsed) == value


def _serialize_item(value: dict) -> dict:
    return {key: _serialize_value(item) for key, item in value.items()}


def _serialize_value(value):
    if value is None:
        return {"NULL": True}
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, int):
        return {"N": str(value)}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, dict):
        return {"M": _serialize_item(value)}
    if isinstance(value, list):
        return {"L": [_serialize_value(item) for item in value]}
    raise TypeError(type(value).__name__)
