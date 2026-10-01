from datetime import UTC, datetime
from decimal import Decimal
import time
import uuid

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

import config
from errors import AppError
from validation import AGE_BANDS, STATE_CODES

CURRENT_SK = "DEMOGRAPHIC_RESEARCH"
OPERATION_PREFIX = "DEMOGRAPHIC_OPERATION#"
AUDIT_PREFIX = "DEMOGRAPHIC_CONSENT#"
AUTHORITY_SK = "DEMOGRAPHIC_RESEARCH_AUTHORITY"
MAX_VERSION = 9007199254740991

resource = boto3.resource("dynamodb")
client = boto3.client("dynamodb")
users = resource.Table(config.USERS_TABLE_NAME)
ledger = resource.Table(config.DELETION_LEDGER_TABLE_NAME)
serializer = TypeSerializer()


def get_profile(account_id, operation_id=None, *, now_epoch=None):
    now = int(time.time()) if now_epoch is None else now_epoch
    _assert_account(account_id)
    current = _current(account_id)
    authority = _authority(account_id)
    _assert_authority(current, authority)
    operation = _operation(account_id, operation_id, now) if operation_id else None
    # Re-authorize after every value-bearing read so a concurrent deletion or
    # profile transition cannot release the assembled response.
    _assert_account(account_id)
    return _response(current, operation_id, operation, now)


