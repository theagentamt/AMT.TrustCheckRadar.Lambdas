"""Unwired campaign completion primitive. No handler, environment gate or marker writer.

Strong partition absence becomes stable only under the separately reviewed full
writer/repair/publication/restore inventory and persistent transactional fences.
"""
import base64
from copy import deepcopy
from datetime import UTC,datetime
import re

from coverage_assessment import _key_record,_state
from service import PERIOD_SECONDS,TOKEN_DOMAIN
from shared_campaign_locators import load_inventory
from shared_campaign_recovery import records as R
from shared_campaign_recovery.jobs import update

INVARIANTS=['fenced_writers','legacy_locators','orphan_repairs','publication_anonymity',
            'restore_non_resurrection','prior_period_erasure']
FIELDS={'PK','SK','recordType','schemaVersion','environment','revision','coverage','manifestSha256',
        'approvedAtEpoch','locatorManifestSha256','locatorInventoryRevision',
        'recoveryManifestSha256','recoveryInventoryRevision','invariants'}
RECEIPT_FIELDS={'PK','SK','schemaVersion','recordVersion','environment','eventType','component','status',
                'operationId','occurredAtEpoch','requestOccurredAtEpoch','retainUntilEpoch'}
PARTICIPATION_FIELDS={'PK','SK','schemaVersion','recordVersion','environment','state','stateVersion',
    'noticeVersion','policyVersion','consentEpochId','effectiveFrom','updatedAt','lastOperationId',
    'effectiveUntil','withdrawalRequestedAt','deletionDeadlineAt'}

class CompletionUnavailable(RuntimeError):
    def __init__(self):super().__init__('CAMPAIGN_COMPLETION_UNVERIFIED')

def need(value):
    if not value:raise CompletionUnavailable()

def stamp(epoch):return datetime.fromtimestamp(epoch,UTC).isoformat().replace('+00:00','Z')

