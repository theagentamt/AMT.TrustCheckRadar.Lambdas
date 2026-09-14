from decimal import Decimal
from datetime import UTC, datetime
import hashlib
import time

import boto3
from botocore.exceptions import ClientError

from errors import AppError
from shared_history import HistoryError, HistorySettings, build_history_items

dynamodb = boto3.resource("dynamodb")
dynamodb_client = boto3.client("dynamodb")


def history_writes_enabled() -> bool:
    return HistorySettings.from_env().writes_enabled


def reserve_history_acceptance(account_id, request_id, payload_hash, lease_token, *, now_epoch=None):
    settings = HistorySettings.from_env()
    if not settings.writes_enabled:
        return None
    _validate(settings, "writes")
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    control = dynamodb.Table(settings.control_table_name)
    state = control.get_item(
        Key={"PK": f"USER#{account_id}", "SK": "STATE"}, ConsistentRead=True
    ).get("Item")
    _validate_state(state)
    previous_sequence = int(state["acceptedSequence"])
    authorization = {
        "historyGeneration": int(state["historyGeneration"]),
        "recognitionGeneration": int(state["recognitionGeneration"]),
        "acceptedSequence": previous_sequence + 1,
        "acceptedAtEpochMs": now_epoch * 1000,
    }
    completion_job = _completion_job(settings, account_id, request_id, authorization, now_epoch)
    try:
        dynamodb_client.transact_write_items(TransactItems=[
            *_account_authority_checks(settings, account_id),
            {"Update": {
                "TableName": settings.control_table_name,
                "Key": _serialize({"PK": f"USER#{account_id}", "SK": "STATE"}),
                "UpdateExpression": "SET acceptedSequence = :next, updatedAtEpoch = :now",
                "ConditionExpression": "accountStatus = :active AND acceptedSequence = :previous AND historyGeneration = :history_generation AND recognitionGeneration = :recognition_generation",
                "ExpressionAttributeValues": _serialize({
                    ":active": "ACTIVE", ":previous": previous_sequence,
                    ":next": previous_sequence + 1, ":now": now_epoch,
                    ":history_generation": authorization["historyGeneration"],
                    ":recognition_generation": authorization["recognitionGeneration"],
                }),
            }},
            {"Update": {
                "TableName": settings.analysis_abuse_table_name,
                "Key": _serialize({"PK": f"ANALYSIS#REQUEST#{_account_hash(account_id)}", "SK": request_id}),
                "UpdateExpression": "SET historyAuthorization = :authorization",
                "ConditionExpression": "#status = :processing AND payloadHash = :payload_hash AND leaseToken = :lease_token AND attribute_not_exists(historyAuthorization)",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": _serialize({
                    ":processing": "PROCESSING", ":payload_hash": payload_hash,
                    ":lease_token": lease_token, ":authorization": authorization,
                }),
            }},
            {"Put": {
                "TableName": settings.control_table_name,
                "Item": _serialize(completion_job),
                "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
            }},
        ])
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "TransactionCanceledException":
            raise AppError(
                "REQUEST_IN_PROGRESS",
                "History state changed while the request was accepted.",
                retryable=True,
                details=[{"retryAfterSeconds": 1}],
            ) from err
        raise
    return authorization


