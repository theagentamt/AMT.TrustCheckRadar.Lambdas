import logging
import os
import boto3
from botocore.config import Config
import config
from service import parse_deletion_record
from retained_periods import delete_retained_contributions

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
SDK_CONFIG = Config(connect_timeout=2, read_timeout=3, retries={"total_max_attempts":1})
dynamodb = boto3.client("dynamodb", config=SDK_CONFIG)
kms = boto3.client("kms", config=SDK_CONFIG)


def lambda_handler(event, context):
    config.validate_config()
    totals = {"deleted":0,"recomputedCandidates":0,"pending":0}
    failures = []
    remaining = getattr(context, 'get_remaining_time_in_millis', None)
    for record in event.get("Records", []):
        try:
            command = parse_deletion_record(record,environment=config.APP_ENVIRONMENT,
                                            schema_version=config.CAMPAIGN_SCHEMA_VERSION)
            if not command:
                continue
            identity = context.invoked_function_arn.split(":")
            if len(identity) not in (7,8) or identity[0:3] != ["arn","aws","lambda"]:
                raise RuntimeError("Campaign runtime identity is invalid")
            result = delete_retained_contributions(command,environment=config.APP_ENVIRONMENT,
                aws_account_id=identity[4],aws_region=identity[3],table_name=config.PIPELINE_TABLE_NAME,
                retention_days=config.TRANSIENT_RETENTION_DAYS,dynamodb=dynamodb,kms=kms,remaining_ms=remaining,
                deletion_ledger_table_name=config.DELETION_LEDGER_TABLE_NAME,
                locator_manifest_sha256=config.CAMPAIGN_LOCATOR_MANIFEST_SHA256,
                locator_inventory_revision=config.CAMPAIGN_LOCATOR_INVENTORY_REVISION)
            if result.get("alreadyCompleted"):
                continue
            for name in ('deleted','recomputedCandidates'):
                totals[name] += result[name]
            # Progress does not acknowledge account/withdrawal completion. Stream
            # retries are bounded by existing source policy; reconciliation is a
            # required activation gate, not promised by this candidate.
            totals['pending'] += 1
        except Exception:
            totals['pending'] += 1
        sequence = record.get('dynamodb',{}).get('SequenceNumber')
        if not isinstance(sequence,str) or not sequence.isdigit():
            raise RuntimeError('Campaign deletion requires reconciliation') from None
        failures.append({'itemIdentifier':sequence})
    LOGGER.info('Campaign deletion progress | deletedCount=%s recomputedCount=%s pendingCount=%s',
                totals['deleted'],totals['recomputedCandidates'],totals['pending'])
    if failures:
        # Existing stream mapping does not enable partial-batch responses. A
        # successful return would silently acknowledge unfinished requests.
        raise RuntimeError('Campaign deletion requires reconciliation') from None
    return totals
