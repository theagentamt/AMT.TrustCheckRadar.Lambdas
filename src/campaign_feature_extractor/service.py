from __future__ import annotations

import hashlib
import json
import re
import time


ENVELOPE_FIELDS = {"schemaVersion", "eventType", "environment", "statisticsEventId", "recordVersion"}
WORD_PATTERN = re.compile(r"[\w]{3,}", re.UNICODE)


def process_message(body: str, *, environment: str, schema_version: int, table_name: str,
                    cluster_queue_url: str, model_version: str, retention_days: int,
                    dynamodb, sqs, encoder, now_epoch: int | None = None) -> str:
    envelope = _parse_envelope(body, environment, schema_version)
    event_id = envelope["statisticsEventId"]
    observation = dynamodb.get_item(
        TableName=table_name,
        Key={"PK": {"S": f"EVENT#{event_id}"}, "SK": {"S": "OBSERVATION"}},
        ConsistentRead=True,
    ).get("Item")
    if not observation:
        return "missing"
    item = _deserialize_item(observation)
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    if item.get("expiresAt", 0) <= now_epoch or item.get("suppressed") is True:
        return "suppressed"
    if item.get("environment") != environment or item.get("schemaVersion") != schema_version:
        raise ValueError("Observation contract mismatch")

    existing = dynamodb.get_item(
        TableName=table_name,
        Key={"PK": {"S": f"EVENT#{event_id}"}, "SK": {"S": "FEATURE"}},
        ConsistentRead=True,
    ).get("Item")
    if existing and existing.get("deliveryStatus") == {"S": "PUBLISHED"}:
        return "duplicate"

    if not existing:
        vector = encoder.encode(item["sanitizedText"])
        feature = {
            "PK": f"EVENT#{event_id}",
            "SK": "FEATURE",
            "schemaVersion": schema_version,
            "recordVersion": 1,
            "environment": environment,
            "statisticsEventId": event_id,
            "periodId": item["periodId"],
            "contributorToken": item["contributorToken"],
            "taxonomyBucket": _taxonomy_bucket(item.get("signalIds", [])),
            "languageId": _language_id(item["sanitizedText"]),
            "vector": vector,
            "lexicalFingerprint": _fingerprint(item["sanitizedText"]),
            "signalIds": item.get("signalIds", [])[:16],
            "confidence": 1.0,
            "modelVersion": model_version,
            "deliveryStatus": "PENDING",
            "expiresAt": min(item["expiresAt"] + 18 * 24 * 60 * 60, now_epoch + retention_days * 86400),
        }
        dynamodb.put_item(
            TableName=table_name,
            Item=_serialize_item(feature),
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )

    cluster_envelope = dict(envelope)
    cluster_envelope["eventType"] = "campaign.cluster.requested"
    sqs.send_message(
        QueueUrl=cluster_queue_url,
        MessageBody=json.dumps(cluster_envelope, sort_keys=True, separators=(",", ":")),
    )
    dynamodb.update_item(
        TableName=table_name,
        Key={"PK": {"S": f"EVENT#{event_id}"}, "SK": {"S": "FEATURE"}},
        UpdateExpression="SET deliveryStatus = :published",
        ConditionExpression="deliveryStatus = :pending",
        ExpressionAttributeValues={":published": {"S": "PUBLISHED"}, ":pending": {"S": "PENDING"}},
    )
    return "published"


def _parse_envelope(body: str, environment: str, schema_version: int) -> dict:
    try:
        value = json.loads(body)
    except (TypeError, json.JSONDecodeError) as err:
        raise ValueError("Malformed queue envelope") from err
    if not isinstance(value, dict) or set(value) != ENVELOPE_FIELDS:
        raise ValueError("Queue envelope fields are invalid")
    if value["schemaVersion"] != schema_version or value["recordVersion"] != 1:
        raise ValueError("Queue envelope version is invalid")
    if value["eventType"] != "campaign.feature.requested" or value["environment"] != environment:
        raise ValueError("Queue envelope routing is invalid")
    return value


def _taxonomy_bucket(signal_ids: list[str]) -> str:
    mapping = {
        "payment_request": "advance_fee",
        "credential_request": "credential_theft",
        "impersonation": "impersonation",
        "investment_promise": "investment",
    }
    return next((mapping[value] for value in signal_ids if value in mapping), "unknown")


def _language_id(text: str) -> str:
    lowered = f" {text.lower()} "
    spanish_markers = (" el ", " la ", " que ", " por ", " para ", " dinero ", " cuenta ")
    return "es" if sum(marker in lowered for marker in spanish_markers) >= 2 else "en"


def _fingerprint(text: str) -> list[str]:
    words = sorted(set(WORD_PATTERN.findall(text.casefold())))[:64]
    return sorted({hashlib.sha256(word.encode("utf-8")).hexdigest()[:16] for word in words})[:32]


def _deserialize_item(item: dict) -> dict:
    result = {}
    for key, value in item.items():
        kind, raw = next(iter(value.items()))
        if kind == "S": result[key] = raw
        elif kind == "N": result[key] = float(raw) if "." in raw else int(raw)
        elif kind == "BOOL": result[key] = raw
        elif kind == "L": result[key] = [_deserialize_item({"v": child})["v"] for child in raw]
    return result


def _serialize_item(item: dict) -> dict:
    return {key: _serialize(value) for key, value in item.items()}


def _serialize(value):
    if isinstance(value, str): return {"S": value}
    if isinstance(value, bool): return {"BOOL": value}
    if isinstance(value, (int, float)): return {"N": str(value)}
    if isinstance(value, list): return {"L": [_serialize(child) for child in value]}
    raise TypeError(type(value).__name__)
