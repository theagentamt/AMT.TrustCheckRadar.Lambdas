"""Bounded eventual discovery; only the independently gated cleanup adapter can complete jobs."""
import time
from .records import *
from .jobs import update

class BudgetExhausted(RecoveryUnavailable):pass

class BudgetClient:
    def __init__(self, client, remaining_ms):
        self.client,self.remaining_ms=client,remaining_ms
    def __getattr__(self, name):
        if name not in ('query','get_item','transact_write_items'):raise AttributeError(name)
        def invoke(**kwargs):
            if self.remaining_ms()<6000:raise BudgetExhausted('CAMPAIGN_RECOVERY_TIME_BUDGET')
            return getattr(self.client,name)(**kwargs)
        return invoke

class Worker:
    def __init__(self, *, client, table, environment, manifest, revision, cleanup,
                 now=lambda:int(time.time()), remaining_ms=lambda:30000):
        self.client=client;self.table=table;self.environment=environment
        self.manifest=manifest;self.revision=revision;self.cleanup=cleanup
        self.now=now;self.remaining_ms=remaining_ms

    def run(self):
        now=integer(self.now(),1)
        metrics={name:0 for name in ('RecoveryTicks','RecoveryFailures','CommandsAttempted',
            'CommandsUnverified','CommandsCompleted','CommandsTerminalAcknowledged','SidecarSchemaFailures','IndexCandidatesObserved',
            'ObservedPendingAgeSeconds','ObservedOverdueCommands','RecoveryBudgetExhausted','RecoveryShardTruncated')}
        if self.remaining_ms()<6000:
            metrics['RecoveryBudgetExhausted']=1
            return metrics
        client=BudgetClient(self.client,self.remaining_ms)
        try:inv=validate_inventory(read(client,self.table,'INVENTORY#'+self.environment,
            'CAMPAIGN_RECOVERY_INVENTORY'),self.environment,self.manifest,self.revision,now)
        except BudgetExhausted:
            metrics['RecoveryBudgetExhausted']=1
            return metrics
        metrics['RecoveryTicks']=1
        start=(now//300)%SHARDS
        # Sixteen partitions, at most two pages of eight candidates each; four actual attempts.
        for offset in range(SHARDS):
            cursor=None
            for _ in range(2):
                if self.remaining_ms()<6000 or metrics['CommandsAttempted']>=4:
                    metrics['RecoveryBudgetExhausted']=1
                    return metrics
                args={'TableName':self.table,'IndexName':INDEX,'Limit':8,
                    'KeyConditionExpression':'campaignRecoveryPartition = :p AND nextAttemptAtEpoch <= :now',
                    'ExpressionAttributeValues':wire({':p':partition(self.environment,(start+offset)%SHARDS),':now':now})}
                if cursor:args['ExclusiveStartKey']=cursor
                try:page=client.query(**args)
                except BudgetExhausted:
                    metrics['RecoveryBudgetExhausted']=1
                    return metrics
                for candidate in page.get('Items',[]):
                    if self.remaining_ms()<6000 or metrics['CommandsAttempted']>=4:
                        metrics['RecoveryBudgetExhausted']=1
                        return metrics
                    metrics['IndexCandidatesObserved']+=1
                    try:
                        candidate=plain(candidate);pk=account_key(candidate.get('PK'))
                        sk=candidate.get('SK');need(type(sk) is str and sk.startswith(JOB_PREFIX));operation(sk[len(JOB_PREFIX):])
                        job=read(client,self.table,pk,sk)
                        if job is None:continue
                        validate_job(job,self.environment)
                        if job['nextAttemptAtEpoch']>now:continue
                        command=read(client,self.table,pk,job['commandSK'])
                        validate_command(command,self.environment);need(command['occurredAtEpoch']<=now);validate_job(job,self.environment,command)
                        control=validate_control(read(client,self.table,pk,CONTROL_SK),self.environment,pk)
                        need(control['state']=='OPEN')
                        metrics['ObservedPendingAgeSeconds']=max(metrics['ObservedPendingAgeSeconds'],now-integer(command['occurredAtEpoch'],1))
                        metrics['ObservedOverdueCommands']+=int(now>command['deleteByEpoch'])
                        need(job['revision']<9007199254740991)
                        actions=[update(self.table,job,{'revision':job['revision']+1,'nextAttemptAtEpoch':now+60}),
                            condition(self.table,command),condition(self.table,control),condition(self.table,inv)]
                        # A withdrawal may coexist with a pending account command, never a terminal one.
                        if command['SK']!='ACCOUNT_DELETION':
                            fence=read(client,self.table,pk,'ACCOUNT_DELETION')
                            if fence is not None:validate_command(fence,self.environment)
                            actions.append(condition(self.table,fence) if fence else absent(self.table,pk,'ACCOUNT_DELETION'))
                        client.transact_write_items(TransactItems=serialize_actions(actions))
                    except BudgetExhausted:
                        metrics['RecoveryBudgetExhausted']=1
                        return metrics
                    except Exception:
                        metrics['SidecarSchemaFailures']+=1;metrics['RecoveryFailures']+=1
                        continue
                    if self.remaining_ms()<6000:
                        metrics['RecoveryBudgetExhausted']=1
                        return metrics
                    metrics['CommandsAttempted']+=1
                    try:
                        outcome=self.cleanup(command)
                        status=outcome.get('completionStatus') if isinstance(outcome,dict) else None
                        if status=='COMPLETE':metrics['CommandsCompleted']+=1
                        elif status=='TERMINAL_ACKNOWLEDGED':metrics['CommandsTerminalAcknowledged']+=1
                        else:metrics['CommandsUnverified']+=1
                    except Exception:
                        metrics['RecoveryFailures']+=1;metrics['CommandsUnverified']+=1
                cursor=page.get('LastEvaluatedKey')
                if not cursor:break
            if cursor:metrics['RecoveryShardTruncated']+=1
        metrics['RecoveryTicks']=1
        return metrics
