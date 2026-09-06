from __future__ import annotations

import base64
import json
import time

from botocore.exceptions import ClientError

from contracts import build_feature_envelope


TOKEN_DOMAIN = b"campaign-contributor:v1\0"
PERIOD_SECONDS = 14 * 24 * 60 * 60


def publish_observation(
    item: dict,
    *,
    pipeline_table_name: str,
    feature_queue_url: str,
    hmac_key_id: str,
    observation_retention_hours: int,
    transient_retention_days: int,
    dynamodb_client,
    kms_client,
    sqs_client,
    now_epoch: int | None = None,
) -> str:
    if not item["campaignConsentGranted"]:
        return "consent-suppressed"

    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    if item["expiresAt"] <= now_epoch:
        return "expired"

    period_id = contributor_period_id(item["observedAtEpoch"])
    event_id = item["statisticsEventId"]
    key = {"PK": {"S": f"EVENT#{event_id}"}, "SK": {"S": "DEDUPE"}}
    existing = dynamodb_client.get_item(
        TableName=pipeline_table_name,
        Key=key,
        ConsistentRead=True,
        ProjectionExpression="#status",
        ExpressionAttributeNames={"#status": "status"},
    ).get("Item")
    if existing and existing.get("status") == {"S": "PUBLISHED"}:
        return "duplicate"

    if not existing:
        contributor_token = derive_contributor_token(
            item["accountId"],
            key_id=hmac_key_id,
            kms_client=kms_client,
        )
        observation_expiry = min(
            item["expiresAt"],
            now_epoch + observation_retention_hours * 60 * 60,
        )
        transient_expiry = now_epoch + transient_retention_days * 24 * 60 * 60
        try:
            dynamodb_client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": pipeline_table_name,
                            "Item": _serialize_item(
                                {
                                    "PK": f"EVENT#{event_id}",
                                    "SK": "OBSERVATION",
                                    "schemaVersion": item["schemaVersion"],
                                    "recordVersion": item["recordVersion"],
                                    "environment": item["environment"],
                                    "statisticsEventId": event_id,
                                    "periodId": period_id,
                                    "contributorToken": contributor_token,
                                    "GSI1PK": f"CONTRIB#{period_id}#{contributor_token}",
                                    "GSI1SK": f"EVENT#{event_id}",
                                    "sourceType": item["sourceType"],
                                    "sanitizedText": item["sanitizedText"],
                                    "riskLevel": item["riskLevel"],
                                    "signalIds": item["signalIds"],
                                    "expiresAt": observation_expiry,
                                }
                            ),
                            "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": pipeline_table_name,
                            "Item": _serialize_item(
                                {
                                    "PK": f"EVENT#{event_id}",
                                    "SK": "DEDUPE",
                                    "status": "PENDING",
                                    "periodId": period_id,
                                    "expiresAt": transient_expiry,
                                }
                            ),
                            "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                        }
                    },
                ]
            )
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") != "TransactionCanceledException":
                raise
            concurrent = dynamodb_client.get_item(
                TableName=pipeline_table_name,
                Key=key,
                ConsistentRead=True,
                ProjectionExpression="#status",
                ExpressionAttributeNames={"#status": "status"},
            ).get("Item")
            if concurrent and concurrent.get("status") == {"S": "PUBLISHED"}:
                return "duplicate"
            if not concurrent:
                raise

    envelope = build_feature_envelope(item)
    sqs_client.send_message(
        QueueUrl=feature_queue_url,
        MessageBody=json.dumps(envelope, separators=(",", ":"), sort_keys=True),
    )
    dynamodb_client.update_item(
        TableName=pipeline_table_name,
        Key=key,
        UpdateExpression="SET #status = :published, publishedAtEpoch = :published_at",
        ConditionExpression="#status = :pending",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":published": {"S": "PUBLISHED"},
            ":pending": {"S": "PENDING"},
            ":published_at": {"N": str(now_epoch)},
        },
    )
    return "published"


def contributor_period_id(epoch_seconds: int) -> int:
    return epoch_seconds // PERIOD_SECONDS


def derive_contributor_token(account_id: str, *, key_id: str, kms_client) -> str:
    response = kms_client.generate_mac(
        KeyId=key_id,
        Message=TOKEN_DOMAIN + account_id.encode("utf-8"),
        MacAlgorithm="HMAC_SHA_256",
    )
    return base64.urlsafe_b64encode(response["Mac"]).decode("ascii").rstrip("=")


def _serialize_item(value: dict) -> dict:
    return {key: _serialize_value(item) for key, item in value.items()}


def _serialize_value(value):
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, int):
        return {"N": str(value)}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, list):
        return {"L": [_serialize_value(item) for item in value]}
    raise TypeError(f"Unsupported DynamoDB value type: {type(value).__name__}")
