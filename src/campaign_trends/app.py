import json
import logging
import os
import boto3
import config
from service import TrendsError, list_trends

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
dynamodb = boto3.client("dynamodb")


def lambda_handler(event, _context):
    try:
        config.validate_config()
        result = list_trends(event, environment=config.APP_ENVIRONMENT,
            table_name=config.INTELLIGENCE_TABLE_NAME, index_name=config.PUBLICATION_INDEX_NAME,
            maximum_page_size=config.MAXIMUM_PAGE_SIZE, token_ttl=config.PAGINATION_TOKEN_TTL_SECS,
            dynamodb=dynamodb)
        LOGGER.info("Campaign trends completed | operation=list schemaVersion=%s result=success resultCount=%s",
                    config.CAMPAIGN_SCHEMA_VERSION, len(result["trends"]))
        return response(200, result)
    except TrendsError as err:
        LOGGER.warning("Campaign trends rejected | operation=list schemaVersion=%s result=%s",
                       config.CAMPAIGN_SCHEMA_VERSION, err.code)
        return response(err.status, {"schemaVersion": 1, "error": {"code": err.code, "message": err.message}})


def response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, sort_keys=True, separators=(",", ":"))}
