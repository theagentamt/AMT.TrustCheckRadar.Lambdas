from decimal import Decimal
import hashlib
import re
import time


REQUEST_FIELDS = {
    "PK", "SK", "schemaVersion", "recordVersion", "environment",
    "eventType", "accountId", "operationId", "status", "occurredAtEpoch",
    "deleteByEpoch",
}
ACCOUNT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,199}$")
ACCOUNT_DELETION_RECEIPT_RETENTION_DAYS = 120


def parse_account_deletion_record(record, *, environment, schema_version):
    if record.get("eventName") not in {"INSERT", "MODIFY"}:
        return None
    raw = (record.get("dynamodb") or {}).get("NewImage")
    if not raw:
        raise ValueError("Deletion stream record has no NewImage")
    item = _deserialize(raw)
    event_type = item.get("eventType")
    if event_type != "account.deletion.requested":
        return None
    return validate_account_deletion_command(
        item, environment=environment, schema_version=schema_version
    )


def validate_account_deletion_command(item, *, environment, schema_version):
    account_id = item.get("accountId")
    occurred_at = _exact_nonnegative_int(item.get("occurredAtEpoch"))
    delete_by = _exact_nonnegative_int(item.get("deleteByEpoch"))
    if (
        set(item) != REQUEST_FIELDS
        or item.get("schemaVersion") != schema_version
        or item.get("recordVersion") != 1
        or item.get("environment") != environment
        or item.get("status") != "REQUESTED"
        or not isinstance(account_id, str) or not ACCOUNT_ID_PATTERN.fullmatch(account_id)
        or occurred_at is None
        or delete_by != occurred_at + 24 * 3600
        or not _is_uuid4(item.get("operationId"))
        or item.get("PK") != f"ACCOUNT#{account_id}"
        or item.get("SK") != "ACCOUNT_DELETION"
    ):
        raise ValueError("Invalid authoritative account-deletion command")
    return item


def reconcile_account_deletions(
    *, environment, schema_version, control_table, control_table_name,
    deletion_ledger_table, deletion_ledger_table_name, dynamodb_client,
    erasure_sla_hours, scan_limit, max_pages, now=lambda: int(time.time()),
):
    checkpoint_key = {
        "PK": f"LIFECYCLE#{environment}",
        "SK": "ACCOUNT_DELETION_RECONCILIATION",
    }
    checkpoint = control_table.get_item(
        Key=checkpoint_key, ConsistentRead=True
    ).get("Item") or {}
    start_key = checkpoint.get("lastEvaluatedKey")
    completed_pass_at = _exact_nonnegative_int(checkpoint.get("completedPassAtEpoch"))
    if start_key is not None and not _valid_ledger_key(start_key):
        raise ValueError("Invalid account-deletion reconciliation checkpoint")
    totals = {"scanned": 0, "matched": 0, "started": 0, "alreadyPending": 0, "completed": 0}
    workset_truncated = False
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
        page = deletion_ledger_table.scan(**kwargs)
        totals["scanned"] += int(page.get("ScannedCount", 0))
        for raw in page.get("Items") or []:
            command = validate_account_deletion_command(
                raw, environment=environment, schema_version=schema_version
            )
            totals["matched"] += 1
            result = start_history_deletion(
                command,
                control_table=control_table,
                control_table_name=control_table_name,
                deletion_ledger_table=deletion_ledger_table,
                deletion_ledger_table_name=deletion_ledger_table_name,
                dynamodb_client=dynamodb_client,
                schema_version=schema_version,
                erasure_sla_hours=erasure_sla_hours,
            )
            for key in ("started", "alreadyPending", "completed"):
                totals[key] += 1 if result.get(key) else 0
        start_key = page.get("LastEvaluatedKey")
        checkpoint_time = now()
        if not start_key:
            completed_pass_at = checkpoint_time
        _save_reconciliation_checkpoint(
            control_table, checkpoint_key, start_key, checkpoint_time,
            schema_version, completed_pass_at=completed_pass_at,
        )
        if not start_key:
            break
        if page_number == max_pages - 1:
            workset_truncated = True
    completed_at = now()
    return {
        **totals,
        "worksetTruncated": workset_truncated,
        "completedFullPass": not workset_truncated and not start_key,
        "completedPassAtEpoch": completed_pass_at,
        "fullPassAgeSeconds": (
            max(0, completed_at - completed_pass_at)
            if completed_pass_at is not None else None
        ),
        "completedAtEpoch": completed_at,
    }


