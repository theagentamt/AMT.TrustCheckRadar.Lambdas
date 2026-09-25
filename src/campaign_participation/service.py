from datetime import UTC, datetime
import time
import uuid

import boto3
from botocore.exceptions import ClientError

import config
from errors import AppError
from decimal import Decimal

PARTICIPATION_SK = "CAMPAIGN_PARTICIPATION"
RECEIPT_PREFIX = "CAMPAIGN_CONSENT#"
OPERATION_PREFIX = "CAMPAIGN_OPERATION#"

dynamodb = boto3.client("dynamodb")
resource = boto3.resource("dynamodb")
users_table = resource.Table(config.USERS_TABLE_NAME) if config.USERS_TABLE_NAME else None
deletion_ledger_table = (
    resource.Table(config.DELETION_LEDGER_TABLE_NAME)
    if config.DELETION_LEDGER_TABLE_NAME else None
)


def get_participation(account_id: str, operation_id: str | None = None) -> dict:
    _require_tables()
    _assert_account_active(account_id)
    current = _load_current(account_id)
    if operation_id is not None and not _is_uuid4(operation_id):
        raise AppError("INVALID_REQUEST", "operationId must be a canonical UUIDv4.")
    operation = _load_operation(account_id, operation_id) if operation_id else None
    return _response(current, operation, operation_id)


