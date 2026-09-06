import logging
import os
import boto3
import config
from service import delete_account_contributions, parse_deletion_record

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
dynamodb = boto3.client("dynamodb")
kms = boto3.client("kms")


def lambda_handler(event, _context):
    config.validate_config()
    totals = {"deleted": 0, "recomputedCandidates": 0}
    for record in event.get("Records", []):
        command = parse_deletion_record(record, environment=config.APP_ENVIRONMENT,
            schema_version=config.CAMPAIGN_SCHEMA_VERSION)
        if not command: continue
        result = delete_account_contributions(command, table_name=config.PIPELINE_TABLE_NAME,
            retention_days=config.TRANSIENT_RETENTION_DAYS, dynamodb=dynamodb, kms=kms)
        totals = {key: totals[key] + result[key] for key in totals}
    LOGGER.info("Campaign deletion completed | operation=delete schemaVersion=%s result=success deletedCount=%s recomputedCount=%s",
                config.CAMPAIGN_SCHEMA_VERSION, totals["deleted"], totals["recomputedCandidates"])
    return totals
