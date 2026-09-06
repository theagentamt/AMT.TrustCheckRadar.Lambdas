from __future__ import annotations

import base64
import time

TOKEN_DOMAIN = b"campaign-contributor:v1\0"
PERIOD_SECONDS = 14 * 86400
RECOVERY_SECONDS = 7 * 86400


def parse_deletion_record(record, *, environment, schema_version):
    if record.get("eventName") not in {"INSERT", "MODIFY"}: return None
    raw = record.get("dynamodb", {}).get("NewImage")
    if not raw: raise ValueError("Deletion stream record has no NewImage")
    item = deserialize(raw)
    required = {"schemaVersion", "recordVersion", "environment", "eventType", "accountId", "status", "occurredAtEpoch"}
    if set(item) != required or item["schemaVersion"] != schema_version or item["recordVersion"] != 1 \
            or item["environment"] != environment or item["eventType"] not in {"campaign.consent.withdrawn", "account.deletion.requested"} \
            or item["status"] != "REQUESTED":
        raise ValueError("Invalid deletion command")
    return item


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
            if candidate_id: recomputed.add(candidate_id)
        for candidate_id in recomputed:
            _recompute(dynamodb, table_name, candidate_id)
    return {"deleted": deleted, "recomputedCandidates": len(recomputed)}


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
