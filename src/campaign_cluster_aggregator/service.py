from __future__ import annotations

import json
import time
import uuid

from scoring import similarity, updated_centroid


ENVELOPE_FIELDS = {"schemaVersion", "eventType", "environment", "statisticsEventId", "recordVersion"}


def process_message(body: str, *, environment: str, schema_version: int, table_name: str,
                    retention_days: int, max_submissions: int, dynamodb, now_epoch=None) -> str:
    envelope = _envelope(body, environment, schema_version)
    event_id = envelope["statisticsEventId"]
    feature_raw = dynamodb.get_item(TableName=table_name,
        Key={"PK": {"S": f"EVENT#{event_id}"}, "SK": {"S": "FEATURE"}}, ConsistentRead=True).get("Item")
    if not feature_raw:
        return "missing"
    feature = deserialize(feature_raw)
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    if feature.get("expiresAt", 0) <= now_epoch or feature.get("suppressed") is True:
        return "suppressed"

    dedupe_key = {"PK": {"S": f"EVENT#{event_id}"}, "SK": {"S": "CLUSTERED"}}
    if dynamodb.get_item(TableName=table_name, Key=dedupe_key, ConsistentRead=True).get("Item"):
        return "duplicate"

    candidates = _load_candidates(dynamodb, table_name, feature)
    scored = sorted(((similarity(feature, candidate), candidate) for candidate in candidates),
                    key=lambda pair: pair[0], reverse=True)
    selected = scored[0][1] if scored and scored[0][0] >= 0.82 else None
    candidate_id = selected["candidateId"] if selected else str(uuid.uuid4())
    contribution_key = {"PK": {"S": f"CANDIDATE#{candidate_id}"},
                        "SK": {"S": f"CONTRIB#{feature['contributorToken']}"}}
    existing = dynamodb.get_item(TableName=table_name, Key=contribution_key, ConsistentRead=True).get("Item")
    expires_at = now_epoch + retention_days * 86400
    if existing:
        count = int(existing.get("submissionCount", {"N": "0"})["N"])
        if count >= max_submissions:
            _dedupe(dynamodb, table_name, event_id, candidate_id, expires_at, "CONTRIBUTOR_CAPPED")
            return "contributor-capped"
        dynamodb.transact_write_items(TransactItems=[
            {"Update": {"TableName": table_name, "Key": contribution_key,
                        "UpdateExpression": "ADD submissionCount :one",
                        "ConditionExpression": "submissionCount < :maximum",
                        "ExpressionAttributeValues": {":one": {"N": "1"}, ":maximum": {"N": str(max_submissions)}}}},
            {"Update": {"TableName": table_name,
                        "Key": {"PK": {"S": f"CANDIDATE#{candidate_id}"}, "SK": {"S": "SUMMARY"}},
                        "UpdateExpression": "ADD submissionCount :one SET version = version + :one",
                        "ExpressionAttributeValues": {":one": {"N": "1"}}}},
            _dedupe_action(table_name, event_id, candidate_id, expires_at, "COUNTED"),
        ])
        return "counted-repeat"

    candidate = selected or _new_candidate(candidate_id, feature, expires_at)
    previous_version = int(candidate.get("version", 0))
    if selected:
        candidate["centroid"] = updated_centroid(selected["centroid"], selected["contributorCount"], feature["vector"])
        candidate["contributorCount"] += 1
        candidate["submissionCount"] += 1
        candidate["version"] += 1
        candidate["lexicalFingerprint"] = sorted(set(candidate.get("lexicalFingerprint", [])) | set(feature.get("lexicalFingerprint", [])))[:32]
        candidate["signalIds"] = sorted(set(candidate.get("signalIds", [])) | set(feature.get("signalIds", [])))[:16]

    put_candidate = {"Put": {"TableName": table_name, "Item": serialize(candidate)}}
    if selected:
        put_candidate["Put"].update({"ConditionExpression": "version = :version",
                                     "ExpressionAttributeValues": {":version": {"N": str(previous_version)}}})
    else:
        put_candidate["Put"]["ConditionExpression"] = "attribute_not_exists(PK) AND attribute_not_exists(SK)"
    dynamodb.transact_write_items(TransactItems=[
        put_candidate,
        {"Put": {"TableName": table_name, "Item": serialize({
            "PK": f"CANDIDATE#{candidate_id}", "SK": f"CONTRIB#{feature['contributorToken']}",
            "periodId": feature["periodId"], "submissionCount": 1, "vectorApplied": True,
            "vector": feature["vector"], "expiresAt": expires_at,
        }), "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)"}},
        _dedupe_action(table_name, event_id, candidate_id, expires_at, "COUNTED"),
    ])
    return "matched" if selected else "candidate-created"


