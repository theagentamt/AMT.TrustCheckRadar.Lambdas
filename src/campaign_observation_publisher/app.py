import logging
import os

import boto3

import config
from contracts import ContractError, parse_stream_record
from service import publish_observation


LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

dynamodb_client = boto3.client("dynamodb")
kms_client = boto3.client("kms")
sqs_client = boto3.client("sqs")
cloudwatch_client = boto3.client("cloudwatch")


def lambda_handler(event, _context):
    config.validate_config()
    records = event.get("Records") if isinstance(event, dict) else None
    if not isinstance(records, list):
        raise ContractError("Event must contain a Records list")

    results = {}
    for record in records:
        try:
            item = parse_stream_record(
                record,
                environment=config.APP_ENVIRONMENT,
                schema_version=config.CAMPAIGN_SCHEMA_VERSION,
            )
        except ContractError:
            _metric("malformed")
            raise
        if item is None:
            continue
        result = publish_observation(
            item,
            pipeline_table_name=config.PIPELINE_TABLE_NAME,
            users_table_name=config.USERS_TABLE_NAME,
            cluster_queue_url=config.CLUSTER_QUEUE_URL,
            hmac_key_resolver=_period_key,
            transient_retention_days=config.TRANSIENT_RETENTION_DAYS,
            dynamodb_client=dynamodb_client,
            kms_client=kms_client,
            sqs_client=sqs_client,
        )
        results[result] = results.get(result, 0) + 1
        _metric(result)

    LOGGER.info(
        "Campaign observation batch completed | operation=publish schemaVersion=%s result=success recordCount=%s",
        config.CAMPAIGN_SCHEMA_VERSION,
        sum(results.values()),
    )
    return {"processed": sum(results.values()), "results": results}


def _period_key(period_id: int) -> str:
    response = dynamodb_client.get_item(
        TableName=config.PIPELINE_TABLE_NAME,
        Key={"PK": {"S": f"PERIOD#{period_id}"}, "SK": {"S": "HMAC_KEY"}},
        ConsistentRead=True,
        ProjectionExpression="keyArn,#status",
        ExpressionAttributeNames={"#status": "status"},
    )
    item = response.get("Item") or {}
    if item.get("status") != {"S": "ENABLED"} or "keyArn" not in item:
        raise RuntimeError("The contributor period key is unavailable")
    return item["keyArn"]["S"]


def _metric(result: str) -> None:
    metric_name = {
        "published": "Published",
        "consent-suppressed": "ConsentSuppressed",
        "participation-suppressed": "ParticipationSuppressed",
        "duplicate": "Duplicate",
        "expired": "Expired",
        "malformed": "Malformed",
    }[result]
    try:
        cloudwatch_client.put_metric_data(
            Namespace="TrustCheckRadar/Campaign",
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Value": 1,
                    "Unit": "Count",
                    "Dimensions": [
                        {"Name": "Environment", "Value": config.APP_ENVIRONMENT},
                    ],
                }
            ],
        )
    except Exception:
        LOGGER.warning(
            "Campaign metric emission failed | operation=metric schemaVersion=%s result=failed",
            config.CAMPAIGN_SCHEMA_VERSION,
        )
