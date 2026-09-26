import json
import time
import logging
import os
import re
import boto3
from botocore.config import Config
import config
from shared_campaign_locators import period as period_fence
from service import parse_deletion_record
from retained_periods import delete_retained_contributions
from orchestration import process

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
SDK_CONFIG = Config(connect_timeout=2, read_timeout=3, retries={"total_max_attempts":1})
SDK_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
dynamodb = boto3.client("dynamodb", region_name=SDK_REGION, config=SDK_CONFIG)
kms = boto3.client("kms", region_name=SDK_REGION, config=SDK_CONFIG)


def _identity(context):
    identity=context.invoked_function_arn.split(':')
    if (len(identity) not in (7,8) or identity[:3]!=['arn','aws','lambda'] or identity[5]!='function'
            or SDK_REGION!=identity[3] or not re.fullmatch('[0-9]{12}',identity[4])
            or not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-[0-9]',identity[3])):
        raise RuntimeError('CAMPAIGN_RUNTIME_IDENTITY_INVALID')
    if not callable(getattr(context,'get_remaining_time_in_millis',None)):
        raise RuntimeError('CAMPAIGN_RUNTIME_BUDGET_REQUIRED')
    return identity


def _process(command, context, identity):
    return process(command,dynamodb=dynamodb,kms=kms,settings=config,
        aws_account_id=identity[4],aws_region=identity[3],remaining_ms=context.get_remaining_time_in_millis,
        now=lambda:int(time.time()),cleanup=delete_retained_contributions)


def _stream_command(record, identity):
    expected=f'arn:aws:dynamodb:{identity[3]}:{identity[4]}:table/{config.DELETION_LEDGER_TABLE_NAME}/stream/'
    if (not isinstance(record,dict) or record.get('eventSource')!='aws:dynamodb'
            or not isinstance(record.get('eventSourceARN'),str)
            or not record['eventSourceARN'].startswith(expected)
            or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}',record['eventSourceARN'][len(expected):])):
        raise RuntimeError('CAMPAIGN_DELETION_SOURCE_INVALID')
    if record.get('eventName')=='REMOVE':return None
    if record.get('eventName') not in ('INSERT','MODIFY'):
        raise RuntimeError('CAMPAIGN_DELETION_EVENT_INVALID')
    raw=record.get('dynamodb',{}).get('NewImage',{})
    sort=raw.get('SK') if isinstance(raw,dict) else None
    # Shared-ledger control/sidecar/receipt/inventory events are not commands.
    # Malformed command keys continue through strict parsing and fail closed.
    kind=raw.get('eventType') if isinstance(raw,dict) else None
    declared_command=kind in ({'S':'account.deletion.requested'},{'S':'campaign.consent.withdrawn'})
    if not declared_command and isinstance(sort,dict) and set(sort)=={'S'} and isinstance(sort['S'],str):
        if sort['S']!='ACCOUNT_DELETION' and not sort['S'].startswith('CAMPAIGN_WITHDRAWAL#'):
            return None
    return parse_deletion_record(record,environment=config.APP_ENVIRONMENT,
                                 schema_version=config.CAMPAIGN_SCHEMA_VERSION)


def lambda_handler(event, context):
    if isinstance(event,dict) and event.get('operation')=='reconcile-campaign-cleanup':
        return _recover(event,context)
    if not config.CAMPAIGN_DELETION_STREAM_ENABLED:
        # A accidentally delivered pending stream batch must not be acknowledged.
        raise RuntimeError('CAMPAIGN_DELETION_STREAM_DISABLED') from None
    config.validate_config()
    period_fence.runtime(context)
    identity=_identity(context)
    if not isinstance(event,dict) or not isinstance(event.get('Records'),list) or len(event['Records'])>10:
        raise RuntimeError('CAMPAIGN_DELETION_EVENT_INVALID')
    totals={'deleted':0,'recomputedCandidates':0,'pending':0,'completed':0,'terminalAcknowledged':0,'ignored':0}
    for record in event['Records']:
        try:
            command=_stream_command(record,identity)
            if command is None:
                totals['ignored']+=1;continue
            result=_process(command,context,identity)
            for name in ('deleted','recomputedCandidates'):totals[name]+=result[name]
            status=result['completionStatus']
            if status=='COMPLETE':totals['completed']+=1
            elif status=='TERMINAL_ACKNOWLEDGED':totals['terminalAcknowledged']+=1
            else:totals['pending']+=1
        except Exception:
            totals['pending']+=1
    LOGGER.info('Campaign deletion outcome | deletedCount=%s recomputedCount=%s pendingCount=%s completedCount=%s terminalCount=%s',
        totals['deleted'],totals['recomputedCandidates'],totals['pending'],totals['completed'],totals['terminalAcknowledged'])
    if totals['pending']:
        # No partial-batch response is configured; replay completed records safely.
        raise RuntimeError('CAMPAIGN_DELETION_RECONCILIATION_REQUIRED') from None
    return totals


def _recover(event, context):
    if not config.CAMPAIGN_RECOVERY_ENABLED:
        return {"enabled":False,"complete":False}
    from shared_campaign_recovery.worker import Worker
    from shared_campaign_recovery.records import INDEX
    config.validate_config()
    if event != {"schemaVersion":1,"operation":"reconcile-campaign-cleanup"} or type(event["schemaVersion"]) is not int:
        raise RuntimeError("Invalid campaign recovery event")
    if config.CAMPAIGN_RECOVERY_INDEX_NAME != INDEX:
        raise RuntimeError("Invalid campaign recovery index")
    period_fence.runtime(context)
    identity=_identity(context)
    remaining=context.get_remaining_time_in_millis
    def cleanup(command):return _process(command,context,identity)
    metrics={"RecoveryTicks":0,"RecoveryFailures":1}
    try:
        metrics=Worker(client=dynamodb,table=config.DELETION_LEDGER_TABLE_NAME,
            environment=config.APP_ENVIRONMENT,manifest=config.CAMPAIGN_RECOVERY_MANIFEST_SHA256,
            revision=config.CAMPAIGN_RECOVERY_INVENTORY_REVISION,cleanup=cleanup,
            remaining_ms=remaining).run()
        return {"enabled":True,"complete":False,"receiptEligible":False,**metrics}
    finally:
        print(json.dumps({"_aws":{"Timestamp":int(time.time()*1000),"CloudWatchMetrics":[{
            "Namespace":"TrustCheckRadar/Campaign","Dimensions":[["Environment"]],
            "Metrics":[{"Name":name} for name in metrics]}]},"Environment":config.APP_ENVIRONMENT,**metrics}))
