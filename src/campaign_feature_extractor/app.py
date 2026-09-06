import logging
import os

import boto3

import config
from model import LocalSentenceEncoder
from service import process_message


LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
dynamodb = boto3.client("dynamodb")
sqs = boto3.client("sqs")
encoder = None


def lambda_handler(event, _context):
    global encoder
    config.validate_config()
    encoder = encoder or LocalSentenceEncoder(config.MODEL_PATH)
    failures = []
    counts = {}
    for record in event.get("Records", []):
        try:
            result = process_message(
                record.get("body"), environment=config.APP_ENVIRONMENT,
                schema_version=config.CAMPAIGN_SCHEMA_VERSION,
                table_name=config.PIPELINE_TABLE_NAME, cluster_queue_url=config.CLUSTER_QUEUE_URL,
                model_version=config.MODEL_VERSION, retention_days=config.TRANSIENT_RETENTION_DAYS,
                dynamodb=dynamodb, sqs=sqs, encoder=encoder,
            )
            counts[result] = counts.get(result, 0) + 1
        except Exception:
            failures.append({"itemIdentifier": record.get("messageId", "unknown")})
    LOGGER.info("Campaign feature batch completed | operation=extract schemaVersion=%s result=complete successCount=%s failureCount=%s",
                config.CAMPAIGN_SCHEMA_VERSION, sum(counts.values()), len(failures))
    return {"batchItemFailures": failures}
