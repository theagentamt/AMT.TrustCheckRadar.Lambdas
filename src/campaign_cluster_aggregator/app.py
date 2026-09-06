import logging
import os
import boto3
import config
from service import process_message

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
dynamodb = boto3.client("dynamodb")


def lambda_handler(event, _context):
    config.validate_config()
    failures, successes = [], 0
    for record in event.get("Records", []):
        try:
            process_message(record.get("body"), environment=config.APP_ENVIRONMENT,
                schema_version=config.CAMPAIGN_SCHEMA_VERSION, table_name=config.PIPELINE_TABLE_NAME,
                retention_days=config.TRANSIENT_RETENTION_DAYS,
                max_submissions=config.MAX_CONTRIBUTOR_SUBMISSIONS, dynamodb=dynamodb)
            successes += 1
        except Exception:
            failures.append({"itemIdentifier": record.get("messageId", "unknown")})
    LOGGER.info("Campaign cluster batch completed | operation=cluster schemaVersion=%s result=complete successCount=%s failureCount=%s",
                config.CAMPAIGN_SCHEMA_VERSION, successes, len(failures))
    return {"batchItemFailures": failures}