def start_history_deletion(
    command, *, control_table, control_table_name, deletion_ledger_table,
    deletion_ledger_table_name, dynamodb_client, schema_version, erasure_sla_hours,
):
    account_id = command["accountId"]
    state_key = {"PK": f"USER#{account_id}", "SK": "STATE"}
    state = control_table.get_item(Key=state_key, ConsistentRead=True).get("Item")
    if not state:
        return _complete_without_history(
            command, deletion_ledger_table, schema_version=schema_version
        )
    maximum_generation = _exact_nonnegative_int(state.get("historyGeneration"))
    recognition_generation = _exact_nonnegative_int(state.get("recognitionGeneration"))
    accepted_sequence = _exact_nonnegative_int(state.get("acceptedSequence"))
    status = state.get("accountStatus")
    if (
        maximum_generation is None or recognition_generation is None
        or accepted_sequence is None
        or status not in {"ACTIVE", "HISTORY_DELETING", "HISTORY_DELETED", "DELETING", "DELETED"}
    ):
        raise ValueError("Invalid History account state")
    if status == "DELETED":
        return _complete_without_history(
            command, deletion_ledger_table, schema_version=schema_version
        )
    operation_id = _operation_id(command)
    job_key = {"PK": state_key["PK"], "SK": f"ERASURE#{operation_id}"}
    if status == "DELETING":
        existing = control_table.get_item(Key=job_key, ConsistentRead=True).get("Item")
        if existing and existing.get("reason") == "ACCOUNT_DELETION":
            return {"started": False, "alreadyPending": True, "completed": False}
        raise ValueError("History is deleting under a different operation")
    now = command["occurredAtEpoch"]
    shard = int(hashlib.sha256(operation_id.encode()).hexdigest()[:2], 16) % 16
    job = {
        **job_key,
        "recordType": "ERASURE", "schemaVersion": schema_version,
        "operationId": operation_id, "reason": "ACCOUNT_DELETION",
        "status": "PENDING", "stage": "HISTORY", "historyGeneration": 0,
        "maxHistoryGeneration": maximum_generation,
        "createdAtEpoch": now,
        "deleteByEpoch": now + erasure_sla_hours * 3600,
        "lifecycleBucket": f"PENDING#{shard:02d}", "lifecycleAt": now,
        "deletionLedgerPK": command["PK"], "deletionLedgerSK": command["SK"],
        "deletionRequestedAtEpoch": now,
        "deletionOperationId": command["operationId"],
    }
    transaction = [
        {"Update": {
            "TableName": control_table_name,
            "Key": _serialize(state_key),
            "UpdateExpression": "SET accountStatus = :deleting, updatedAtEpoch = :now",
            "ConditionExpression": "accountStatus = :previous AND historyGeneration = :generation",
            "ExpressionAttributeValues": _serialize({
                ":deleting": "DELETING", ":previous": status,
                ":generation": maximum_generation, ":now": now,
            }),
        }},
        {"Put": {
            "TableName": control_table_name, "Item": _serialize(job),
            "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
        }},
        {"ConditionCheck": {
            "TableName": deletion_ledger_table_name,
            "Key": _serialize({"PK": command["PK"], "SK": command["SK"]}),
            "ConditionExpression": "#status = :requested AND eventType = :event_type AND occurredAtEpoch = :occurred",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": _serialize({
                ":requested": "REQUESTED", ":event_type": "account.deletion.requested",
                ":occurred": now,
            }),
        }},
    ]
    try:
        dynamodb_client.transact_write_items(TransactItems=transaction)
    except Exception as err:
        if _error_code(err) != "TransactionCanceledException":
            raise
        existing = control_table.get_item(Key=job_key, ConsistentRead=True).get("Item")
        if existing and existing.get("reason") == "ACCOUNT_DELETION":
            return {"started": False, "alreadyPending": True, "completed": False}
        raise
    return {"started": True, "alreadyPending": False, "completed": False}


