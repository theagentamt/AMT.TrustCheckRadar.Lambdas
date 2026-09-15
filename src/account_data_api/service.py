from decimal import Decimal
import hashlib
import re
import time

from errors import AppError


COMMAND_FIELDS = {
    "PK", "SK", "schemaVersion", "recordVersion", "environment", "eventType",
    "accountId", "operationId", "status", "occurredAtEpoch", "deleteByEpoch",
}
RECEIPT_FIELDS = {
    "PK", "SK", "schemaVersion", "recordVersion", "environment", "eventType",
    "component", "status", "operationId", "occurredAtEpoch", "requestOccurredAtEpoch",
    "retainUntilEpoch",
}
ACCOUNT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,199}$")
ACCOUNT_DELETION_RECEIPT_RETENTION_DAYS = 120
DEVICE_RECOVERY_RECEIPT_RETENTION_DAYS = 7
DEVICE_RECOVERY_AUDIT_RETENTION_DAYS = 90
DEVICE_RECOVERY_RATE_STATE_TTL_SECONDS = 24 * 3600
ANALYSIS_REQUEST_ID_TTL_SECONDS = 24 * 3600
ANALYSIS_ABUSE_FAMILIES = ("REQUEST", "RATE", "SCAN_RATE", "CONSUMPTION")
PAYLOAD_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class AccountDeletionService:
    def __init__(
        self, *, environment, ledger_table, users_table_name,
        ledger_table_name, dynamodb_client, required_components,
        erasure_sla_hours=24, now=lambda: int(time.time()),
    ):
        self.environment = environment
        self.ledger_table = ledger_table
        self.users_table_name = users_table_name
        self.ledger_table_name = ledger_table_name
        self.dynamodb_client = dynamodb_client
        self.required_components = tuple(required_components)
        self.erasure_sla_hours = erasure_sla_hours
        self.now = now

    def request(self, account_id, operation_id):
        existing = self._command(account_id)
        if existing:
            return self._replay(existing, operation_id)
        now = self.now()
        command = {
            "PK": f"ACCOUNT#{account_id}", "SK": "ACCOUNT_DELETION",
            "schemaVersion": 1, "recordVersion": 1,
            "environment": self.environment,
            "eventType": "account.deletion.requested", "accountId": account_id,
            "operationId": operation_id, "status": "REQUESTED",
            "occurredAtEpoch": now,
            "deleteByEpoch": now + self.erasure_sla_hours * 3600,
        }
        transaction = [
            {"Update": {
                "TableName": self.users_table_name,
                "Key": _serialize({"PK": f"USER#{account_id}", "SK": "PROFILE"}),
                "UpdateExpression": (
                    "SET #status = :requested, deletionOperationId = :operation_id, "
                    "deletionRequestedAtEpoch = :now, updatedAtEpoch = :now"
                ),
                "ConditionExpression": (
                    "#status = :active AND ageVerified = :true AND #sub = :account"
                ),
                "ExpressionAttributeNames": {"#status": "status", "#sub": "sub"},
                "ExpressionAttributeValues": _serialize({
                    ":requested": "DELETION_REQUESTED", ":active": "ACTIVE",
                    ":true": True, ":account": account_id,
                    ":operation_id": operation_id, ":now": now,
                }),
            }},
            {"Put": {
                "TableName": self.ledger_table_name, "Item": _serialize(command),
                "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
            }},
        ]
        try:
            self.dynamodb_client.transact_write_items(TransactItems=transaction)
        except Exception as err:
            if _error_code(err) != "TransactionCanceledException":
                raise
            existing = self._command(account_id)
            if existing:
                return self._replay(existing, operation_id)
            raise AppError(
                "CONFLICT", "Account state changed during the deletion request.", retryable=True
            ) from err
        return self.status(account_id, command=command)

    def status(self, account_id, *, command=None):
        command = command or self._command(account_id)
        if not command:
            return {
                "schemaVersion": 1, "operation": "ACCOUNT_DELETION",
                "status": "NOT_REQUESTED", "completionEligible": False,
                "components": [],
            }
        command = validate_command(command, self.environment)
        components = []
        for component in self.required_components:
            receipt = self.ledger_table.get_item(
                Key={"PK": command["PK"], "SK": f"ACCOUNT_DELETION#{component}"},
                ConsistentRead=True,
            ).get("Item")
            complete = _valid_component_receipt(receipt, command, component)
            components.append({"component": component, "status": "COMPLETE" if complete else "PENDING"})
        completion_eligible = all(value["status"] == "COMPLETE" for value in components)
        return {
            "schemaVersion": 1, "operation": "ACCOUNT_DELETION",
            "operationId": command["operationId"], "status": command["status"],
            "requestedAtEpoch": command["occurredAtEpoch"],
            "deleteByEpoch": command["deleteByEpoch"],
            "completionEligible": completion_eligible,
            "components": components,
        }

    def _command(self, account_id):
        return self.ledger_table.get_item(
            Key={"PK": f"ACCOUNT#{account_id}", "SK": "ACCOUNT_DELETION"},
            ConsistentRead=True,
        ).get("Item")

    def _replay(self, command, operation_id):
        command = validate_command(command, self.environment)
        if command["operationId"] != operation_id:
            raise AppError(
                "IDEMPOTENCY_CONFLICT", "The account already has a different deletion request."
            )
        return self.status(command["accountId"], command=command)


