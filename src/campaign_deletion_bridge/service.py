from __future__ import annotations

import base64
from decimal import Decimal
import time
from uuid import UUID

TOKEN_DOMAIN = b"campaign-contributor:v1\0"
PERIOD_SECONDS = 14 * 86400
RECOVERY_SECONDS = 7 * 86400
ACCOUNT_DELETION_RECEIPT_RETENTION_DAYS = 120


def parse_deletion_record(record, *, environment, schema_version):
    if record.get("eventName") not in {"INSERT", "MODIFY"}: return None
    raw = record.get("dynamodb", {}).get("NewImage")
    if not raw: raise ValueError("Deletion stream record has no NewImage")
    item = deserialize(raw)
    if item.get("eventType") in {"account.deletion.component.completed", "account.deletion.completed"}:
        return None
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
        required = common | {"PK", "SK", "operationId", "deleteByEpoch"}
        if set(item) != required or item["status"] != "REQUESTED" \
                or item["PK"] != f"ACCOUNT#{item['accountId']}" or item["SK"] != "ACCOUNT_DELETION" \
                or not _is_uuid4(item["operationId"]) \
                or isinstance(item["deleteByEpoch"], bool) or not isinstance(item["deleteByEpoch"], int) \
                or item["deleteByEpoch"] != item["occurredAtEpoch"] + 24 * 3600:
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


def delete_account_contributions(command, *, table_name, retention_days, dynamodb, kms,
                                 now_epoch=None, max_steps=10, remaining_ms=None, deletion_ledger_table_name=None):
    from progress import CoverageUnavailable, sweep, CommandGuardedClient
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    # Request-time periods are stable across retries and key rollover. Missing or
    # retired keys cannot be skipped as evidence that old contributions are gone.
    parse_deletion_record({"eventName":"INSERT", "dynamodb":{"NewImage":{
        k:serialize_value(v) for k,v in command.items()}}},
        environment=command.get("environment"), schema_version=1)
    if not 1 <= retention_days <= 21 or now_epoch < command["occurredAtEpoch"]:
        raise CoverageUnavailable()
    totals = {"deleted":0, "recomputedCandidates":0, "complete":False,
              "coverage":"UNVERIFIED", "observedPassEnded":False}
    if not deletion_ledger_table_name:
        raise CoverageUnavailable()
    stored = dynamodb.get_item(TableName=deletion_ledger_table_name,
        Key={"PK":{"S":command["PK"]},"SK":{"S":command["SK"]}},ConsistentRead=True).get("Item")
    from progress import plain
    stored = plain(stored) if stored else None
    if stored and stored.get("status") == "COMPLETE":
        completed = stored.get("completedAtEpoch")
        if type(completed) not in (int, Decimal) or completed < command["occurredAtEpoch"] or completed != int(completed):
            raise CoverageUnavailable()
        expected = command | {"status":"COMPLETE", "completedAtEpoch":completed}
        if command["eventType"] == "account.deletion.requested":
            expected |= {"eventType":"account.deletion.completed", "retainUntilEpoch":completed+120*86400}
        if stored != expected:
            raise CoverageUnavailable()
        # Exact terminal replay cannot restart cleanup; no polling promise follows.
        return totals | {"alreadyCompleted":True}
    if stored != command:
        raise CoverageUnavailable()
    dynamodb = CommandGuardedClient(dynamodb,deletion_ledger_table_name,command)
    for period_id in active_periods(command["occurredAtEpoch"]):
        if remaining_ms is not None and remaining_ms() < 3000:
            break
        key_record = dynamodb.get_item(TableName=table_name,
            Key={"PK":{"S":f"PERIOD#{period_id}"},"SK":{"S":"HMAC_KEY"}}, ConsistentRead=True).get("Item")
        if not key_record or key_record.get("status") != {"S":"ENABLED"}:
            raise CoverageUnavailable()
        mac = kms.generate_mac(KeyId=key_record["keyArn"]["S"],
            Message=TOKEN_DOMAIN + command["accountId"].encode(),MacAlgorithm="HMAC_SHA_256")["Mac"]
        if not isinstance(mac, bytes) or len(mac) != 32:
            raise CoverageUnavailable()
        token = base64.urlsafe_b64encode(mac).decode().rstrip("=")
        partition = f"CONTRIB#{period_id}#{token}"
        # Retry does not extend the existing transient deadline or reset progress.
        dynamodb.update_item(TableName=table_name,Key={"PK":{"S":partition},"SK":{"S":"TOMBSTONE"}},
            UpdateExpression="SET expiresAt = if_not_exists(expiresAt, :expiry), createdAtEpoch = if_not_exists(createdAtEpoch, :now), GSI3PK = if_not_exists(GSI3PK, :partition), GSI3SK = if_not_exists(GSI3SK, :expiry)",
            ExpressionAttributeValues={":expiry":{"N":str(now_epoch+retention_days*86400)},
                ":now":{"N":str(now_epoch)},":partition":{"S":f"EXPIRY#{command['environment']}"}})
        result = sweep(dynamodb,table_name,partition,command["operationId"],now_epoch,
                       max_steps=max_steps,remaining_ms=remaining_ms)
        for name in ("deleted","recomputedCandidates"):
            totals[name] += result[name]
        totals["observedPassEnded"] = totals["observedPassEnded"] or result["observedPassEnded"]
    return totals


def complete_campaign_withdrawal(*args, **kwargs):
    # No approved strong locator/migration proof; no caller can bypass the gate.
    from progress import CoverageUnavailable
    raise CoverageUnavailable()


def complete_account_deletion_component(*args, **kwargs):
    from progress import CoverageUnavailable
    raise CoverageUnavailable()


def active_periods(now_epoch):
    current = now_epoch // PERIOD_SECONDS
    periods = [current]
    previous_end = current * PERIOD_SECONDS
    if now_epoch - previous_end < RECOVERY_SECONDS and current > 0: periods.append(current - 1)
    return periods



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
