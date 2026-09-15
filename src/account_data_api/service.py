from decimal import Decimal
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
}
ACCOUNT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,199}$")


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


def reconcile_session_revocations(
    *, environment, ledger_table, device_table, user_pool_id, cognito,
    scan_limit, max_pages, device_page_size=100,
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


def _component_receipt(command, component, completed_at):
    return {
        "PK": command["PK"], "SK": f"ACCOUNT_DELETION#{component}",
        "schemaVersion": 1, "recordVersion": 1,
        "environment": command["environment"],
        "eventType": "account.deletion.component.completed",
        "component": component, "status": "COMPLETE",
        "operationId": command["operationId"],
        "occurredAtEpoch": completed_at,
        "requestOccurredAtEpoch": command["occurredAtEpoch"],
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
    )


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