def ensure_session_revoked(
    command, *, user_pool_id, cognito, ledger_table, now_epoch=None,
):
    command = validate_command(command, command["environment"])
    key = {"PK": command["PK"], "SK": "ACCOUNT_DELETION#SESSION_REVOCATION"}
    existing = ledger_table.get_item(Key=key, ConsistentRead=True).get("Item")
    if _valid_component_receipt(existing, command, "SESSION_REVOCATION"):
        return True
    try:
        cognito.admin_user_global_sign_out(
            UserPoolId=user_pool_id, Username=command["accountId"]
        )
    except Exception as err:
        if _error_code(err) != "UserNotFoundException":
            raise
    completed_at = int(time.time()) if now_epoch is None else now_epoch
    receipt = _component_receipt(command, "SESSION_REVOCATION", completed_at)
    try:
        ledger_table.put_item(
            Item=receipt,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
    except Exception as err:
        if _error_code(err) != "ConditionalCheckFailedException":
            raise
        existing = ledger_table.get_item(Key=key, ConsistentRead=True).get("Item")
        if not _valid_component_receipt(existing, command, "SESSION_REVOCATION"):
            raise
    return True


def delete_device_bindings(
    command, *, device_table, ledger_table, page_size=100, now_epoch=None,
):
    command = validate_command(command, command["environment"])
    if page_size != 100:
        raise ValueError("Device deletion page size must be 100")
    receipt_key = {
        "PK": command["PK"], "SK": "ACCOUNT_DELETION#DEVICE_BINDINGS",
    }
    progress_key = {
        "PK": command["PK"], "SK": "ACCOUNT_DELETION#DEVICE_BINDINGS_PROGRESS",
    }
    existing = ledger_table.get_item(
        Key=receipt_key, ConsistentRead=True
    ).get("Item")
    if _valid_component_receipt(existing, command, "DEVICE_BINDINGS"):
        ledger_table.delete_item(Key=progress_key)
        return {"deleted": 0, "complete": True, "alreadyComplete": True}
    progress = ledger_table.get_item(
        Key=progress_key, ConsistentRead=True
    ).get("Item")
    start_key = None
    if progress:
        if (
            progress.get("operationId") != command["operationId"]
            or progress.get("requestOccurredAtEpoch") != command["occurredAtEpoch"]
        ):
            raise ValueError("Device-deletion progress belongs to another request")
        start_key = progress.get("lastEvaluatedKey")
        if start_key is not None and not _valid_key(start_key):
            raise ValueError("Invalid device-deletion continuation")
    kwargs = {
        "KeyConditionExpression": "PK = :pk",
        "ExpressionAttributeValues": {":pk": f"USER#{command['accountId']}"},
        "ConsistentRead": True,
        "Limit": page_size,
        "ProjectionExpression": "PK, SK",
    }
    if start_key:
        kwargs["ExclusiveStartKey"] = start_key
    page = device_table.query(**kwargs)
    deleted = 0
    for item in page.get("Items") or []:
        if (
            item.get("PK") != f"USER#{command['accountId']}"
            or not isinstance(item.get("SK"), str) or not item["SK"]
        ):
            raise ValueError("Device-deletion query returned an invalid key")
        device_table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
        deleted += 1
    continuation = page.get("LastEvaluatedKey")
    observed_at = int(time.time()) if now_epoch is None else now_epoch
    if continuation:
        if not _valid_key(continuation):
            raise ValueError("Invalid device-deletion continuation")
        ledger_table.put_item(Item={
            **progress_key,
            "recordType": "ACCOUNT_DELETION_DEVICE_BINDINGS_PROGRESS",
            "schemaVersion": 1,
            "operationId": command["operationId"],
            "requestOccurredAtEpoch": command["occurredAtEpoch"],
            "lastEvaluatedKey": dict(continuation),
            "updatedAtEpoch": observed_at,
        })
        return {"deleted": deleted, "complete": False, "alreadyComplete": False}
    receipt = _component_receipt(command, "DEVICE_BINDINGS", observed_at)
    try:
        ledger_table.put_item(
            Item=receipt,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
    except Exception as err:
        if _error_code(err) != "ConditionalCheckFailedException":
            raise
        existing = ledger_table.get_item(
            Key=receipt_key, ConsistentRead=True
        ).get("Item")
        if not _valid_component_receipt(existing, command, "DEVICE_BINDINGS"):
            raise
    if progress:
        ledger_table.delete_item(Key=progress_key)
    return {"deleted": deleted, "complete": True, "alreadyComplete": False}


def delete_device_recovery_control(
    command, *, recovery_table, ledger_table, page_size=100,
    receipt_retention_days=DEVICE_RECOVERY_RECEIPT_RETENTION_DAYS,
    audit_retention_days=DEVICE_RECOVERY_AUDIT_RETENTION_DAYS,
    rate_state_ttl_seconds=DEVICE_RECOVERY_RATE_STATE_TTL_SECONDS,
    account_receipt_retention_days=ACCOUNT_DELETION_RECEIPT_RETENTION_DAYS,
    now_epoch=None,
):
    """Delete rate state and minimize bounded recovery evidence for one account."""
    command = validate_command(command, command["environment"])
    if (
        page_size != 100
        or receipt_retention_days != 7
        or audit_retention_days != 90
        or rate_state_ttl_seconds != 24 * 3600
        or account_receipt_retention_days != 120
    ):
        raise ValueError("Invalid device-recovery deletion policy")
    receipt_key = {
        "PK": command["PK"], "SK": "ACCOUNT_DELETION#DEVICE_RECOVERY",
    }
    progress_key = {
        "PK": command["PK"], "SK": "ACCOUNT_DELETION#DEVICE_RECOVERY_PROGRESS",
    }
    existing = ledger_table.get_item(
        Key=receipt_key, ConsistentRead=True
    ).get("Item")
    if _valid_component_receipt(existing, command, "DEVICE_RECOVERY"):
        ledger_table.delete_item(Key=progress_key)
        return {
            "deleted": 0, "minimized": 0, "complete": True,
            "alreadyComplete": True,
        }
    progress = ledger_table.get_item(
        Key=progress_key, ConsistentRead=True
    ).get("Item")
    start_key = _progress_start_key(progress, command, "Device-recovery deletion")
    kwargs = {
        "KeyConditionExpression": "PK = :pk",
        "ExpressionAttributeValues": {":pk": f"USER#{command['accountId']}"},
        "ConsistentRead": True,
        "Limit": page_size,
    }
    if start_key:
        kwargs["ExclusiveStartKey"] = start_key
    page = recovery_table.query(**kwargs)
    now = int(time.time()) if now_epoch is None else now_epoch
    deleted = 0
    minimized = 0
    for item in page.get("Items") or []:
        _validate_recovery_control_key(item, command["accountId"])
        sort_key = item["SK"]
        if sort_key.startswith("RATE#"):
            _validate_rate_state(item, rate_state_ttl_seconds)
            recovery_table.delete_item(Key={"PK": item["PK"], "SK": sort_key})
            deleted += 1
            continue
        if sort_key.startswith("RECOVERY#"):
            minimal = _minimal_recovery_receipt(item, receipt_retention_days)
        elif sort_key.startswith("AUDIT#"):
            minimal = _minimal_recovery_audit(item, audit_retention_days)
        else:
            raise ValueError("Unknown device-recovery control item family")
        if int(minimal["expiresAt"]) <= now:
            recovery_table.delete_item(Key={"PK": item["PK"], "SK": sort_key})
            deleted += 1
        else:
            recovery_table.put_item(
                Item=minimal,
                ConditionExpression=(
                    "operationId = :operation_id AND expiresAt = :expires_at"
                ),
                ExpressionAttributeValues={
                    ":operation_id": minimal["operationId"],
                    ":expires_at": minimal["expiresAt"],
                },
            )
            minimized += 1
    continuation = page.get("LastEvaluatedKey")
    if continuation:
        if not _valid_key(continuation):
            raise ValueError("Invalid device-recovery deletion continuation")
        ledger_table.put_item(Item={
            **progress_key,
            "recordType": "ACCOUNT_DELETION_DEVICE_RECOVERY_PROGRESS",
            "schemaVersion": 1,
            "operationId": command["operationId"],
            "requestOccurredAtEpoch": command["occurredAtEpoch"],
            "lastEvaluatedKey": dict(continuation),
            "updatedAtEpoch": now,
        })
        return {
            "deleted": deleted, "minimized": minimized, "complete": False,
            "alreadyComplete": False,
        }
    receipt = _component_receipt(
        command, "DEVICE_RECOVERY", now,
        retention_days=account_receipt_retention_days,
    )
    try:
        ledger_table.put_item(
            Item=receipt,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
    except Exception as err:
        if _error_code(err) != "ConditionalCheckFailedException":
            raise
        existing = ledger_table.get_item(
            Key=receipt_key, ConsistentRead=True
        ).get("Item")
        if not _valid_component_receipt(existing, command, "DEVICE_RECOVERY"):
            raise
    if progress:
        ledger_table.delete_item(Key=progress_key)
    return {
        "deleted": deleted, "minimized": minimized, "complete": True,
        "alreadyComplete": False,
    }


def delete_analysis_abuse_control(
    command, *, abuse_table, ledger_table, page_size=100,
    request_retention_seconds=ANALYSIS_REQUEST_ID_TTL_SECONDS,
    account_receipt_retention_days=ACCOUNT_DELETION_RECEIPT_RETENTION_DAYS,
    now_epoch=None,
):
    """Drain account-scoped abuse state and retain only bounded request dedupe."""
    command = validate_command(command, command["environment"])
    if (
        page_size != 100
        or request_retention_seconds != 24 * 3600
        or account_receipt_retention_days != 120
    ):
        raise ValueError("Invalid analysis-abuse deletion policy")
    receipt_key = {
        "PK": command["PK"], "SK": "ACCOUNT_DELETION#ANALYSIS_ABUSE",
    }
    progress_key = {
        "PK": command["PK"], "SK": "ACCOUNT_DELETION#ANALYSIS_ABUSE_PROGRESS",
    }
    existing = ledger_table.get_item(
        Key=receipt_key, ConsistentRead=True
    ).get("Item")
    if _valid_component_receipt(existing, command, "ANALYSIS_ABUSE"):
        ledger_table.delete_item(Key=progress_key)
        return {
            "deleted": 0, "minimized": 0, "complete": True,
            "alreadyComplete": True, "family": None,
        }
    progress = ledger_table.get_item(
        Key=progress_key, ConsistentRead=True
    ).get("Item")
    family_index, start_key = _analysis_progress(progress, command)
    family = ANALYSIS_ABUSE_FAMILIES[family_index]
    account_hash = hashlib.sha256(command["accountId"].encode("utf-8")).hexdigest()
    partition = f"ANALYSIS#{family}#{account_hash}"
    kwargs = {
        "KeyConditionExpression": "PK = :pk",
        "ExpressionAttributeValues": {":pk": partition},
        "ConsistentRead": True,
        "Limit": page_size,
    }
    if start_key:
        kwargs["ExclusiveStartKey"] = start_key
    page = abuse_table.query(**kwargs)
    now = int(time.time()) if now_epoch is None else now_epoch
    deleted = 0
    minimized = 0
    for item in page.get("Items") or []:
        _validate_analysis_abuse_key(item, partition)
        key = {"PK": item["PK"], "SK": item["SK"]}
        if family != "REQUEST":
            abuse_table.delete_item(Key=key)
            deleted += 1
            continue
        minimal = _minimal_analysis_request(
            item, now_epoch=now, retention_seconds=request_retention_seconds
        )
        if minimal is None:
            abuse_table.delete_item(Key=key)
            deleted += 1
        elif item != minimal:
            abuse_table.put_item(
                Item=minimal,
                ConditionExpression="payloadHash = :payload_hash AND expiresAt = :expires_at",
                ExpressionAttributeValues={
                    ":payload_hash": minimal["payloadHash"],
                    ":expires_at": minimal["expiresAt"],
                },
            )
            minimized += 1
    continuation = page.get("LastEvaluatedKey")
    if continuation and not _valid_key(continuation):
        raise ValueError("Invalid analysis-abuse deletion continuation")
    if continuation:
        next_family_index = family_index
        next_start_key = dict(continuation)
    else:
        next_family_index = family_index + 1
        next_start_key = None
    if next_family_index < len(ANALYSIS_ABUSE_FAMILIES):
        progress_item = {
            **progress_key,
            "recordType": "ACCOUNT_DELETION_ANALYSIS_ABUSE_PROGRESS",
            "schemaVersion": 1,
            "operationId": command["operationId"],
            "requestOccurredAtEpoch": command["occurredAtEpoch"],
            "familyIndex": next_family_index,
            "updatedAtEpoch": now,
        }
        if next_start_key:
            progress_item["lastEvaluatedKey"] = next_start_key
        ledger_table.put_item(Item=progress_item)
        return {
            "deleted": deleted, "minimized": minimized, "complete": False,
            "alreadyComplete": False, "family": family,
        }
    receipt = _component_receipt(
        command, "ANALYSIS_ABUSE", now,
        retention_days=account_receipt_retention_days,
    )
    try:
        ledger_table.put_item(
            Item=receipt,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
    except Exception as err:
        if _error_code(err) != "ConditionalCheckFailedException":
            raise
        existing = ledger_table.get_item(
            Key=receipt_key, ConsistentRead=True
        ).get("Item")
        if not _valid_component_receipt(existing, command, "ANALYSIS_ABUSE"):
            raise
    if progress:
        ledger_table.delete_item(Key=progress_key)
    return {
        "deleted": deleted, "minimized": minimized, "complete": True,
        "alreadyComplete": False, "family": family,
    }


def reconcile_session_revocations(
    *, environment, ledger_table, device_table, recovery_table, abuse_table,
    user_pool_id, cognito,
    scan_limit, max_pages, device_page_size=100,
    recovery_page_size=100, analysis_abuse_page_size=100,
    now=lambda: int(time.time()),
):
    checkpoint_key = {
        "PK": f"LIFECYCLE#{environment}",
        "SK": "ACCOUNT_DELETION_SESSION_REVOCATION_RECONCILIATION",
    }
    checkpoint = ledger_table.get_item(
        Key=checkpoint_key, ConsistentRead=True
    ).get("Item") or {}
    start_key = checkpoint.get("lastEvaluatedKey")
    if start_key is not None and not _valid_key(start_key):
        raise ValueError("Invalid session-revocation reconciliation checkpoint")
    completed_pass_at = _exact_int(checkpoint.get("completedPassAtEpoch"))
    totals = {
        "scanned": 0, "matched": 0, "revoked": 0,
        "sessionAlreadyComplete": 0, "deviceRecordsDeleted": 0,
        "deviceComponentsCompleted": 0,
        "recoveryRecordsDeleted": 0, "recoveryRecordsMinimized": 0,
        "recoveryComponentsCompleted": 0,
        "analysisAbuseRecordsDeleted": 0,
        "analysisAbuseRecordsMinimized": 0,
        "analysisAbuseComponentsCompleted": 0,
    }
    truncated = False
    for page_number in range(max_pages):
        kwargs = {
            "ConsistentRead": True,
            "Limit": scan_limit,
            "FilterExpression": "eventType = :event_type AND #status = :requested",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": {
                ":event_type": "account.deletion.requested",
                ":requested": "REQUESTED",
            },
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        page = ledger_table.scan(**kwargs)
        totals["scanned"] += int(page.get("ScannedCount", 0))
        for raw in page.get("Items") or []:
            command = validate_command(raw, environment)
            totals["matched"] += 1
            receipt = ledger_table.get_item(
                Key={
                    "PK": command["PK"],
                    "SK": "ACCOUNT_DELETION#SESSION_REVOCATION",
                },
                ConsistentRead=True,
            ).get("Item")
            if _valid_component_receipt(receipt, command, "SESSION_REVOCATION"):
                totals["sessionAlreadyComplete"] += 1
            else:
                ensure_session_revoked(
                    command, user_pool_id=user_pool_id, cognito=cognito,
                    ledger_table=ledger_table, now_epoch=now(),
                )
                totals["revoked"] += 1
            device_result = delete_device_bindings(
                command, device_table=device_table, ledger_table=ledger_table,
                page_size=device_page_size, now_epoch=now(),
            )
            totals["deviceRecordsDeleted"] += device_result["deleted"]
            totals["deviceComponentsCompleted"] += 1 if device_result["complete"] else 0
            recovery_result = delete_device_recovery_control(
                command, recovery_table=recovery_table, ledger_table=ledger_table,
                page_size=recovery_page_size, now_epoch=now(),
            )
            totals["recoveryRecordsDeleted"] += recovery_result["deleted"]
            totals["recoveryRecordsMinimized"] += recovery_result["minimized"]
            totals["recoveryComponentsCompleted"] += 1 if recovery_result["complete"] else 0
            abuse_result = delete_analysis_abuse_control(
                command, abuse_table=abuse_table, ledger_table=ledger_table,
                page_size=analysis_abuse_page_size, now_epoch=now(),
            )
            totals["analysisAbuseRecordsDeleted"] += abuse_result["deleted"]
            totals["analysisAbuseRecordsMinimized"] += abuse_result["minimized"]
            totals["analysisAbuseComponentsCompleted"] += (
                1 if abuse_result["complete"] else 0
            )
        start_key = page.get("LastEvaluatedKey")
        checkpoint_time = now()
        if not start_key:
            completed_pass_at = checkpoint_time
        _save_reconciliation_checkpoint(
            ledger_table, checkpoint_key, start_key, checkpoint_time,
            completed_pass_at=completed_pass_at,
        )
        if not start_key:
            break
        if page_number == max_pages - 1:
            truncated = True
    completed_at = now()
    return {
        **totals,
        "worksetTruncated": truncated,
        "completedFullPass": not truncated and not start_key,
        "completedPassAtEpoch": completed_pass_at,
        "fullPassAgeSeconds": (
            max(0, completed_at - completed_pass_at)
            if completed_pass_at is not None else None
        ),
        "completedAtEpoch": completed_at,
    }


def command_from_stream(record, *, environment):
    if record.get("eventName") not in {"INSERT", "MODIFY"}:
        return None
    raw = (record.get("dynamodb") or {}).get("NewImage")
    if not raw:
        raise ValueError("Account-deletion stream record has no NewImage")
    item = _deserialize(raw)
    if item.get("eventType") != "account.deletion.requested":
        return None
    return validate_command(item, environment)


def validate_command(item, environment):
    account_id = item.get("accountId") if isinstance(item, dict) else None
    occurred = _exact_int(item.get("occurredAtEpoch")) if isinstance(item, dict) else None
    delete_by = _exact_int(item.get("deleteByEpoch")) if isinstance(item, dict) else None
    if (
        not isinstance(item, dict) or set(item) != COMMAND_FIELDS
        or _exact_int(item.get("schemaVersion")) != 1
        or _exact_int(item.get("recordVersion")) != 1
        or item.get("environment") != environment
        or item.get("eventType") != "account.deletion.requested"
        or item.get("status") != "REQUESTED"
        or not isinstance(account_id, str) or not ACCOUNT_ID_PATTERN.fullmatch(account_id)
        or item.get("PK") != f"ACCOUNT#{account_id}" or item.get("SK") != "ACCOUNT_DELETION"
        or not _is_uuid4(item.get("operationId"))
        or occurred is None or delete_by != occurred + 24 * 3600
    ):
        raise ValueError("Invalid authoritative account-deletion command")
    return item


def _component_receipt(
    command, component, completed_at,
    *, retention_days=ACCOUNT_DELETION_RECEIPT_RETENTION_DAYS,
):
    if retention_days != 120:
        raise ValueError("Invalid account-deletion receipt retention")
    return {
        "PK": command["PK"], "SK": f"ACCOUNT_DELETION#{component}",
        "schemaVersion": 1, "recordVersion": 1,
        "environment": command["environment"],
        "eventType": "account.deletion.component.completed",
        "component": component, "status": "COMPLETE",
        "operationId": command["operationId"],
        "occurredAtEpoch": completed_at,
        "requestOccurredAtEpoch": command["occurredAtEpoch"],
        "retainUntilEpoch": completed_at + retention_days * 86400,
    }


def _valid_component_receipt(item, command, component):
    return bool(
        isinstance(item, dict)
        and set(item) == RECEIPT_FIELDS
        and item.get("PK") == command["PK"]
        and item.get("SK") == f"ACCOUNT_DELETION#{component}"
        and item.get("schemaVersion") == 1
        and item.get("recordVersion") == 1
        and item.get("environment") == command["environment"]
        and item.get("eventType") == "account.deletion.component.completed"
        and item.get("component") == component
        and item.get("status") == "COMPLETE"
        and item.get("operationId") == command["operationId"]
        and _exact_int(item.get("requestOccurredAtEpoch")) == command["occurredAtEpoch"]
        and _exact_int(item.get("occurredAtEpoch")) is not None
        and _exact_int(item.get("retainUntilEpoch"))
        == _exact_int(item.get("occurredAtEpoch"))
        + ACCOUNT_DELETION_RECEIPT_RETENTION_DAYS * 86400
    )


def _progress_start_key(progress, command, label):
    if not progress:
        return None
    if (
        progress.get("operationId") != command["operationId"]
        or progress.get("requestOccurredAtEpoch") != command["occurredAtEpoch"]
    ):
        raise ValueError(f"{label} progress belongs to another request")
    start_key = progress.get("lastEvaluatedKey")
    if start_key is not None and not _valid_key(start_key):
        raise ValueError(f"Invalid {label.lower()} continuation")
    return start_key


def _analysis_progress(progress, command):
    if not progress:
        return 0, None
    if (
        progress.get("recordType") != "ACCOUNT_DELETION_ANALYSIS_ABUSE_PROGRESS"
        or progress.get("schemaVersion") != 1
        or progress.get("operationId") != command["operationId"]
        or progress.get("requestOccurredAtEpoch") != command["occurredAtEpoch"]
    ):
        raise ValueError("Analysis-abuse progress belongs to another request")
    family_index = _exact_int(progress.get("familyIndex"))
    if family_index is None or family_index >= len(ANALYSIS_ABUSE_FAMILIES):
        raise ValueError("Invalid analysis-abuse progress family")
    start_key = progress.get("lastEvaluatedKey")
    if start_key is not None and not _valid_key(start_key):
        raise ValueError("Invalid analysis-abuse deletion continuation")
    return family_index, start_key


def _validate_analysis_abuse_key(item, partition):
    if (
        not isinstance(item, dict)
        or item.get("PK") != partition
        or not isinstance(item.get("SK"), str)
        or not REQUEST_ID_PATTERN.fullmatch(item["SK"])
    ):
        raise ValueError("Analysis-abuse query returned an invalid key")


def _minimal_analysis_request(item, *, now_epoch, retention_seconds):
    expires = _exact_int(item.get("expiresAt"))
    ttl = _exact_int(item.get("ttl"))
    payload_hash = item.get("payloadHash")
    if expires is not None and expires <= now_epoch:
        return None
    if (
        item.get("status") not in {
            "PROCESSING", "RETRYABLE", "RESULT_READY", "COMPLETED",
            "COMPLETED_ERASED",
        }
        or not isinstance(payload_hash, str)
        or not PAYLOAD_HASH_PATTERN.fullmatch(payload_hash)
        or expires is None or ttl != expires
        or expires > now_epoch + retention_seconds
    ):
        raise ValueError("Invalid analysis request replay record")
    return {
        "PK": item["PK"], "SK": item["SK"],
        "status": "COMPLETED_ERASED", "payloadHash": payload_hash,
        "expiresAt": expires, "ttl": expires,
    }


def _validate_recovery_control_key(item, account_id):
    if (
        not isinstance(item, dict)
        or item.get("PK") != f"USER#{account_id}"
        or not isinstance(item.get("SK"), str)
        or not item["SK"]
    ):
        raise ValueError("Device-recovery query returned an invalid key")


def _minimal_recovery_receipt(item, retention_days):
    completed = _exact_int(item.get("completedAtEpoch"))
    expires = _exact_int(item.get("expiresAt"))
    operation_id = item.get("operationId")
    if (
        item.get("recordType") != "DEVICE_RECOVERY_RECEIPT"
        or item.get("schemaVersion") != 1
        or not _is_uuid4(operation_id)
        or item.get("operation") != "REPLACE_ACTIVE_BINDING"
        or item.get("result") != "RECOVERED"
        or item.get("status") != "COMPLETE"
        or completed is None or expires is None
        or expires <= completed or expires > completed + retention_days * 86400
    ):
        raise ValueError("Invalid device-recovery receipt")
    return {
        "PK": item["PK"], "SK": item["SK"],
        "recordType": "DEVICE_RECOVERY_RECEIPT", "schemaVersion": 1,
        "operationId": operation_id, "operation": "REPLACE_ACTIVE_BINDING",
        "result": "RECOVERED", "status": "COMPLETE",
        "completedAtEpoch": completed, "expiresAt": expires,
    }


def _minimal_recovery_audit(item, retention_days):
    occurred = _exact_int(item.get("occurredAtEpoch"))
    expires = _exact_int(item.get("expiresAt"))
    operation_id = item.get("operationId")
    if (
        item.get("recordType") != "DEVICE_RECOVERY_AUDIT"
        or item.get("schemaVersion") != 1
        or not _is_uuid4(operation_id)
        or item.get("actorType") != "SELF"
        or item.get("action") != "REPLACE_ACTIVE_BINDING"
        or item.get("result") != "RECOVERED"
        or occurred is None or expires is None
        or expires <= occurred or expires > occurred + retention_days * 86400
    ):
        raise ValueError("Invalid device-recovery audit")
    return {
        "PK": item["PK"], "SK": item["SK"],
        "recordType": "DEVICE_RECOVERY_AUDIT", "schemaVersion": 1,
        "operationId": operation_id, "actorType": "SELF",
        "action": "REPLACE_ACTIVE_BINDING", "result": "RECOVERED",
        "occurredAtEpoch": occurred, "expiresAt": expires,
    }


def _validate_rate_state(item, retention_seconds):
    try:
        window = int(item["SK"].removeprefix("RATE#"))
    except (TypeError, ValueError) as err:
        raise ValueError("Invalid device-recovery rate state") from err
    expires = _exact_int(item.get("expiresAt"))
    count = _exact_int(item.get("requestCount"))
    if (
        window < 0 or expires is None or count is None
        or expires <= window or expires > window + retention_seconds * 2
    ):
        raise ValueError("Invalid device-recovery rate state")


def _save_reconciliation_checkpoint(
    table, key, last_evaluated_key, now_epoch, *, completed_pass_at,
):
    item = {
        **key,
        "recordType": "ACCOUNT_DELETION_SESSION_REVOCATION_RECONCILIATION",
        "schemaVersion": 1,
        "updatedAtEpoch": now_epoch,
    }
    if last_evaluated_key:
        if not _valid_key(last_evaluated_key):
            raise ValueError("Invalid deletion-ledger scan continuation")
        item["lastEvaluatedKey"] = dict(last_evaluated_key)
    if completed_pass_at is not None:
        item["completedPassAtEpoch"] = completed_pass_at
    table.put_item(Item=item)


def _valid_key(value):
    return (
        isinstance(value, dict) and set(value) == {"PK", "SK"}
        and isinstance(value.get("PK"), str) and bool(value["PK"])
        and isinstance(value.get("SK"), str) and bool(value["SK"])
    )


def _is_uuid4(value):
    from uuid import UUID
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value


def _exact_int(value):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    integer = int(value)
    return integer if integer == value and integer >= 0 else None


def _serialize(item):
    result = {}
    for key, value in item.items():
        if isinstance(value, bool):
            result[key] = {"BOOL": value}
        elif isinstance(value, int):
            result[key] = {"N": str(value)}
        elif isinstance(value, str):
            result[key] = {"S": value}
        else:
            raise TypeError(type(value).__name__)
    return result


def _deserialize(item):
    result = {}
    for key, value in item.items():
        kind, raw = next(iter(value.items()))
        if kind == "S":
            result[key] = raw
        elif kind == "N":
            result[key] = int(raw)
        elif kind == "BOOL":
            result[key] = raw
        else:
            raise ValueError("Unsupported account-deletion stream attribute")
    return result


def _error_code(err):
    return getattr(err, "response", {}).get("Error", {}).get("Code")