def update_profile(account_id, payload, *, now_epoch=None):
    now = int(time.time()) if now_epoch is None else now_epoch
    action = payload["action"]
    if action in {"enroll", "update"} and not config.ENROLLMENT_ENABLED:
        raise AppError("ENROLLMENT_UNAVAILABLE", "Demographic profile choices are temporarily unavailable.", retryable=True)
    _assert_account(account_id)
    replay = _operation(account_id, payload["operationId"], now)
    if replay:
        _assert_replay(replay, payload)
        return get_profile(account_id, payload["operationId"], now_epoch=now)
    current = _current(account_id)
    authority = _authority(account_id)
    _assert_authority(current, authority)
    if current.get("lastOperationId") == payload["operationId"]:
        # The seven-day receipt may already be gone, but the current profile
        # still reserves its operation ID for the full current-state lifetime.
        raise AppError("CONFLICT", "operationId was already used for another request.")
    version = int(current.get("stateVersion", 0))
    if payload["expectedStateVersion"] != version or version >= MAX_VERSION:
        raise AppError("CONFLICT", "The demographic profile changed after this choice was reviewed.", retryable=True)
    state = current.get("state", "not_enrolled")
    expired = state == "enrolled" and int(current.get("validUntilEpoch", 0)) <= now
    if action == "enroll" and state not in {"not_enrolled", "withdrawn"} and not expired:
        raise AppError("CONFLICT", "A demographic profile is already enrolled.")
    if action == "update" and (state != "enrolled" or expired):
        raise AppError("CONFLICT", "Reconsent is required before this profile can be updated.")
    if action == "withdraw" and state != "enrolled":
        raise AppError("CONFLICT", "The demographic profile is not enrolled.")
    operation_id = payload["operationId"]
    epoch_id = str(uuid.uuid4()) if action == "enroll" else current.get("consentEpochId")
    if not _uuid4(epoch_id):
        raise AppError("SERVER_UNAVAILABLE", "Stored demographic state is invalid.", retryable=True)
    new_version = version + 1
    cleanup_deadline = now + 24 * 3600
    base = {
        "PK": f"USER#{account_id}", "SK": CURRENT_SK, "schemaVersion": 1, "recordVersion": 1,
        "environment": config.ENVIRONMENT, "purpose": config.PURPOSE, "purposeVersion": config.PURPOSE_VERSION, "noticeVersion": config.NOTICE_VERSION,
        "policyVersion": config.POLICY_VERSION, "stateVersion": new_version, "consentEpochId": epoch_id,
        "updatedAtEpoch": now, "lastOperationId": operation_id,
    }
    if action in {"enroll", "update"}:
        valid_until = now + config.PROFILE_RETENTION_DAYS * 86400 if action == "enroll" else int(current["validUntilEpoch"])
        new = base | {"state": "enrolled", "ageBand": payload["ageBand"], "stateCode": payload["stateCode"],
                      "validUntilEpoch": valid_until, "expiresAt": valid_until}
    else:
        new = base | {"state": "withdrawn", "withdrawnAtEpoch": now,
                      "valueCleanupDeadlineEpoch": cleanup_deadline, "valueCleanupCompletedAtEpoch": now,
                      "expiresAt": now + config.PROFILE_RETENTION_DAYS * 86400}
    operation = {
        "PK": f"USER#{account_id}", "SK": OPERATION_PREFIX + operation_id, "schemaVersion": 1, "recordVersion": 1,
        "operationId": operation_id, "action": action, "expectedStateVersion": version,
        "requestNoticeVersion": payload["noticeVersion"], "resultingState": new["state"], "stateVersion": new_version,
        "consentEpochId": epoch_id, "occurredAtEpoch": now, "expiresAt": now + config.OPERATION_RETENTION_DAYS * 86400,
    }
    if action in {"enroll", "update"}:
        operation |= {"ageBand": payload["ageBand"], "stateCode": payload["stateCode"]}
    audit = {
        "PK": f"USER#{account_id}", "SK": f"{AUDIT_PREFIX}{epoch_id}#{now}#{operation_id}",
        "schemaVersion": 1, "recordVersion": 1, "eventType": f"demographic.research.{action}",
        "purpose": config.PURPOSE, "purposeVersion": config.PURPOSE_VERSION, "noticeVersion": config.NOTICE_VERSION, "policyVersion": config.POLICY_VERSION,
        "consentEpochId": epoch_id, "operationId": operation_id, "resultingState": new["state"],
        "stateVersion": new_version, "occurredAtEpoch": now, "expiresAt": now + config.CONSENT_AUDIT_RETENTION_DAYS * 86400,
    }
    if action in {"update", "withdraw"}:
        audit |= {"valueCleanupDeadlineEpoch": cleanup_deadline, "valueCleanupCompletedAtEpoch": now}
    authority = {
        "PK": f"ACCOUNT#{account_id}", "SK": AUTHORITY_SK, "schemaVersion": 1, "recordVersion": 1,
        "purpose": config.PURPOSE, "purposeVersion": config.PURPOSE_VERSION, "noticeVersion": config.NOTICE_VERSION, "policyVersion": config.POLICY_VERSION,
        "state": new["state"], "stateVersion": new_version, "consentEpochId": epoch_id,
        "lastOperationId": operation_id, "validUntilEpoch": new.get("validUntilEpoch"), "updatedAtEpoch": now,
        "expiresAt": new["expiresAt"],
    }
    transaction = [
        _current_write(current, new),
        _authority_write(current, authority),
        {"Put": {"TableName": config.USERS_TABLE_NAME, "Item": wire(operation), "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)"}},
        {"Put": {"TableName": config.USERS_TABLE_NAME, "Item": wire(audit), "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)"}},
        _profile_check(account_id),
        _deletion_check(account_id),
    ]
    old_operation = current.get("lastOperationId")
    if old_operation:
        transaction.append({"Delete": {"TableName": config.USERS_TABLE_NAME,
                                        "Key": wire({"PK": f"USER#{account_id}", "SK": OPERATION_PREFIX + old_operation})}})
    try:
        client.transact_write_items(TransactItems=transaction)
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") != "TransactionCanceledException":
            raise
        replay = _operation(account_id, operation_id, now)
        if replay:
            _assert_replay(replay, payload)
            return get_profile(account_id, operation_id, now_epoch=now)
        _assert_account(account_id)
        raise AppError("CONFLICT", "The demographic profile changed during this request.", retryable=True) from err
    return _response(new, operation_id, operation, now)


def _assert_account(account_id):
    fence = ledger.get_item(Key={"PK": f"ACCOUNT#{account_id}", "SK": "ACCOUNT_DELETION"}, ConsistentRead=True).get("Item")
    if fence:
        raise AppError("ACCOUNT_DELETION_IN_PROGRESS", "Account deletion has started.")
    profile = users.get_item(Key={"PK": f"USER#{account_id}", "SK": "PROFILE"}, ConsistentRead=True).get("Item")
    if (not profile or profile.get("sub") != account_id or profile.get("status") != "ACTIVE"
            or profile.get("ageVerified") is not True or profile.get("agePolicyVersion") != "v1.0"
            or not isinstance(profile.get("ageVerifiedAt"), str) or not profile["ageVerifiedAt"]):
        raise AppError("AGE_VERIFICATION_REQUIRED", "Complete account onboarding and age confirmation first.")


