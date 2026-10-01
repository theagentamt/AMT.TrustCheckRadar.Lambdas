import logging
import os
import time
import boto3
import config
from publication import Publication
from shared_campaign_locators import period as period_fence

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
dynamodb = boto3.client("dynamodb")


def lambda_handler(event, context):
    config.validate_candidate()
    period_fence.runtime(context)
    if (type(event) is not dict or event.get("environment") != config.APP_ENVIRONMENT
            or type(event.get("schemaVersion")) is not int or event["schemaVersion"] != 1):
        raise ValueError("Cross-environment or unsupported lifecycle request")
    operation = event.get("operation")
    fields = {"environment", "schemaVersion", "operation"}
    expected = {
        "close_period": fields | {"periodId"},
        "reconcile_periods": fields,
        "reconcile_aggregates": fields,
        "recover_candidate": fields | {"candidateId"},
        "recover_expired_orphan": fields | {"candidateId","periodId"},
        "expire_locator": fields | {"locatorPK", "locatorSK"},
    }
    if operation not in expected or set(event) != expected[operation]:
        raise ValueError("Unsupported candidate operation")
    if operation == 'reconcile_aggregates':
        from shared_campaign_work.aggregates import tick
        return tick(dynamodb,boto3.client('cloudwatch'),now=lambda:int(time.time()),remaining_ms=context.get_remaining_time_in_millis)
    if operation == 'reconcile_periods':
        from shared_campaign_work.scheduler import tick
        worker=Publication(client=dynamodb,pipeline=config.PIPELINE_TABLE_NAME,intelligence=config.INTELLIGENCE_TABLE_NAME,
            environment=config.APP_ENVIRONMENT,manifest_sha256=config.CAMPAIGN_LOCATOR_MANIFEST_SHA256,
            inventory_revision=int(config.CAMPAIGN_LOCATOR_INVENTORY_REVISION),now=lambda:int(time.time()),enabled=True,
            remaining_ms=context.get_remaining_time_in_millis)
        metrics=boto3.client('cloudwatch')
        result=tick(dynamodb,boto3.client('kms'),metrics,now=lambda:int(time.time()),remaining_ms=context.get_remaining_time_in_millis,
            recover_candidate=worker.process)
        return result
    if operation == 'close_period':
        return period_fence.close(dynamodb,config.PIPELINE_TABLE_NAME,config.APP_ENVIRONMENT,
            event['periodId'],config.CAMPAIGN_LOCATOR_MANIFEST_SHA256,
            int(config.CAMPAIGN_LOCATOR_INVENTORY_REVISION),int(time.time()),context.get_remaining_time_in_millis)
    worker = Publication(client=dynamodb, pipeline=config.PIPELINE_TABLE_NAME,
        intelligence=config.INTELLIGENCE_TABLE_NAME, environment=config.APP_ENVIRONMENT,
        manifest_sha256=config.CAMPAIGN_LOCATOR_MANIFEST_SHA256,
        inventory_revision=int(config.CAMPAIGN_LOCATOR_INVENTORY_REVISION),
        now=lambda: int(time.time()), enabled=True,
        remaining_ms=context.get_remaining_time_in_millis)
    if operation == "recover_expired_orphan":
        result = worker.recover_expired_orphan(event["candidateId"],event["periodId"])
    elif operation == "recover_candidate":
        result = worker.process(event["candidateId"])
    else:
        result = worker.expire_locator(event["locatorPK"], event["locatorSK"])
    LOGGER.info("Campaign lifecycle candidate step completed | operation=%s schemaVersion=1", operation)
    return result
