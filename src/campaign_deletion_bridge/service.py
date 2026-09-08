from __future__ import annotations

import base64
from datetime import UTC, datetime
import time
from uuid import UUID

TOKEN_DOMAIN = b"campaign-contributor:v1\0"
PERIOD_SECONDS = 14 * 86400
RECOVERY_SECONDS = 7 * 86400


def parse_deletion_record(record, *, environment, schema_version):
    if record.get("eventName") not in {"INSERT", "MODIFY"}: return None
    raw = record.get("dynamodb", {}).get("NewImage")
    if not raw: raise ValueError("Deletion stream record has no NewImage")
    item = deserialize(raw)
    if item.get("eventType") == "campaign.consent.withdrawn" and item.get("status") == "COMPLETE":
        return None
    common = {"schemaVersion", "recordVersion", "environment", "eventType", "accountId", "status", "occurredAtEpoch"}
    if isinstance(item.get("schemaVersion"), bool) or not isinstance(item.get("schemaVersion"), int) \
            or item.get("schemaVersion") != schema_version \
            or isinstance(item.get("recordVersion"), bool) or not isinstance(item.get("recordVersion"), int) \
            or item.get("recordVersion") != 1 \
            or item.get("environment") != environment or isinstance(item.get("occurredAtEpoch"), bool) \
            or not isinstance(item.get("occurredAtEpoch"), int) or item["occurredAtEpoch"] < 0 \
            or not isinstance(item.get("accountId"), str) or not item["accountId"]:
        raise ValueError("Invalid deletion command")
    if item.get("eventType") == "campaign.consent.withdrawn":
        required = common | {"PK", "SK", "consentEpochId", "deleteByEpoch", "operationId"}
        if set(item) != required or item["status"] != "PENDING" \
                or item["PK"] != f"ACCOUNT#{item['accountId']}" \
                or item["SK"] != f"CAMPAIGN_WITHDRAWAL#{item['operationId']}" \
                or not _is_uuid4(item["consentEpochId"]) or not _is_uuid4(item["operationId"]) \
                or isinstance(item["deleteByEpoch"], bool) or not isinstance(item["deleteByEpoch"], int) \
                or item["deleteByEpoch"] != item["occurredAtEpoch"] + 24 * 3600:
            raise ValueError("Invalid deletion command")
    elif item.get("eventType") == "account.deletion.requested":
        if frozenset(item) not in {frozenset(common), frozenset(common | {"PK", "SK"})} or item["status"] != "REQUESTED":
            raise ValueError("Invalid deletion command")
    else:
        raise ValueError("Invalid deletion command")
    return item


def _is_uuid4(value):
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return parsed.version == 4 and str(parsed) == value


def delete_account_contributions(command, *, table_name, retention_days, dynamodb, kms, now_epoch=None):
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    deleted, recomputed = 0, set()
    for period_id in active_periods(now_epoch):
        key_record = dynamodb.get_item(TableName=table_name,
            Key={"PK": {"S": f"PERIOD#{period_id}"}, "SK": {"S": "HMAC_KEY"}}, ConsistentRead=True).get("Item")
        if not key_record or key_record.get("status") != {"S": "ENABLED"}: continue
        mac = kms.generate_mac(KeyId=key_record["keyArn"]["S"],
            Message=TOKEN_DOMAIN + command["accountId"].encode(), MacAlgorithm="HMAC_SHA_256")["Mac"]
        token = base64.urlsafe_b64encode(mac).decode().rstrip("=")
        tombstone_key = {"PK": {"S": f"CONTRIB#{period_id}#{token}"}, "SK": {"S": "TOMBSTONE"}}
        dynamodb.update_item(TableName=table_name, Key=tombstone_key,
            UpdateExpression="SET expiresAt = :expiry, createdAtEpoch = :now",
            ExpressionAttributeValues={":expiry": {"N": str(now_epoch + retention_days * 86400)},
                                       ":now": {"N": str(now_epoch)}})
        response = dynamodb.query(TableName=table_name, IndexName="ContributorPeriodIndex",
            KeyConditionExpression="GSI1PK = :token",
            ExpressionAttributeValues={":token": {"S": f"CONTRIB#{period_id}#{token}"}})
        keys = [(item["PK"]["S"], item["SK"]["S"]) for item in response.get("Items", [])]
        for pk, sk in keys:
            candidate_id = pk.removeprefix("CANDIDATE#") if pk.startswith("CANDIDATE#") else None
            dynamodb.delete_item(TableName=table_name, Key={"PK": {"S": pk}, "SK": {"S": sk}})
            deleted += 1
            if pk.startswith("EVENT#") and sk == "FEATURE":
                for sibling_sk in ("DEDUPE", "CLUSTERED"):
                    dynamodb.delete_item(
                        TableName=table_name,
                        Key={"PK": {"S": pk}, "SK": {"S": sibling_sk}},
                    )
                    deleted += 1
            if candidate_id: recomputed.add(candidate_id)
    for candidate_id in recomputed:
        _recompute(dynamodb, table_name, candidate_id)
    return {"deleted": deleted, "recomputedCandidates": len(recomputed)}