def _current(account_id):
    item = users.get_item(Key={"PK": f"USER#{account_id}", "SK": CURRENT_SK}, ConsistentRead=True).get("Item")
    if not item:
        return {"state": "not_enrolled", "stateVersion": 0}
    result = plain(item)
    required = {"PK","SK","schemaVersion","recordVersion","environment","purpose","purposeVersion","noticeVersion","policyVersion","state","stateVersion","consentEpochId","updatedAtEpoch","lastOperationId","expiresAt"}
    if (not required.issubset(result) or result["PK"] != f"USER#{account_id}" or result["SK"] != CURRENT_SK
            or result.get("environment") != config.ENVIRONMENT or result.get("purpose") != config.PURPOSE
            or result.get("purposeVersion") != config.PURPOSE_VERSION
            or result.get("state") not in {"enrolled","withdrawn"} or not _uuid4(result.get("consentEpochId"))
            or not _uuid4(result.get("lastOperationId")) or type(result.get("stateVersion")) is not int):
        raise AppError("SERVER_UNAVAILABLE", "Stored demographic state is invalid.", retryable=True)
    if result["state"] == "enrolled":
        if (set(result) != required | {"ageBand","stateCode","validUntilEpoch"}
                or result.get("ageBand") not in AGE_BANDS or result.get("stateCode") not in STATE_CODES
                or type(result.get("validUntilEpoch")) is not int or result["expiresAt"] != result["validUntilEpoch"]):
            raise AppError("SERVER_UNAVAILABLE", "Stored demographic state is invalid.", retryable=True)
    elif set(result) != required | {"withdrawnAtEpoch","valueCleanupDeadlineEpoch","valueCleanupCompletedAtEpoch"}:
        raise AppError("SERVER_UNAVAILABLE", "Stored demographic state is invalid.", retryable=True)
    return result


def _authority(account_id):
    item = ledger.get_item(Key={"PK": f"ACCOUNT#{account_id}", "SK": AUTHORITY_SK}, ConsistentRead=True).get("Item")
    result = plain(item) if item else None
    if result is not None:
        expected = {"PK","SK","schemaVersion","recordVersion","purpose","purposeVersion","noticeVersion","policyVersion",
                    "state","stateVersion","consentEpochId","lastOperationId","updatedAtEpoch","expiresAt"}
        if result.get("state") == "enrolled":
            expected.add("validUntilEpoch")
        if (set(result) != expected or result.get("PK") != f"ACCOUNT#{account_id}" or result.get("SK") != AUTHORITY_SK
                or result.get("purpose") != config.PURPOSE or result.get("purposeVersion") != config.PURPOSE_VERSION
                or result.get("noticeVersion") != config.NOTICE_VERSION
                or result.get("policyVersion") != config.POLICY_VERSION or result.get("state") not in {"enrolled","withdrawn"}
                or type(result.get("stateVersion")) is not int or not _uuid4(result.get("consentEpochId"))
                or not _uuid4(result.get("lastOperationId"))):
            raise AppError("SERVER_UNAVAILABLE", "Demographic authority is invalid.", retryable=True)
    return result


def _assert_authority(current, authority):
    if current.get("state") == "not_enrolled":
        if authority is not None:
            raise AppError("SERVER_UNAVAILABLE", "Demographic restored-copy suppression rejected the profile.", retryable=True)
        return
    exact = {"state","stateVersion","consentEpochId","lastOperationId","validUntilEpoch"}
    if not authority or any(authority.get(k) != current.get(k) for k in exact):
        raise AppError("SERVER_UNAVAILABLE", "Demographic restored-copy suppression rejected the profile.", retryable=True)


def _operation(account_id, operation_id, now):
    if not operation_id:
        return None
    item = users.get_item(Key={"PK": f"USER#{account_id}", "SK": OPERATION_PREFIX + operation_id}, ConsistentRead=True).get("Item")
    result = plain(item) if item else None
    if not result or result.get("expiresAt", 0) <= now:
        return None
    fields = {"PK","SK","schemaVersion","recordVersion","operationId","action","expectedStateVersion",
              "requestNoticeVersion","resultingState","stateVersion","consentEpochId","occurredAtEpoch","expiresAt"}
    if result.get("action") in {"enroll","update"}:
        fields |= {"ageBand","stateCode"}
    if (set(result) != fields or result.get("PK") != f"USER#{account_id}"
            or result.get("SK") != OPERATION_PREFIX + operation_id or result.get("operationId") != operation_id
            or result.get("action") not in {"enroll","update","withdraw"} or not _uuid4(result.get("consentEpochId"))
            or result.get("requestNoticeVersion") != config.NOTICE_VERSION
            or (result.get("action") in {"enroll","update"}
                and (result.get("ageBand") not in AGE_BANDS or result.get("stateCode") not in STATE_CODES))):
        raise AppError("SERVER_UNAVAILABLE", "Stored demographic operation is invalid.", retryable=True)
    return result