def update_participation(
    account_id: str,
    payload: dict,
    *,
    now_epoch: int | None = None,
    now_iso: str | None = None,
) -> dict:
    _require_tables()
    _assert_account_active(account_id)
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    now_iso = now_iso or datetime.fromtimestamp(now_epoch, UTC).isoformat().replace("+00:00", "Z")
    operation_id = payload["operationId"]
    action = payload["action"]

    replay = _load_operation(account_id, operation_id, now_epoch=now_epoch)
    if replay:
        _assert_replay_action(replay, action, payload)
        return get_participation(account_id, operation_id)

    if payload["schemaVersion"] == 1 and action == "join":
        raise AppError("POLICY_REVIEW_REQUIRED", "Review the current research notice before joining.")
    current = _load_current(account_id)
    current_state = current.get("state", "not_enrolled")
    if payload["schemaVersion"] == 2 and payload["expectedStateVersion"] != current.get("stateVersion", 0):
        raise AppError("CONFLICT", "Research participation changed after this choice was reviewed.")
    if current.get("stateVersion", 0) >= 9007199254740991:
        raise AppError("SERVER_UNAVAILABLE", "Stored campaign participation revision is exhausted.")
    if action == "join":
        if not config.CONSENT_INDEPENDENCE_ENABLED:
            raise AppError("JOIN_UNAVAILABLE", "New research participation is temporarily unavailable.")
        eligible_legacy_review = current_state == "enrolled" and not _eligible(current)
        if current_state not in {"not_enrolled", "withdrawn"} and not eligible_legacy_review:
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
            "noticeVersion": current["noticeVersion"],
            "policyVersion": current["policyVersion"],
            "effectiveUntil": now_iso,
            "withdrawalRequestedAt": now_iso,
            "deletionDeadlineAt": deletion_deadline,
            "updatedAt": now_iso,
            "lastOperationId": operation_id,
        }
        event_type = "campaign.participation.withdrawal_requested"

    receipt = _receipt(
        account_id=account_id,
        state=new_state,
        event_type=event_type,
        operation_id=operation_id,
        occurred_at=now_iso,
        occurred_at_epoch=now_epoch,
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
                        "recordVersion": 2,
                        "requestSchemaVersion": payload["schemaVersion"],
                        "requestNoticeVersion": payload["noticeVersion"],
                        "expectedStateVersion": payload["expectedStateVersion"] if payload["schemaVersion"] == 2 else None,
                        "noticeVersion": new_state["noticeVersion"],
                        "stateVersion": new_state["stateVersion"],
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

    if action == "withdraw" and config.CAMPAIGN_RECOVERY_WRITES_ENABLED:
        from shared_campaign_recovery.jobs import enqueue_actions
        from shared_campaign_recovery.records import plain, serialize_actions
        command = plain(transaction[-1]["Put"]["Item"])
        transaction.extend(serialize_actions(enqueue_actions(
            dynamodb, config.DELETION_LEDGER_TABLE_NAME, command)))
    transaction.extend(_account_authority_checks(account_id))

    try:
        dynamodb.transact_write_items(TransactItems=transaction)
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") != "TransactionCanceledException":
            raise
        replay = _load_operation(account_id, operation_id)
        if replay:
            _assert_account_active(account_id)
            _assert_replay_action(replay, action, payload)
            return get_participation(account_id, operation_id)
        raise AppError("CONFLICT", "Campaign participation changed during this request.", retryable=True) from err
    return _response(new_state, {"operationId": operation_id, "action": action, "consentEpochId": consent_epoch_id, "noticeVersion": new_state["noticeVersion"], "recordVersion": 2, "requestSchemaVersion": payload["schemaVersion"], "requestNoticeVersion": payload["noticeVersion"], "expectedStateVersion": payload["expectedStateVersion"] if payload["schemaVersion"] == 2 else None, "resultingState": new_state["state"]}, operation_id)


def _load_current(account_id: str) -> dict:
    response = users_table.get_item(
        Key={"PK": f"USER#{account_id}", "SK": PARTICIPATION_SK},
        ConsistentRead=True,
    )
    item = response.get("Item")
    if not item:
        return {"state": "not_enrolled", "stateVersion": 0}
    required = {"PK", "SK", "schemaVersion", "recordVersion", "environment", "state", "stateVersion",
                "noticeVersion", "policyVersion", "consentEpochId", "effectiveFrom", "updatedAt", "lastOperationId"}
    optional = {"effectiveUntil", "withdrawalRequestedAt", "deletionDeadlineAt", "deletionCompletedAt"}
    if (not required.issubset(item) or not set(item).issubset(required | optional)
        or item.get("PK") != f"USER#{account_id}" or item.get("SK") != PARTICIPATION_SK
        or not _integer(item.get("schemaVersion"), 1, 1) or not _integer(item.get("recordVersion"), 1, 1)
        or item.get("environment") != config.ENVIRONMENT
        or item.get("state") not in {"enrolled", "withdrawal_pending", "withdrawn"}
        or not _integer(item.get("stateVersion"), 1, 9007199254740991)
        or not _is_uuid4(item.get("consentEpochId")) or not _is_uuid4(item.get("lastOperationId"))
        or not _version(item.get("noticeVersion")) or not _version(item.get("policyVersion"))
        or not all(_timestamp(item.get(name)) for name in {"effectiveFrom", "updatedAt"} | (optional & set(item)))
        or (item.get("state") == "withdrawal_pending" and not {"effectiveUntil", "withdrawalRequestedAt", "deletionDeadlineAt"}.issubset(item))):
        raise AppError("SERVER_UNAVAILABLE", "Stored campaign participation state is invalid.")
    if item["state"] == "enrolled" and optional & set(item):
        raise AppError("SERVER_UNAVAILABLE", "Stored campaign participation state is invalid.")
    if item["state"] == "withdrawal_pending":
        requested = datetime.fromisoformat(item["withdrawalRequestedAt"].replace("Z", "+00:00"))
        deadline = datetime.fromisoformat(item["deletionDeadlineAt"].replace("Z", "+00:00"))
        if (item["effectiveUntil"] != item["withdrawalRequestedAt"]
                or "deletionCompletedAt" in item or (deadline - requested).total_seconds() != 86400):
            raise AppError("SERVER_UNAVAILABLE", "Stored campaign participation state is invalid.")
    return dict(item)


def _load_operation(account_id: str, operation_id: str, *, now_epoch=None) -> dict | None:
    response = users_table.get_item(
        Key={"PK": f"USER#{account_id}", "SK": f"{OPERATION_PREFIX}{operation_id}"},
        ConsistentRead=True,
    )
    item = response.get("Item")
    if not item:
        return None
    legacy_fields = {"PK", "SK", "schemaVersion", "recordVersion", "operationId", "action",
                     "consentEpochId", "resultingState", "occurredAt", "expiresAt"}
    new_fields = {"requestSchemaVersion", "requestNoticeVersion", "expectedStateVersion", "noticeVersion", "stateVersion"}
    version = item.get("recordVersion")
    if (set(item) != (legacy_fields | new_fields if version == 2 else legacy_fields)
        or item.get("PK") != f"USER#{account_id}" or item.get("SK") != f"{OPERATION_PREFIX}{operation_id}"
        or not _integer(item.get("schemaVersion"), 1, 1) or not _integer(version, 1, 2)
        or item.get("operationId") != operation_id or item.get("action") not in {"join", "withdraw"}
        or not _is_uuid4(item.get("consentEpochId"))
        or item.get("resultingState") != {"join": "enrolled", "withdraw": "withdrawal_pending"}.get(item.get("action"))
        or not _timestamp(item.get("occurredAt")) or not _integer(item.get("expiresAt"), 1)
        or (version == 2 and (not _integer(item.get("requestSchemaVersion"), 1, 2)
            or not _integer(item.get("stateVersion"), 1, 9007199254740991) or not _version(item.get("requestNoticeVersion"))
            or not _version(item.get("noticeVersion"))
            or (item.get("requestSchemaVersion") == 1 and item.get("expectedStateVersion") is not None)
            or (item.get("requestSchemaVersion") == 2 and not _integer(item.get("expectedStateVersion"), 0, 9007199254740991))))):
        raise AppError("SERVER_UNAVAILABLE", "Stored campaign operation is invalid.")
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    if item["expiresAt"] <= now_epoch:
        return None
    return dict(item)


def _assert_replay_action(receipt: dict, action: str, payload: dict) -> None:
    if (receipt.get("action") != action or (receipt.get("recordVersion") == 1 and payload["schemaVersion"] != 1) or (receipt.get("recordVersion") == 2 and
            (receipt.get("requestSchemaVersion") != payload["schemaVersion"]
             or receipt.get("requestNoticeVersion") != payload["noticeVersion"]
             or receipt.get("expectedStateVersion") != (payload["expectedStateVersion"] if payload["schemaVersion"] == 2 else None)))):
        raise AppError("CONFLICT", "operationId was already used for another request.")


def _participation_write(current: dict, new_state: dict) -> dict:
    write = {
        "TableName": config.USERS_TABLE_NAME,
        "Item": _serialize_item(new_state),
    }
    if current.get("state") == "not_enrolled" and int(current.get("stateVersion", 0)) == 0:
        write["ConditionExpression"] = "attribute_not_exists(PK) AND attribute_not_exists(SK)"
    else:
        write["ConditionExpression"] = "#state = :state AND stateVersion = :state_version AND consentEpochId = :epoch AND noticeVersion = :notice AND policyVersion = :policy"
        write["ExpressionAttributeNames"] = {"#state": "state"}
        write["ExpressionAttributeValues"] = _serialize_item(
            {":state": current["state"], ":state_version": int(current["stateVersion"]), ":epoch": current["consentEpochId"], ":notice": current["noticeVersion"], ":policy": current["policyVersion"]}
        )
    return {"Put": write}


def _receipt(*, account_id, state, event_type, operation_id, occurred_at, occurred_at_epoch) -> dict:
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
        "expiresAt": occurred_at_epoch + config.AUDIT_RETENTION_DAYS * 86400,
    }