def complete_campaign_withdrawal(
    command,
    *,
    users_table_name,
    deletion_ledger_table_name,
    participation_item_sk,
    audit_days,
    dynamodb,
    now_epoch=None,
):
    if command.get("eventType") != "campaign.consent.withdrawn" or command.get("status") != "PENDING":
        return False
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    completed_at = datetime.fromtimestamp(now_epoch, UTC).isoformat().replace("+00:00", "Z")
    account_id = command["accountId"]
    epoch_id = command["consentEpochId"]
    operation_id = command["operationId"]
    receipt = {
        "PK": {"S": f"USER#{account_id}"},
        "SK": {"S": f"CAMPAIGN_CONSENT#{epoch_id}#{now_epoch}#{operation_id}#COMPLETE"},
        "schemaVersion": {"N": "1"},
        "recordVersion": {"N": "1"},
        "eventType": {"S": "campaign.participation.withdrawal_completed"},
        "occurredAt": {"S": completed_at},
        "consentEpochId": {"S": epoch_id},
        "operationId": {"S": operation_id},
        "resultingState": {"S": "withdrawn"},
        "expiresAt": {"N": str(now_epoch + audit_days * 86400)},
    }
    transaction = [
            {
                "Update": {
                    "TableName": users_table_name,
                    "Key": {"PK": {"S": f"USER#{account_id}"}, "SK": {"S": participation_item_sk}},
                    "UpdateExpression": "SET #state = :withdrawn, withdrawalCompletedAt = :completed_at, updatedAt = :completed_at ADD stateVersion :one",
                    "ConditionExpression": "#state = :pending AND consentEpochId = :epoch AND lastOperationId = :operation_id",
                    "ExpressionAttributeNames": {"#state": "state"},
                    "ExpressionAttributeValues": {
                        ":withdrawn": {"S": "withdrawn"},
                        ":pending": {"S": "withdrawal_pending"},
                        ":epoch": {"S": epoch_id},
                        ":operation_id": {"S": operation_id},
                        ":completed_at": {"S": completed_at},
                        ":one": {"N": "1"},
                    },
                }
            },
            {
                "Put": {
                    "TableName": users_table_name,
                    "Item": receipt,
                    "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                }
            },
            {
                "Update": {
                    "TableName": deletion_ledger_table_name,
                    "Key": {"PK": {"S": command["PK"]}, "SK": {"S": command["SK"]}},
                    "UpdateExpression": "SET #status = :complete, completedAtEpoch = :completed_at",
                    "ConditionExpression": "#status = :pending AND consentEpochId = :epoch AND operationId = :operation_id",
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {
                        ":complete": {"S": "COMPLETE"},
                        ":pending": {"S": "PENDING"},
                        ":completed_at": {"N": str(now_epoch)},
                        ":epoch": {"S": epoch_id},
                        ":operation_id": {"S": operation_id},
                    },
                }
            },
        ]
    try:
        dynamodb.transact_write_items(TransactItems=transaction)
    except Exception as err:
        if getattr(err, "response", {}).get("Error", {}).get("Code") != "TransactionCanceledException":
            raise
        stored = dynamodb.get_item(
            TableName=deletion_ledger_table_name,
            Key={"PK": {"S": command["PK"]}, "SK": {"S": command["SK"]}},
            ConsistentRead=True,
        ).get("Item")
        if stored:
            completed = deserialize(stored)
            if completed.get("status") == "COMPLETE" \
                    and completed.get("consentEpochId") == epoch_id \
                    and completed.get("operationId") == operation_id:
                return False
        raise
    return True


def active_periods(now_epoch):
    current = now_epoch // PERIOD_SECONDS
    periods = [current]
    previous_end = current * PERIOD_SECONDS
    if now_epoch - previous_end < RECOVERY_SECONDS and current > 0: periods.append(current - 1)
    return periods


def _recompute(dynamodb, table, candidate_id):
    response = dynamodb.query(TableName=table,
        KeyConditionExpression="PK = :candidate AND begins_with(SK, :contribution)",
        ExpressionAttributeValues={":candidate": {"S": f"CANDIDATE#{candidate_id}"},
                                   ":contribution": {"S": "CONTRIB#"}}, ConsistentRead=True)
    contributions = [deserialize(item) for item in response.get("Items", [])]
    if not contributions:
        dynamodb.delete_item(TableName=table,
            Key={"PK": {"S": f"CANDIDATE#{candidate_id}"}, "SK": {"S": "SUMMARY"}})
        return
    vectors = [item["vector"] for item in contributions if item.get("vectorApplied")]
    centroid = [round(sum(v[index] for v in vectors) / len(vectors), 7) for index in range(len(vectors[0]))]
    dynamodb.update_item(TableName=table,
        Key={"PK": {"S": f"CANDIDATE#{candidate_id}"}, "SK": {"S": "SUMMARY"}},
        UpdateExpression="SET centroid = :centroid, contributorCount = :contributors, submissionCount = :submissions, version = version + :one",
        ExpressionAttributeValues={":centroid": serialize_value(centroid),
            ":contributors": {"N": str(len(contributions))},
            ":submissions": {"N": str(sum(min(3, item["submissionCount"]) for item in contributions))},
            ":one": {"N": "1"}})


def deserialize(item):
    def one(v):
        kind, raw = next(iter(v.items()))
        if kind == "S": return raw
        if kind == "N": return float(raw) if "." in raw else int(raw)
        if kind == "BOOL": return raw
        if kind == "L": return [one(x) for x in raw]
    return {k: one(v) for k, v in item.items()}


def serialize_value(v):
    if isinstance(v, str): return {"S": v}
    if isinstance(v, bool): return {"BOOL": v}
    if isinstance(v, (int, float)): return {"N": str(v)}
    if isinstance(v, list): return {"L": [serialize_value(x) for x in v]}
    raise TypeError(type(v).__name__)