def _assert_replay(row, payload):
    fields = ("action", "expectedStateVersion", "requestNoticeVersion")
    if any(row.get(k) != payload.get({"requestNoticeVersion": "noticeVersion"}.get(k, k)) for k in fields):
        raise AppError("CONFLICT", "operationId was already used for another request.")
    if payload["action"] in {"enroll", "update"} and (row.get("ageBand") != payload["ageBand"] or row.get("stateCode") != payload["stateCode"]):
        raise AppError("CONFLICT", "operationId was already used for another request.")


def _current_write(current, new):
    put = {"TableName": config.USERS_TABLE_NAME, "Item": wire(new)}
    if current.get("state") == "not_enrolled":
        put["ConditionExpression"] = "attribute_not_exists(PK) AND attribute_not_exists(SK)"
    else:
        put |= {"ConditionExpression": "stateVersion = :v AND lastOperationId = :op",
                "ExpressionAttributeValues": wire({":v": current["stateVersion"], ":op": current["lastOperationId"]})}
    return {"Put": put}


def _authority_write(current, authority):
    put = {"TableName": config.DELETION_LEDGER_TABLE_NAME, "Item": wire(authority)}
    if current.get("state") == "not_enrolled":
        put["ConditionExpression"] = "attribute_not_exists(PK) AND attribute_not_exists(SK)"
    else:
        put |= {"ConditionExpression": "stateVersion = :v AND lastOperationId = :op",
                "ExpressionAttributeValues": wire({":v": current["stateVersion"], ":op": current["lastOperationId"]})}
    return {"Put": put}


def _profile_check(account_id):
    return {"ConditionCheck": {"TableName": config.USERS_TABLE_NAME,
        "Key": wire({"PK": f"USER#{account_id}", "SK": "PROFILE"}),
        "ConditionExpression": "#sub = :sub AND #status = :active AND ageVerified = :true AND agePolicyVersion = :age_policy AND attribute_exists(ageVerifiedAt)",
        "ExpressionAttributeNames": {"#sub": "sub", "#status": "status"},
        "ExpressionAttributeValues": wire({":sub": account_id, ":active": "ACTIVE", ":true": True, ":age_policy": "v1.0"})}}


def _deletion_check(account_id):
    return {"ConditionCheck": {"TableName": config.DELETION_LEDGER_TABLE_NAME,
        "Key": wire({"PK": f"ACCOUNT#{account_id}", "SK": "ACCOUNT_DELETION"}),
        "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)"}}


def _response(current, operation_id, operation, now):
    state = current.get("state", "not_enrolled")
    expired = state == "enrolled" and current.get("validUntilEpoch", 0) <= now
    public_state = "review_required" if expired else state
    visible = state == "enrolled" and not expired
    result = {"schemaVersion": 1, "purpose": config.PURPOSE, "purposeVersion": config.PURPOSE_VERSION, "noticeVersion": config.NOTICE_VERSION,
              "policyVersion": config.POLICY_VERSION, "state": public_state,
              "stateVersion": int(current.get("stateVersion", 0)),
              "ageBand": current.get("ageBand") if visible else None,
              "stateCode": current.get("stateCode") if visible else None,
              "validUntilEpoch": current.get("validUntilEpoch") if visible else None,
              "updatedAtEpoch": current.get("updatedAtEpoch"),
              "capabilities": {"enrollmentEnabled": config.ENROLLMENT_ENABLED and (state in {"not_enrolled","withdrawn"} or expired),
                               "updateEnabled": config.ENROLLMENT_ENABLED and visible,
                               "withdrawalEnabled": state == "enrolled"}}
    if operation_id:
        result["operation"] = {"operationId": operation_id, "status": "not_found", "action": None,
                               "resultingState": None, "stateVersion": None}
        if operation:
            result["operation"].update(status="applied", action=operation["action"],
                                       resultingState=operation["resultingState"], stateVersion=int(operation["stateVersion"]))
    return result


def wire(values):
    return {key: serializer.serialize(value) for key, value in values.items() if value is not None}


def plain(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    return value


def _uuid4(value):
    try:
        return str(uuid.UUID(value, version=4)) == value
    except (TypeError, ValueError, AttributeError):
        return False