def _eligible(state):
    return (state.get("state") == "enrolled" and state.get("noticeVersion") == config.CURRENT_NOTICE
            and state.get("policyVersion") == config.CURRENT_POLICY)


def _response(state: dict, operation=None, operation_id=None) -> dict:
    value = state.get("state", "not_enrolled")
    if value == "enrolled" and not _eligible(state):
        value = "review_required"
    operation_result = None
    if operation_id is not None:
        operation_result = {"operationId": operation_id, "status": "not_found", "action": None,
            "acceptedNoticeVersion": None, "requestSchemaVersion": None, "requestNoticeVersion": None, "expectedStateVersion": None, "resultingState": None, "consentEpochId": None}
        if operation:
            operation_result.update(status="applied" if operation["recordVersion"] == 2 else "applied_legacy",
                action=operation["action"], expectedStateVersion=int(operation["expectedStateVersion"]) if operation.get("expectedStateVersion") is not None else None, requestSchemaVersion=int(operation.get("requestSchemaVersion", 1)), requestNoticeVersion=operation.get("requestNoticeVersion"), acceptedNoticeVersion=operation.get("noticeVersion") if operation["recordVersion"] == 2 else None,
                resultingState=operation["resultingState"], consentEpochId=operation["consentEpochId"])
    return {"schemaVersion": 2, "contractVersion": "2.0.0-candidate.2", "state": value,
        "noticeVersion": config.CURRENT_NOTICE, "policyVersion": config.CURRENT_POLICY,
        "acceptedNoticeVersion": state.get("noticeVersion"), "stateVersion": int(state.get("stateVersion", 0)),
        "consentEpochId": state.get("consentEpochId"), "lastOperationId": state.get("lastOperationId"),
        "contributionEligible": _eligible(state),
        "withdrawalStatus": "pending" if value == "withdrawal_pending" else "completion_unverified" if value == "withdrawn" else "not_requested",
        "withdrawalRequestedAt": state.get("withdrawalRequestedAt"),
        "deletionDeadlineAt": state.get("deletionDeadlineAt"), "operation": operation_result}