def append_history_completion(
    transaction, *, account_id, payload_hash, payload, response, authorization, now_epoch
):
    """Append synchronous durable completion writes; return whether replay must be redacted."""
    settings = HistorySettings.from_env()
    if not settings.writes_enabled and not isinstance(authorization, dict):
        return False
    if settings.writes_enabled:
        _validate(settings, "writes")
    else:
        _validate(settings, "write_contract")
    if not isinstance(authorization, dict):
        raise AppError("SERVER_UNAVAILABLE", "The persisted History authorization is missing.", retryable=False)
    control = dynamodb.Table(settings.control_table_name)
    state = control.get_item(
        Key={"PK": f"USER#{account_id}", "SK": "STATE"}, ConsistentRead=True
    ).get("Item")
    _validate_state(state)
    captured_generation = _auth_int(authorization, "historyGeneration", 0)
    captured_sequence = _auth_int(authorization, "acceptedSequence", 1)
    if captured_sequence > int(state["acceptedSequence"]):
        raise AppError("SERVER_UNAVAILABLE", "The persisted History acceptance sequence is invalid.", retryable=False)
    current_generation = int(state["historyGeneration"])
    active_generation = state.get("accountStatus") == "ACTIVE" and captured_generation == current_generation
    transaction.extend(_account_authority_checks(settings, account_id))
    transaction.append({"ConditionCheck": {
        "TableName": settings.control_table_name,
        "Key": _serialize({"PK": f"USER#{account_id}", "SK": "STATE"}),
        "ConditionExpression": "accountStatus = :account_status AND historyGeneration = :history_generation AND recognitionGeneration = :recognition_generation AND acceptedSequence = :accepted_sequence",
        "ExpressionAttributeValues": _serialize({
            ":account_status": state["accountStatus"],
            ":history_generation": current_generation,
            ":recognition_generation": int(state["recognitionGeneration"]),
            ":accepted_sequence": int(state["acceptedSequence"]),
        }),
    }})
    if active_generation:
        try:
            content, locator = build_history_items(
                account_id=account_id,
                payload_hash=payload_hash,
                accepted=authorization,
                payload=payload,
                response=response,
                now_epoch=now_epoch,
                settings=settings,
            )
        except HistoryError as err:
            raise AppError("SERVER_UNAVAILABLE", err.message, retryable=False) from err
        transaction.extend([
            {"Put": {
                "TableName": settings.content_table_name,
                "Item": _serialize(content),
                "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
            }},
            {"Put": {
                "TableName": settings.control_table_name,
                "Item": _serialize(locator),
                "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
            }},
        ])
    else:
        transaction.append({"Put": {
            "TableName": settings.control_table_name,
            "Item": _serialize(_tombstone(settings, account_id, payload_hash, response["requestId"], authorization, now_epoch, state)),
            "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
        }})
    _append_progress_if_current(
        transaction, settings, control, account_id, authorization, state, now_epoch
    )
    transaction.append({"Update": {
        "TableName": settings.control_table_name,
        "Key": _serialize({"PK": f"USER#{account_id}", "SK": f"COMPLETION#{response['requestId']}"}),
        "UpdateExpression": "SET #status = :complete, completedAtEpoch = :now, expiresAt = :expires_at, expiryBucket = :expiry_bucket REMOVE lifecycleBucket, lifecycleAt",
        "ConditionExpression": "#status = :pending AND acceptedSequence = :accepted_sequence",
        "ExpressionAttributeNames": {"#status": "status"},
        "ExpressionAttributeValues": _serialize({
            ":complete": "COMPLETE", ":pending": "PENDING", ":now": now_epoch,
            ":accepted_sequence": captured_sequence,
            ":expires_at": now_epoch + settings.dedup_retention_days * 86400,
            ":expiry_bucket": _control_expiry_bucket(now_epoch + settings.dedup_retention_days * 86400, response["requestId"]),
        }),
    }})
    return not active_generation


