from decimal import Decimal
import hashlib
import re


REQUEST_FIELDS = {
    "PK", "SK", "schemaVersion", "recordVersion", "environment",
    "eventType", "accountId", "status", "occurredAtEpoch",
}
ACCOUNT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,199}$")


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
    account_id = item.get("accountId")
    occurred_at = _exact_nonnegative_int(item.get("occurredAtEpoch"))
    if (
        set(item) != REQUEST_FIELDS
        or item.get("schemaVersion") != schema_version
        or item.get("recordVersion") != 1
        or item.get("environment") != environment
        or item.get("status") != "REQUESTED"
        or not isinstance(account_id, str) or not ACCOUNT_ID_PATTERN.fullmatch(account_id)
        or occurred_at is None
        or item.get("PK") != f"ACCOUNT#{account_id}"
        or item.get("SK") != "ACCOUNT_DELETION"
    ):
        raise ValueError("Invalid authoritative account-deletion command")
    return item


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
        created = False
    return {"started": False, "alreadyPending": False, "completed": True, "receiptCreated": created}


def _operation_id(command):
    material = f"{command['environment']}\0{command['accountId']}\0{command['occurredAtEpoch']}"
    return "account-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


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
