import logging
import os
import time
import boto3
import config
from publication import Publication

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
dynamodb = boto3.client("dynamodb")


def lambda_handler(event, context):
    config.validate_candidate()
    if (type(event) is not dict or event.get("environment") != config.APP_ENVIRONMENT
            or type(event.get("schemaVersion")) is not int or event["schemaVersion"] != 1):
        raise ValueError("Cross-environment or unsupported lifecycle request")
    operation = event.get("operation")
    fields = {"environment", "schemaVersion", "operation"}
    expected = {
        "recover_candidate": fields | {"candidateId"},
        "expire_locator": fields | {"locatorPK", "locatorSK"},
    }
    if operation not in expected or set(event) != expected[operation]:
        raise ValueError("Unsupported candidate operation")
    worker = Publication(client=dynamodb, pipeline=config.PIPELINE_TABLE_NAME,
        intelligence=config.INTELLIGENCE_TABLE_NAME, environment=config.APP_ENVIRONMENT,
        manifest_sha256=config.CAMPAIGN_LOCATOR_MANIFEST_SHA256,
        inventory_revision=int(config.CAMPAIGN_LOCATOR_INVENTORY_REVISION),
        now=lambda: int(time.time()), enabled=True,
        remaining_ms=context.get_remaining_time_in_millis)
    if operation == "recover_candidate":
        result = worker.process(event["candidateId"])
    else:
        result = worker.expire_locator(event["locatorPK"], event["locatorSK"])
    LOGGER.info("Campaign lifecycle candidate step completed | operation=%s schemaVersion=1", operation)
    return result