def assert_history_result_visible(account_id, request_id):
    settings = HistorySettings.from_env()
    if not settings.durable_replay_enabled:
        return
    _validate(settings, "durable_replay")
    if not settings.users_table_name or not settings.deletion_ledger_table_name:
        raise AppError("SERVER_UNAVAILABLE", "The account authority is unavailable.", retryable=False)
    profile = dynamodb.Table(settings.users_table_name).get_item(
        Key={"PK": f"USER#{account_id}", "SK": "PROFILE"}, ConsistentRead=True
    ).get("Item")
    deletion = dynamodb.Table(settings.deletion_ledger_table_name).get_item(
        Key={"PK": f"ACCOUNT#{account_id}", "SK": "ACCOUNT_DELETION"}, ConsistentRead=True
    ).get("Item")
    if (
        not profile or profile.get("sub") != account_id
        or profile.get("status") != "ACTIVE" or profile.get("ageVerified") is not True
        or deletion is not None
    ):
        raise AppError(
            "RESULT_UNAVAILABLE",
            "This request was completed but its retained result is no longer available.",
            retryable=False,
        )
    control = dynamodb.Table(settings.control_table_name)
    state = control.get_item(
        Key={"PK": f"USER#{account_id}", "SK": "STATE"}, ConsistentRead=True
    ).get("Item")
    locator = control.get_item(
        Key={"PK": f"USER#{account_id}", "SK": f"REQUEST#{request_id}"}, ConsistentRead=True
    ).get("Item")
    if (
        not state
        or state.get("accountStatus") != "ACTIVE"
        or not locator
        or locator.get("status") != "ACTIVE"
        or int(locator.get("historyGeneration", -1)) != int(state.get("historyGeneration", -2))
    ):
        raise AppError(
            "RESULT_UNAVAILABLE",
            "This request was completed but its retained result is no longer available.",
            retryable=False,
        )


def _append_progress_if_current(transaction, settings, control, account_id, authorization, state, now_epoch):
    if not settings.recognition_enabled:
        return
    _validate(settings, "recognition")
    captured = _auth_int(authorization, "recognitionGeneration", 0)
    current = int(state["recognitionGeneration"])
    if captured != current or state.get("accountStatus") != "ACTIVE":
        return
    progress = control.get_item(
        Key={"PK": f"USER#{account_id}", "SK": f"PROGRESS#{current}"}, ConsistentRead=True
    ).get("Item")
    if not progress or int(progress.get("recognitionGeneration", -1)) != current:
        raise AppError("SERVER_UNAVAILABLE", "Recognition progress is not initialized.", retryable=False)
    old_version = _exact_int(progress.get("stateVersion"), minimum=1)
    old_count = _exact_int(progress.get("qualifyingChecks"), minimum=0)
    if old_version is None or old_count is None:
        raise AppError("SERVER_UNAVAILABLE", "Stored recognition progress is invalid.", retryable=False)
    new_count = old_count + 1
    awarded = [item["id"] for item in settings.badge_catalog if item["threshold"] <= new_count]
    updated = dict(progress)
    updated.update({
        "qualifyingChecks": new_count,
        "awardedBadgeIds": awarded,
        "stateVersion": old_version + 1,
        "updatedAtEpoch": now_epoch,
    })
    transaction.append({"Put": {
        "TableName": settings.control_table_name,
        "Item": _serialize(updated),
        "ConditionExpression": "stateVersion = :old_version AND recognitionGeneration = :generation",
        "ExpressionAttributeValues": _serialize({":old_version": old_version, ":generation": current}),
    }})


def _tombstone(settings, account_id, payload_hash, request_id, authorization, now_epoch, state):
    expires_at = now_epoch + settings.dedup_retention_days * 86400
    item = {
        "PK": f"USER#{account_id}", "SK": f"REQUEST#{request_id}",
        "recordType": "REQUEST", "schemaVersion": settings.schema_version,
        "status": "ACCOUNT_ERASED" if state.get("accountStatus") != "ACTIVE" else "CLEARED",
        "requestId": request_id, "payloadHash": payload_hash,
        "historyGeneration": _auth_int(authorization, "historyGeneration", 0),
        "acceptedSequence": _auth_int(authorization, "acceptedSequence", 1),
        "deletedAtEpoch": now_epoch,
        "expiresAt": expires_at,
    }
    item["expiryBucket"] = _control_expiry_bucket(expires_at, request_id)
    return item


