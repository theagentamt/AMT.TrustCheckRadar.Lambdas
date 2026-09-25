"""Shared stream/recovery composition; qualified completion is explicitly gated."""
import time
from completion import Completion, CompletionUnavailable
from retained_periods import delete_retained_contributions
from shared_campaign_recovery import records as R


class CleanupGuard:
    """A late sweep cannot mutate after atomic component seal/receipt creation."""
    def __init__(self, client, ledger, command, remaining, job=None, control=None):
        self.client,self.remaining=client,remaining
        self.guards=[R.absent(ledger,command['PK'],'ACCOUNT_DELETION#CAMPAIGN')]
        if job is not None:self.guards.append(R.condition(ledger,job))
        if control is not None:self.guards.append(R.condition(ledger,control))

    def __getattr__(self,name):
        if name not in ('get_item','query','transact_write_items'):raise AttributeError(name)
        def invoke(**kwargs):
            R.need(self.remaining()>=6000)
            if name=='transact_write_items':
                actions=kwargs['TransactItems']+R.serialize_actions(self.guards)
                R.need(len(actions)<=100)
                kwargs=kwargs|{'TransactItems':actions}
            return getattr(self.client,name)(**kwargs)
        return invoke


class CleanupKms:
    def __init__(self,client,remaining):self.client,self.remaining=client,remaining
    def generate_mac(self,**kwargs):
        R.need(self.remaining()>=6000)
        return self.client.generate_mac(**kwargs)


def process(command, *, dynamodb, kms, settings, aws_account_id, aws_region,
            remaining_ms, now=lambda:int(time.time()), cleanup=delete_retained_contributions):
    """One fair sweep, then independent full proof. No callback can create receipts."""
    R.need(type(settings.CAMPAIGN_COMPLETION_ENABLED) is bool and callable(remaining_ms))
    R.validate_command(command,settings.APP_ENVIRONMENT)
    def read(pk,sk):
        R.need(remaining_ms()>=6000)
        return R.read(dynamodb,settings.DELETION_LEDGER_TABLE_NAME,pk,sk)
    stored=read(command['PK'],command['SK'])
    receipt=read(command['PK'],'ACCOUNT_DELETION#CAMPAIGN')
    progress={'deleted':0,'recomputedCandidates':0}
    enabled=settings.CAMPAIGN_COMPLETION_ENABLED
    if stored==command and receipt is None:
        job=control=None
        if enabled:
            job=R.validate_job(read(command['PK'],R.JOB_PREFIX+command['operationId']),settings.APP_ENVIRONMENT,command)
            control=R.validate_control(read(command['PK'],R.CONTROL_SK),settings.APP_ENVIRONMENT,command['PK'])
            R.need(control['state']=='OPEN')
        guarded=CleanupGuard(dynamodb,settings.DELETION_LEDGER_TABLE_NAME,command,remaining_ms,job,control)
        progress=cleanup(command,environment=settings.APP_ENVIRONMENT,aws_account_id=aws_account_id,
            aws_region=aws_region,table_name=settings.PIPELINE_TABLE_NAME,
            retention_days=settings.TRANSIENT_RETENTION_DAYS,dynamodb=guarded,kms=CleanupKms(kms,remaining_ms),remaining_ms=remaining_ms,
            deletion_ledger_table_name=settings.DELETION_LEDGER_TABLE_NAME,
            locator_manifest_sha256=settings.CAMPAIGN_LOCATOR_MANIFEST_SHA256,
            locator_inventory_revision=settings.CAMPAIGN_LOCATOR_INVENTORY_REVISION,now_epoch=now())
    elif not enabled and receipt is None:
        # Suppression recognition remains strict; retained cleanup verifies the
        # exact terminal command and performs no mutation on this branch.
        guarded=CleanupGuard(dynamodb,settings.DELETION_LEDGER_TABLE_NAME,command,remaining_ms)
        progress=cleanup(command,environment=settings.APP_ENVIRONMENT,aws_account_id=aws_account_id,
            aws_region=aws_region,table_name=settings.PIPELINE_TABLE_NAME,
            retention_days=settings.TRANSIENT_RETENTION_DAYS,dynamodb=guarded,kms=CleanupKms(kms,remaining_ms),remaining_ms=remaining_ms,
            deletion_ledger_table_name=settings.DELETION_LEDGER_TABLE_NAME,
            locator_manifest_sha256=settings.CAMPAIGN_LOCATOR_MANIFEST_SHA256,
            locator_inventory_revision=settings.CAMPAIGN_LOCATOR_INVENTORY_REVISION,now_epoch=now())
    result={'deleted':progress['deleted'],'recomputedCandidates':progress['recomputedCandidates'],
            'completionStatus':'UNVERIFIED' if enabled else 'DISABLED'}
    if not enabled:
        if progress.get('alreadyCompleted'):result['completionStatus']='TERMINAL_ACKNOWLEDGED'
        return result
    try:
        qualified=Completion(dynamodb=dynamodb,kms=kms,pipeline_table=settings.PIPELINE_TABLE_NAME,
            ledger_table=settings.DELETION_LEDGER_TABLE_NAME,users_table=settings.USERS_TABLE_NAME,
            environment=settings.APP_ENVIRONMENT,aws_account_id=aws_account_id,aws_region=aws_region,
            manifest_sha256=settings.CAMPAIGN_COMPLETION_MANIFEST_SHA256,
            inventory_revision=settings.CAMPAIGN_COMPLETION_INVENTORY_REVISION,
            locator_manifest_sha256=settings.CAMPAIGN_LOCATOR_MANIFEST_SHA256,
            locator_inventory_revision=settings.CAMPAIGN_LOCATOR_INVENTORY_REVISION,
            recovery_manifest_sha256=settings.CAMPAIGN_RECOVERY_MANIFEST_SHA256,
            recovery_inventory_revision=settings.CAMPAIGN_RECOVERY_INVENTORY_REVISION,
            now=now,remaining_ms=remaining_ms,max_periods=8).complete(command)
        if qualified.get('campaignComplete') is True:
            result['completionStatus']='COMPLETE'
        elif qualified.get('terminalAcknowledged') is True:
            result['completionStatus']='TERMINAL_ACKNOWLEDGED'
    except CompletionUnavailable:
        pass  # Actual durable job remains pending; no receipt is inferred.
    return result