def _complete_without_history(command, deletion_ledger_table, *, schema_version):
    receipt = {
        "PK": command["PK"], "SK": "ACCOUNT_DELETION#HISTORY",
        "schemaVersion": schema_version, "recordVersion": 1,
        "environment": command["environment"],
        "eventType": "account.deletion.component.completed",
        "component": "HISTORY", "status": "COMPLETE",
        "occurredAtEpoch": command["occurredAtEpoch"],
        "requestOccurredAtEpoch": command["occurredAtEpoch"],
        "operationId": command["operationId"],
        "retainUntilEpoch": (
            command["occurredAtEpoch"]
            + ACCOUNT_DELETION_RECEIPT_RETENTION_DAYS * 86400
        ),
    }
    try:
        deletion_ledger_table.put_item(
            Item=receipt,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
        created = True
    except Exception as err:
        if _error_code(err) != "ConditionalCheckFailedException":
            raise
        existing = deletion_ledger_table.get_item(
            Key={"PK": receipt["PK"], "SK": receipt["SK"]},
            ConsistentRead=True,
        ).get("Item")
        if not existing or set(existing) != set(receipt) or any(
            existing.get(field) != value for field, value in receipt.items()
        ):
            raise
        created = False
    return {"started": False, "alreadyPending": False, "completed": True, "receiptCreated": created}


def _operation_id(command):
    material = f"{command['environment']}\0{command['accountId']}\0{command['occurredAtEpoch']}"
    return "account-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _is_uuid4(value):
    from uuid import UUID
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value


def _save_reconciliation_checkpoint(
    table, key, last_evaluated_key, now_epoch, schema_version, *, completed_pass_at,
):
    item = {
        **key, "recordType": "ACCOUNT_DELETION_RECONCILIATION",
        "schemaVersion": schema_version, "updatedAtEpoch": now_epoch,
    }
    if last_evaluated_key:
        if not _valid_ledger_key(last_evaluated_key):
            raise ValueError("Invalid deletion-ledger scan continuation")
        item["lastEvaluatedKey"] = dict(last_evaluated_key)
    if completed_pass_at is not None:
        item["completedPassAtEpoch"] = completed_pass_at
    table.put_item(Item=item)


def _valid_ledger_key(value):
    return (
        isinstance(value, dict) and set(value) == {"PK", "SK"}
        and isinstance(value.get("PK"), str) and isinstance(value.get("SK"), str)
        and bool(value["PK"]) and bool(value["SK"])
    )


def _exact_nonnegative_int(value):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    integer = int(value)
    return integer if integer == value and integer >= 0 else None


def _deserialize(item):
    def one(value):
        kind, raw = next(iter(value.items()))
        if kind == "S":
            return raw
        if kind == "N":
            return Decimal(raw) if "." in raw else int(raw)
        if kind == "BOOL":
            return raw
        raise ValueError("Unsupported deletion-ledger attribute")
    return {key: one(value) for key, value in item.items()}


def _serialize(item):
    result = {}
    for key, value in item.items():
        if isinstance(value, bool):
            result[key] = {"BOOL": value}
        elif isinstance(value, (int, Decimal)):
            result[key] = {"N": str(value)}
        elif isinstance(value, str):
            result[key] = {"S": value}
        else:
            raise TypeError(f"Unsupported DynamoDB value type: {type(value).__name__}")
    return result


def _error_code(err):
    return getattr(err, "response", {}).get("Error", {}).get("Code")