def _completion_job(settings, account_id, request_id, authorization, now_epoch):
    expires_at = now_epoch + settings.dedup_retention_days * 86400
    shard = int(hashlib.sha256(request_id.encode()).hexdigest()[:2], 16) % 16
    return {
        "PK": f"USER#{account_id}", "SK": f"COMPLETION#{request_id}",
        "recordType": "COMPLETION", "schemaVersion": settings.schema_version,
        "status": "PENDING", "requestId": request_id,
        "historyGeneration": authorization["historyGeneration"],
        "recognitionGeneration": authorization["recognitionGeneration"],
        "acceptedSequence": authorization["acceptedSequence"],
        "acceptedAtEpoch": now_epoch,
        "lifecycleBucket": f"PENDING#{shard:02d}", "lifecycleAt": now_epoch,
        "expiresAt": expires_at,
        "expiryBucket": _control_expiry_bucket(expires_at, request_id),
    }


def _account_authority_checks(settings, account_id):
    if not settings.users_table_name or not settings.deletion_ledger_table_name:
        raise AppError("SERVER_UNAVAILABLE", "The account authority is unavailable.", retryable=False)
    return [
        {"ConditionCheck": {
            "TableName": settings.users_table_name,
            "Key": _serialize({"PK": f"USER#{account_id}", "SK": "PROFILE"}),
            "ConditionExpression": "#status = :active AND ageVerified = :true AND #sub = :account_id",
            "ExpressionAttributeNames": {"#status": "status", "#sub": "sub"},
            "ExpressionAttributeValues": _serialize({
                ":active": "ACTIVE", ":true": True, ":account_id": account_id,
            }),
        }},
        {"ConditionCheck": {
            "TableName": settings.deletion_ledger_table_name,
            "Key": _serialize({"PK": f"ACCOUNT#{account_id}", "SK": "ACCOUNT_DELETION"}),
            "ConditionExpression": "attribute_not_exists(PK)",
        }},
    ]


def _control_expiry_bucket(expires_at, value):
    hour = datetime.fromtimestamp(expires_at, UTC).strftime("%Y%m%d%H")
    shard = int(hashlib.sha256(value.encode()).hexdigest()[:2], 16) % 16
    return f"CONTROL#{hour}#{shard:02d}"


def _validate_state(state):
    if not isinstance(state, dict) or state.get("accountStatus") not in {"ACTIVE", "DELETING"}:
        raise AppError("SERVER_UNAVAILABLE", "The account History state is unavailable.", retryable=False)
    for field, minimum in (("historyGeneration", 0), ("recognitionGeneration", 0), ("acceptedSequence", 0)):
        if _exact_int(state.get(field), minimum=minimum) is None:
            raise AppError("SERVER_UNAVAILABLE", "The account History state is invalid.", retryable=False)


def _auth_int(authorization, field, minimum):
    value = _exact_int(authorization.get(field), minimum=minimum)
    if value is None:
        raise AppError("SERVER_UNAVAILABLE", "The persisted History authorization is invalid.", retryable=False)
    return value


def _exact_int(value, minimum):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    result = int(value)
    return result if result == value and result >= minimum else None


def _validate(settings, operation):
    try:
        getattr(settings, f"validate_{operation}")()
    except HistoryError as err:
        raise AppError("SERVER_UNAVAILABLE", err.message, retryable=err.retryable) from err


def _account_hash(account_id):
    return hashlib.sha256(account_id.encode("utf-8")).hexdigest()


def _serialize(item):
    return {key: _serialize_value(value) for key, value in item.items()}


def _serialize_value(value):
    if value is None:
        return {"NULL": True}
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, (int, float, Decimal)):
        return {"N": str(value)}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, list):
        return {"L": [_serialize_value(item) for item in value]}
    if isinstance(value, dict):
        return {"M": _serialize(value)}
    raise TypeError(f"Unsupported DynamoDB value type: {type(value).__name__}")
