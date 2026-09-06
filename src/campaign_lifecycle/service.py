from __future__ import annotations

from datetime import UTC, datetime
import time

PERIOD_SECONDS = 14 * 86400
RECOVERY_SECONDS = 7 * 86400
TAXONOMY_BUCKETS = ("advance_fee", "credential_theft", "impersonation", "investment", "other", "unknown")


def manage_keys(*, environment, project_name, table_name, dynamodb, kms, now_epoch=None):
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    current = now_epoch // PERIOD_SECONDS
    results = {"created": 0, "retired": 0, "unchanged": 0}
    for period_id in (current, current - 1, current - 2):
        if period_id < 0: continue
        key = {"PK": {"S": f"PERIOD#{period_id}"}, "SK": {"S": "HMAC_KEY"}}
        record = dynamodb.get_item(TableName=table_name, Key=key, ConsistentRead=True).get("Item")
        period_end = (period_id + 1) * PERIOD_SECONDS
        if period_id == current and not record:
            response = kms.create_key(KeySpec="HMAC_256", KeyUsage="GENERATE_VERIFY_MAC",
                Description=f"Campaign contributor token period {period_id}",
                Tags=[{"TagKey": "Project", "TagValue": project_name},
                      {"TagKey": "Environment", "TagValue": environment},
                      {"TagKey": "Purpose", "TagValue": "campaign-contributor-token"},
                      {"TagKey": "PeriodId", "TagValue": str(period_id)}])
            arn = response["KeyMetadata"]["Arn"]
            dynamodb.put_item(TableName=table_name, Item=serialize({"PK": f"PERIOD#{period_id}",
                "SK": "HMAC_KEY", "keyArn": arn, "status": "ENABLED", "periodId": period_id,
                "retireAfterEpoch": period_end + RECOVERY_SECONDS}),
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)")
            results["created"] += 1
        elif record and record.get("status") == {"S": "ENABLED"} and now_epoch >= period_end + RECOVERY_SECONDS:
            arn = record["keyArn"]["S"]
            kms.disable_key(KeyId=arn)
            kms.schedule_key_deletion(KeyId=arn, PendingWindowInDays=7)
            dynamodb.update_item(TableName=table_name, Key=key,
                UpdateExpression="SET #status = :retired, retiredAtEpoch = :now",
                ConditionExpression="#status = :enabled",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":retired": {"S": "RETIRED"}, ":enabled": {"S": "ENABLED"},
                                           ":now": {"N": str(now_epoch)}})
            results["retired"] += 1
        else:
            results["unchanged"] += 1
    return results


def finalize_periods(*, environment, schema_version, pipeline_table, intelligence_table,
                     minimum_contributors, aggregate_retention_days, dynamodb, now_epoch=None):
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    current = now_epoch // PERIOD_SECONDS
    results = {"finalized": 0, "suppressed": 0}
    for period_id in (current - 1, current - 2):
        if period_id < 0 or now_epoch < (period_id + 1) * PERIOD_SECONDS + RECOVERY_SECONDS:
            continue
        for bucket in TAXONOMY_BUCKETS:
            response = dynamodb.query(TableName=pipeline_table, IndexName="CandidateBucketIndex",
                KeyConditionExpression="GSI2PK = :bucket",
                ExpressionAttributeValues={":bucket": {"S": f"PERIOD#{period_id}#BUCKET#{bucket}"}}, Limit=500)
            for projected in response.get("Items", []):
                raw = dynamodb.get_item(TableName=pipeline_table,
                    Key={"PK": projected["PK"], "SK": projected["SK"]}, ConsistentRead=True).get("Item")
                if not raw: continue
                candidate = deserialize(raw)
                contributions = _contributions(dynamodb, pipeline_table, candidate["candidateId"])
                contributor_count = len(contributions)
                submission_count = sum(min(3, item["submissionCount"]) for item in contributions)
                if contributor_count >= minimum_contributors:
                    week = datetime.fromtimestamp((period_id + 1) * PERIOD_SECONDS, UTC).strftime("%G-W%V")
                    aggregate = {"PK": f"CAMPAIGN#{candidate['candidateId']}", "SK": "AGGREGATE",
                        "campaignId": candidate["candidateId"], "schemaVersion": schema_version,
                        "taxonomyVersion": 1, "categoryId": candidate["taxonomyBucket"],
                        "periodWeek": week, "state": "PENDING_REVIEW", "contributorCount": contributor_count,
                        "submissionCount": submission_count, "languageIds": candidate.get("languageIds", ["en", "es"]),
                        "contributorCountBand": count_band(contributor_count),
                        "submissionCountBand": count_band(submission_count),
                        "riskBand": "high", "summaryKey": f"campaign.{candidate['taxonomyBucket']}",
                        "trendDirection": "new", "expiresAt": now_epoch + aggregate_retention_days * 86400,
                        "version": 1, "environment": environment}
                    dynamodb.put_item(TableName=intelligence_table, Item=serialize(aggregate),
                        ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)")
                    results["finalized"] += 1
                else:
                    results["suppressed"] += 1
                _delete_candidate(dynamodb, pipeline_table, candidate["candidateId"], contributions)
    return results


def count_band(value):
    if value < 10: return None
    if value < 25: return "10-24"
    if value < 50: return "25-49"
    if value < 100: return "50-99"
    if value < 250: return "100-249"
    return "250+"


def _contributions(dynamodb, table, candidate_id):
    response = dynamodb.query(TableName=table,
        KeyConditionExpression="PK = :candidate AND begins_with(SK, :contribution)",
        ExpressionAttributeValues={":candidate": {"S": f"CANDIDATE#{candidate_id}"},
                                   ":contribution": {"S": "CONTRIB#"}}, ConsistentRead=True)
    return [deserialize(item) for item in response.get("Items", [])]


def _delete_candidate(dynamodb, table, candidate_id, contributions):
    requests = [{"DeleteRequest": {"Key": {"PK": {"S": f"CANDIDATE#{candidate_id}"}, "SK": {"S": "SUMMARY"}}}}]
    requests += [{"DeleteRequest": {"Key": {"PK": {"S": item["PK"]}, "SK": {"S": item["SK"]}}}}
                 for item in contributions]
    for offset in range(0, len(requests), 25):
        dynamodb.batch_write_item(RequestItems={table: requests[offset:offset + 25]})


def serialize(item):
    def one(v):
        if isinstance(v, str): return {"S": v}
        if isinstance(v, bool): return {"BOOL": v}
        if isinstance(v, (int, float)): return {"N": str(v)}
        if isinstance(v, list): return {"L": [one(x) for x in v]}
        raise TypeError(type(v).__name__)
    return {k: one(v) for k, v in item.items()}


def deserialize(item):
    def one(v):
        kind, raw = next(iter(v.items()))
        if kind == "S": return raw
        if kind == "N": return float(raw) if "." in raw else int(raw)
        if kind == "BOOL": return raw
        if kind == "L": return [one(x) for x in raw]
    return {k: one(v) for k, v in item.items()}