def _load_candidates(dynamodb, table_name, feature):
    response = dynamodb.query(TableName=table_name, IndexName="CandidateBucketIndex",
        KeyConditionExpression="GSI2PK = :bucket",
        ExpressionAttributeValues={":bucket": {"S": f"PERIOD#{feature['periodId']}#BUCKET#{feature['taxonomyBucket']}"}},
        Limit=500)
    candidates = []
    for key in response.get("Items", []):
        raw = dynamodb.get_item(TableName=table_name,
            Key={"PK": key["PK"], "SK": key["SK"]}, ConsistentRead=True).get("Item")
        if raw: candidates.append(deserialize(raw))
    return candidates


def _new_candidate(candidate_id, feature, expires_at):
    return {"PK": f"CANDIDATE#{candidate_id}", "SK": "SUMMARY", "candidateId": candidate_id,
            "periodId": feature["periodId"], "taxonomyBucket": feature["taxonomyBucket"],
            "GSI2PK": f"PERIOD#{feature['periodId']}#BUCKET#{feature['taxonomyBucket']}",
            "GSI2SK": f"CANDIDATE#{candidate_id}", "centroid": feature["vector"],
            "lexicalFingerprint": feature.get("lexicalFingerprint", []),
            "signalIds": feature.get("signalIds", []), "indicatorIds": feature.get("indicatorIds", []),
            "contributorCount": 1, "submissionCount": 1, "version": 1,
            "reviewState": "UNFINALIZED", "expiresAt": expires_at}


def _dedupe(dynamodb, table_name, event_id, candidate_id, expiry, outcome):
    dynamodb.put_item(**_dedupe_action(table_name, event_id, candidate_id, expiry, outcome)["Put"])


def _dedupe_action(table_name, event_id, candidate_id, expiry, outcome):
    return {"Put": {"TableName": table_name, "Item": serialize({"PK": f"EVENT#{event_id}", "SK": "CLUSTERED",
        "candidateId": candidate_id, "outcome": outcome, "expiresAt": expiry}),
        "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)"}}


def _envelope(body, environment, schema_version):
    value = json.loads(body)
    if not isinstance(value, dict) or set(value) != ENVELOPE_FIELDS or value.get("schemaVersion") != schema_version \
            or value.get("recordVersion") != 1 or value.get("environment") != environment \
            or value.get("eventType") != "campaign.cluster.requested":
        raise ValueError("Invalid cluster envelope")
    return value


def deserialize(item):
    def one(value):
        kind, raw = next(iter(value.items()))
        if kind == "S": return raw
        if kind == "N": return float(raw) if "." in raw else int(raw)
        if kind == "BOOL": return raw
        if kind == "L": return [one(child) for child in raw]
    return {key: one(value) for key, value in item.items()}


def serialize(item):
    def one(value):
        if isinstance(value, str): return {"S": value}
        if isinstance(value, bool): return {"BOOL": value}
        if isinstance(value, (int, float)): return {"N": str(value)}
        if isinstance(value, list): return {"L": [one(child) for child in value]}
        raise TypeError(type(value).__name__)
    return {key: one(value) for key, value in item.items()}