class Completion:
    def __init__(self, *, dynamodb, kms, pipeline_table, ledger_table, users_table,
                 environment, aws_account_id, aws_region, manifest_sha256, inventory_revision,
                 locator_manifest_sha256, locator_inventory_revision,
                 recovery_manifest_sha256, recovery_inventory_revision, now, remaining_ms=lambda:30000,
                 max_periods=8):
        self.ddb,self.kms=dynamodb,kms
        self.pipeline,self.ledger,self.users=pipeline_table,ledger_table,users_table
        self.env,self.account,self.region=environment,aws_account_id,aws_region
        self.manifest,self.revision=manifest_sha256,inventory_revision
        self.locator_manifest,self.locator_revision=locator_manifest_sha256,locator_inventory_revision
        self.recovery_manifest,self.recovery_revision=recovery_manifest_sha256,recovery_inventory_revision
        self.now,self.remaining_ms,self.max_periods=now,remaining_ms,max_periods

    def _call(self,fn,**kwargs):
        need(self.remaining_ms()>=6000)
        return fn(**kwargs)

    def _get(self,table,pk,sk):
        raw=self._call(self.ddb.get_item,TableName=table,Key=R.wire(R.key(pk,sk)),ConsistentRead=True).get('Item')
        return R.plain(raw) if raw else None

    def complete(self,original):
        try:return self._complete(deepcopy(original))
        except Exception:raise CompletionUnavailable() from None

    def _complete(self,command,*,replay_only=False):
        now=R.integer(self.now(),1)
        need(self.env in ('dev','uat','prod') and type(self.max_periods) is int and 1<=self.max_periods<=32)
        need(type(self.account) is str and re.fullmatch('[0-9]{12}',self.account)
             and type(self.region) is str and re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-[0-9]',self.region))
        need(all(type(t) is str and re.fullmatch(r'[A-Za-z0-9_.-]{3,255}',t) for t in (self.pipeline,self.ledger,self.users)))
        R.validate_command(command,self.env);need(command['occurredAtEpoch']<=now)
        stored=self._get(self.ledger,command['PK'],command['SK'])
        terminal=self._terminal(command,stored,now)
        if terminal and command['SK']=='ACCOUNT_DELETION':
            # A legacy/retired full-account fence is suppression, not evidence
            # that this newly qualified campaign protocol ran.
            return {'schemaVersion':1,'campaignComplete':False,'alreadyComplete':False,
                    'accountComplete':False,'terminalAcknowledged':True}
        need(terminal or stored==command)
        receipt=self._get(self.ledger,command['PK'],'ACCOUNT_DELETION#CAMPAIGN')
        marker=self._get(self.ledger,'INVENTORY#'+self.env,'CAMPAIGN_COMPLETION_INVENTORY')
        self._marker(marker,command,now)
        # Use a guarded proxy so load_inventory cannot bypass the per-call budget.
        parent=self
        class Reader:
            def get_item(self,**kwargs):return parent._call(parent.ddb.get_item,**kwargs)
        locator=load_inventory(Reader(),self.pipeline,self.env,self.locator_manifest,self.locator_revision,now)
        recovery=R.validate_inventory(self._get(self.ledger,'INVENTORY#'+self.env,'CAMPAIGN_RECOVERY_INVENTORY'),
            self.env,self.recovery_manifest,self.recovery_revision,now)
        need(locator['approvedAtEpoch']<command['occurredAtEpoch'] and recovery['approvedAtEpoch']<command['occurredAtEpoch'])
        replay=terminal or (command['SK']=='ACCOUNT_DELETION' and receipt is not None)
        need(replay or (not replay_only and receipt is None))
        guards=[R.condition(self.ledger,marker),R.condition(self.pipeline,locator),R.condition(self.ledger,recovery)]
        first,last=int(locator['minimumPeriodId']),int(command['occurredAtEpoch'])//PERIOD_SECONDS
        need(0<=first<=last and last-first+1<=self.max_periods)
        for period in range(first,last+1):
            record=self._get(self.pipeline,f'PERIOD#{period}','HMAC_KEY')
            arn=_key_record(record,period,self.account,self.region)
            mac=self._call(self.kms.generate_mac,KeyId=arn,Message=TOKEN_DOMAIN+command['accountId'].encode(),MacAlgorithm='HMAC_SHA_256')
            need(mac.get('KeyId')==arn and mac.get('MacAlgorithm')=='HMAC_SHA_256' and type(mac.get('Mac')) is bytes and len(mac['Mac'])==32)
            partition=f"CONTRIB#{period}#{base64.urlsafe_b64encode(mac['Mac']).decode().rstrip('=')}"
            tomb=self._get(self.pipeline,partition,'TOMBSTONE')
            need(_state(tomb,self.env,partition,command['operationId']) and tomb['createdAtEpoch']<=command['occurredAtEpoch'])
            page=self._call(self.ddb.query,TableName=self.pipeline,ConsistentRead=True,Limit=2,
                KeyConditionExpression='PK = :pk',ExpressionAttributeValues=R.wire({':pk':partition}))
            need(type(page.get('Items')) is list and [R.plain(x) for x in page['Items']]==[tomb] and not page.get('LastEvaluatedKey'))
            guards.extend([R.condition(self.pipeline,record),R.condition(self.pipeline,tomb)])
        if replay:
            guards.append(R.condition(self.ledger,stored))
            if terminal:
                guards.extend(self._withdrawal_replay(command,stored,now))
            else:
                self._receipt(receipt,command,now)
                control=R.validate_control(self._get(self.ledger,command['PK'],R.CONTROL_SK),self.env,command['PK'])
                need(control['state']=='SEALED')
                need(self._get(self.ledger,command['PK'],R.JOB_PREFIX+command['operationId']) is None)
                guards.extend([R.condition(self.ledger,receipt),R.condition(self.ledger,control),
                    R.absent(self.ledger,command['PK'],R.JOB_PREFIX+command['operationId'])])
            fresh=R.integer(self.now(),1);need(fresh>=now)
            deadline=stored['completedAtEpoch']+400*86400 if terminal else receipt['retainUntilEpoch']
            need(fresh<deadline)
            need(len(guards)<=100)
            # Check-only stable proof: never regenerate audit/receipt or counters.
            # Lost verification acknowledgement fails closed, without recursion.
            self._call(self.ddb.transact_write_items,TransactItems=R.serialize_actions(guards))
            finished=R.integer(self.now(),1);need(fresh<=finished<deadline)
            return self._result(True)
        job=R.validate_job(self._get(self.ledger,command['PK'],R.JOB_PREFIX+command['operationId']),self.env,command)
        control=R.validate_control(self._get(self.ledger,command['PK'],R.CONTROL_SK),self.env,command['PK'])
        need(control['state']=='OPEN' and control['revision']<9007199254740991)
        if control['pendingJobs']==1:
            # Refuse a contradictory last-job count even when inventory pins are
            # present. Qualified producers serialize later insertion on control.
            page=self._call(self.ddb.query,TableName=self.ledger,ConsistentRead=True,Limit=2,
                KeyConditionExpression='PK = :pk AND begins_with(SK, :prefix)',
                ExpressionAttributeValues=R.wire({':pk':command['PK'],':prefix':R.JOB_PREFIX}))
            need(type(page.get('Items')) is list and [R.plain(x) for x in page['Items']]==[job]
                 and not page.get('LastEvaluatedKey'))
        profile=self._get(self.users,'USER#'+command['accountId'],'PROFILE')
        need(type(profile) is dict and profile.get('sub')==command['accountId'])
        guards.append(R.condition(self.users,profile))
        if command['SK']=='ACCOUNT_DELETION':
            need(control['pendingJobs']==1 and profile.get('status')=='DELETION_REQUESTED'
                 and profile.get('deletionOperationId')==command['operationId'])
            receipt={'PK':command['PK'],'SK':'ACCOUNT_DELETION#CAMPAIGN','schemaVersion':1,'recordVersion':1,
                'environment':self.env,'eventType':'account.deletion.component.completed','component':'CAMPAIGN',
                'status':'COMPLETE','operationId':command['operationId'],'occurredAtEpoch':now,
                'requestOccurredAtEpoch':command['occurredAtEpoch'],'retainUntilEpoch':now+120*86400}
            actions=[R.condition(self.ledger,command),R.delete(self.ledger,job),
                update(self.ledger,control,{'revision':control['revision']+1,'pendingJobs':0,'state':'SEALED'}),R.put(self.ledger,receipt)]
        else:
            actions=self._withdrawal(command,control,job,profile,now)
        operations=guards+actions
        need(len(operations)<=100)
        try:self._call(self.ddb.transact_write_items,TransactItems=R.serialize_actions(operations))
        except Exception:
            # Reconcile ambiguous mutation only through a fresh stable replay
            # proof; a still-pending command cannot dispatch another mutation.
            return self._complete(command,replay_only=True)
        return self._result(False)

    def _marker(self,row,command,now):
        need(type(row) is dict and set(row)==FIELDS and row['PK']=='INVENTORY#'+self.env
             and row['SK']=='CAMPAIGN_COMPLETION_INVENTORY' and row['recordType']=='CAMPAIGN_COMPLETION_INVENTORY'
             and R.integer(row['schemaVersion'],1)==1 and row['environment']==self.env
             and type(self.revision) is int and self.revision>0 and R.integer(row['revision'],1)==self.revision
             and type(self.manifest) is str and re.fullmatch('[0-9a-f]{64}',self.manifest)
             and row['manifestSha256']==self.manifest and row['coverage']=='VERIFIED_COMPLETE'
             and 0<R.integer(row['approvedAtEpoch'],1)<command['occurredAtEpoch']<=now
             and row['locatorManifestSha256']==self.locator_manifest
             and R.integer(row['locatorInventoryRevision'],1)==self.locator_revision
             and row['recoveryManifestSha256']==self.recovery_manifest
             and R.integer(row['recoveryInventoryRevision'],1)==self.recovery_revision
             and row['invariants']==INVARIANTS)

    def _withdrawal(self,command,control,job,profile,now):
        fence=self._get(self.ledger,command['PK'],'ACCOUNT_DELETION')
        if fence is not None:
            R.validate_command(fence,self.env)
            need(profile.get('status')=='DELETION_REQUESTED' and profile.get('deletionOperationId')==fence['operationId'])
        else:need(profile.get('status')=='ACTIVE')
        state=self._get(self.users,'USER#'+command['accountId'],'CAMPAIGN_PARTICIPATION')
        need(type(state) is dict and set(state)==PARTICIPATION_FIELDS and state['PK']=='USER#'+command['accountId']
             and state['SK']=='CAMPAIGN_PARTICIPATION' and R.integer(state['schemaVersion'],1)==1
             and R.integer(state['recordVersion'],1)==1 and state['environment']==self.env
             and state['state']=='withdrawal_pending' and state['consentEpochId']==command['consentEpochId']
             and state['lastOperationId']==command['operationId'] and 0<R.integer(state['stateVersion'],1)<9007199254740991
             and state['withdrawalRequestedAt']==stamp(command['occurredAtEpoch'])
             and state['effectiveUntil']==state['withdrawalRequestedAt']
             and state['deletionDeadlineAt']==stamp(command['deleteByEpoch']))
        need(all(type(state[x]) is str and 0<len(state[x].encode())<=64 for x in ('noticeVersion','policyVersion')))
        for field in ('effectiveFrom','updatedAt'):
            value=state[field];need(type(value) is str and value.endswith('Z') and len(value)<=40)
            epoch=datetime.fromisoformat(value.replace('Z','+00:00')).timestamp();need(0<epoch<=now)
        completed=command|{'status':'COMPLETE','completedAtEpoch':now}
        new=state|{'state':'withdrawn','stateVersion':state['stateVersion']+1,'updatedAt':stamp(now),'deletionCompletedAt':stamp(now)}
        audit={'PK':state['PK'],'SK':f"CAMPAIGN_CONSENT#{command['consentEpochId']}#{now}#{command['operationId']}#COMPLETED",
            'schemaVersion':1,'recordVersion':1,'eventType':'campaign.participation.withdrawal_completed','occurredAt':stamp(now),
            'consentEpochId':command['consentEpochId'],'operationId':command['operationId'],'resultingState':'withdrawn','expiresAt':now+400*86400}
        control_action=R.delete(self.ledger,control) if control['pendingJobs']==1 else update(self.ledger,control,
            {'revision':control['revision']+1,'pendingJobs':control['pendingJobs']-1})
        return [R.condition(self.ledger,fence) if fence else R.absent(self.ledger,command['PK'],'ACCOUNT_DELETION'),
            R.absent(self.ledger,command['PK'],'ACCOUNT_DELETION#CAMPAIGN'),R.put(self.ledger,completed,command),
            R.delete(self.ledger,job),control_action,R.put(self.users,new,state),R.put(self.users,audit)]

    def _withdrawal_replay(self,command,terminal,now):
        need(self._get(self.ledger,command['PK'],R.JOB_PREFIX+command['operationId']) is None)
        control=self._get(self.ledger,command['PK'],R.CONTROL_SK)
        if control is not None:R.validate_control(control,self.env,command['PK'])
        at=R.integer(terminal['completedAtEpoch'],1)
        expected={'PK':'USER#'+command['accountId'],
            'SK':f"CAMPAIGN_CONSENT#{command['consentEpochId']}#{at}#{command['operationId']}#COMPLETED",
            'schemaVersion':1,'recordVersion':1,'eventType':'campaign.participation.withdrawal_completed',
            'occurredAt':stamp(at),'consentEpochId':command['consentEpochId'],'operationId':command['operationId'],
            'resultingState':'withdrawn','expiresAt':at+400*86400}
        audit=self._get(self.users,expected['PK'],expected['SK'])
        need(audit==expected and R.integer(audit['schemaVersion'],1)==1
             and R.integer(audit['recordVersion'],1)==1 and now<expected['expiresAt'])
        return [R.condition(self.users,audit),
            R.condition(self.ledger,control) if control else R.absent(self.ledger,command['PK'],R.CONTROL_SK),
            R.absent(self.ledger,command['PK'],R.JOB_PREFIX+command['operationId'])]

    def _receipt(self,row,command,now):
        need(type(row) is dict and set(row)==RECEIPT_FIELDS)
        at=R.integer(row['occurredAtEpoch'],1)
        need(row=={'PK':command['PK'],'SK':'ACCOUNT_DELETION#CAMPAIGN','schemaVersion':1,'recordVersion':1,
            'environment':self.env,'eventType':'account.deletion.component.completed','component':'CAMPAIGN',
            'status':'COMPLETE','operationId':command['operationId'],'occurredAtEpoch':at,
            'requestOccurredAtEpoch':command['occurredAtEpoch'],'retainUntilEpoch':at+120*86400})
        need(R.integer(row['schemaVersion'],1)==1 and R.integer(row['recordVersion'],1)==1
             and command['occurredAtEpoch']<=at<=now<row['retainUntilEpoch'])

    def _terminal(self,command,current,now):
        if not isinstance(current,dict) or current.get('status')!='COMPLETE':return False
        at=R.integer(current.get('completedAtEpoch'),1);need(command['occurredAtEpoch']<=at<=now)
        expected=command|{'status':'COMPLETE','completedAtEpoch':at}
        if command['SK']=='ACCOUNT_DELETION':expected|={'eventType':'account.deletion.completed','retainUntilEpoch':at+120*86400}
        need(current==expected and R.integer(current['schemaVersion'],1)==1 and R.integer(current['recordVersion'],1)==1)
        return True

    @staticmethod
    def _result(replay):return {'schemaVersion':1,'campaignComplete':True,'alreadyComplete':replay,'accountComplete':False}
