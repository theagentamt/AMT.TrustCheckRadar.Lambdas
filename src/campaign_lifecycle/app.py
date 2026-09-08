import logging
import os
import boto3
import config
from service import expire_transient, finalize_periods, manage_keys

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
dynamodb = boto3.client("dynamodb")
kms = boto3.client("kms")


def lambda_handler(event, _context):
    config.validate_config()
    if event.get("environment") != config.APP_ENVIRONMENT or event.get("schemaVersion") != config.CAMPAIGN_SCHEMA_VERSION:
        raise ValueError("Cross-environment or unsupported lifecycle request")
    operation = event.get("operation")
    if operation == "manage_keys":
        result = manage_keys(environment=config.APP_ENVIRONMENT, project_name=config.PROJECT_NAME,
            table_name=config.PIPELINE_TABLE_NAME, dynamodb=dynamodb, kms=kms)
    elif operation == "finalize_periods":
        result = finalize_periods(environment=config.APP_ENVIRONMENT, schema_version=config.CAMPAIGN_SCHEMA_VERSION,
            pipeline_table=config.PIPELINE_TABLE_NAME, intelligence_table=config.INTELLIGENCE_TABLE_NAME,
            minimum_contributors=config.MIN_CONTRIBUTOR_COUNT,
            aggregate_retention_days=config.AGGREGATE_RETENTION_DAYS, dynamodb=dynamodb)
    elif operation == "expire_transient":
        result = expire_transient(environment=config.APP_ENVIRONMENT,
            table_name=config.PIPELINE_TABLE_NAME, index_name=config.EXPIRATION_INDEX_NAME,
            dynamodb=dynamodb)
    else:
        raise ValueError("Unknown lifecycle operation")
    LOGGER.info("Campaign lifecycle completed | operation=%s schemaVersion=%s result=success", operation,
                config.CAMPAIGN_SCHEMA_VERSION)
    return result
