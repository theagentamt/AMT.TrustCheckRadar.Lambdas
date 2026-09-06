import json
import logging
import os
import boto3
import config
from service import ReviewError, review

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
dynamodb = boto3.client("dynamodb")


def lambda_handler(event, _context):
    try:
        config.validate_config()
        body = review(event, table_name=config.INTELLIGENCE_TABLE_NAME, reviewer_group=config.REVIEWER_GROUP,
                      minimum_contributors=config.MIN_CONTRIBUTOR_COUNT, dynamodb=dynamodb)
        LOGGER.info("Campaign review completed | operation=transition schemaVersion=%s result=success", config.CAMPAIGN_SCHEMA_VERSION)
        return _response(200, body)
    except ReviewError as err:
        LOGGER.warning("Campaign review rejected | operation=transition schemaVersion=%s result=%s", config.CAMPAIGN_SCHEMA_VERSION, err.code)
        return _response(err.status, {"schemaVersion": 1, "error": {"code": err.code, "message": err.message}})


def _response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, separators=(",", ":"), sort_keys=True)}