def _require_tables() -> None:
    if not users_table or not deletion_ledger_table:
        raise AppError("SERVER_UNAVAILABLE", "Campaign participation storage is not configured.")


def _assert_account_active(account_id: str) -> None:
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
        raise AppError("FORBIDDEN", "The account is not active.")


def _account_authority_checks(account_id: str) -> list[dict]:
    return [
        {"ConditionCheck": {
            "TableName": config.USERS_TABLE_NAME,
            "Key": _serialize_item({
                "PK": f"USER#{account_id}", "SK": "PROFILE",
            }),
            "ConditionExpression": (
                "#status = :active AND ageVerified = :true AND #sub = :account"
            ),
            "ExpressionAttributeNames": {"#status": "status", "#sub": "sub"},
            "ExpressionAttributeValues": _serialize_item({
                ":active": "ACTIVE", ":true": True, ":account": account_id,
            }),
        }},
        {"ConditionCheck": {
            "TableName": config.DELETION_LEDGER_TABLE_NAME,
            "Key": _serialize_item({
                "PK": f"ACCOUNT#{account_id}", "SK": "ACCOUNT_DELETION",
            }),
            "ConditionExpression": "attribute_not_exists(PK)",
        }},
    ]


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
    if isinstance(value, (int, Decimal)):
        return {"N": str(value)}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, dict):
        return {"M": _serialize_item(value)}
    if isinstance(value, list):
        return {"L": [_serialize_value(item) for item in value]}
    raise TypeError(type(value).__name__)


def _integer(value, minimum, maximum=None):
    return (not isinstance(value, bool) and isinstance(value, (int, Decimal)) and value == int(value)
            and value >= minimum and (maximum is None or value <= maximum))


def _version(value):
    return isinstance(value, str) and 1 <= len(value.encode("utf-8")) <= 64


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40 or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True
