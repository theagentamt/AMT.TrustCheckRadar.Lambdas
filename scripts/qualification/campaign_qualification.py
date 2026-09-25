"""Synthetic-only packaged AWS qualification. Never imported by production app.py."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import time
from copy import deepcopy

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from completion import Completion, CompletionUnavailable, INVARIANTS, stamp
from service import PERIOD_SECONDS, RECOVERY_SECONDS, TOKEN_DOMAIN
from shared_campaign_locators.core import WRITERS
from shared_campaign_recovery import records as R
from shared_campaign_recovery.jobs import backfill_actions

ACCOUNT = '107827791950'
REGION = 'us-east-1'
OP = '69a43d58-54d1-4edc-9941-8a37a5ab8d79'
CASES = ('account_complete_replay', 'withdrawal_complete_replay', 'lost_ack',
         'old_users_current_ledger', 'old_ledger_current_pipeline', 'sealed_with_job',
         'old_pipeline_locator', 'delayed_producer', 'retired_prior_key', 'missing_prior_key',
         'stale_completion_marker', 'stale_recovery_marker', 'stale_job', 'stale_control',
         'post_completion_locator', 'expired_withdrawal_audit', 'tomb_race', 'key_race',
         'inventory_race', 'writer_tombstone_race', 'replay_proof_race', 'budget_cutoff',
         'wrong_resource', 'wrong_environment', 'concurrent_completion', 'overlapping_withdrawal',
         'terminal_account', 'replay_expiry_race')


class QualificationFailure(Exception):
    pass


def require(value):
    if not value:
        raise QualificationFailure('QUALIFICATION_FAILED')


def configuration(env, context, event):
    run = env.get('QUALIFICATION_RUN_ID', '')
    require(re.fullmatch('[0-9a-f]{12}', run) is not None)
    prefix = 'amt-campaign-completion-qual-' + run
    require(type(event) is dict and set(event) == {'schemaVersion', 'operation', 'runId', 'case'})
    require(type(event['schemaVersion']) is int and event['schemaVersion'] == 1
            and event['operation'] == 'qualify-campaign-completion' and event['runId'] == run
            and event['case'] in CASES)
    tables = {kind: env.get('QUALIFICATION_' + kind.upper() + '_TABLE') for kind in ('pipeline', 'ledger', 'users')}
    require(all(name == prefix + '-' + kind for kind, name in tables.items()))
    function = prefix + '-runner'
    require(env.get('QUALIFICATION_FUNCTION_NAME') == function and context.function_name == function)
    require(context.invoked_function_arn == f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{function}')
    require(env.get('AWS_REGION') == REGION)
    arn = env.get('QUALIFICATION_KEY_ARN', '')
    require(re.fullmatch(f'arn:aws:kms:{REGION}:{ACCOUNT}:key/[0-9a-f]{{8}}(?:-[0-9a-f]{{4}}){{3}}-[0-9a-f]{{12}}', arn) is not None)
    source = env.get('QUALIFICATION_SOURCE_SHA', '')
    require(re.fullmatch('[0-9a-f]{40}', source) is not None)
    return {'run': run, 'tables': tables, 'key': arn, 'source': source,
            'tags': {'Purpose': 'campaign-completion-qualification', 'QualificationRunId': run,
                     'Environment': 'dev', 'Project': 'trustcheckradar'}}


def verify_package(config):
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / 'qualification-manifest.json').read_text())
    require(manifest['sourceSha'] == config['source'] and manifest['handler'] == 'campaign_qualification.lambda_handler')
    for name, digest in manifest['memberSha256'].items():
        require(not name.startswith('/') and '..' not in Path(name).parts)
        require(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest)


class Runner:
    def __init__(self, config, ddb, kms, context):
        self.config, self.d, self.kms, self.context = config, ddb, kms, context
        self.tables = config['tables']
        self.now = int(time.time())
        self.when = self.now - 1
        self.period = self.when // PERIOD_SECONDS
        self.subject = 'synthetic-' + config['run']
        self.cmd = {'PK': 'ACCOUNT#' + self.subject, 'SK': 'ACCOUNT_DELETION', 'schemaVersion': 1,
                    'recordVersion': 1, 'environment': 'dev', 'eventType': 'account.deletion.requested',
                    'accountId': self.subject, 'status': 'REQUESTED', 'occurredAtEpoch': self.when,
                    'deleteByEpoch': self.when + 86400, 'operationId': OP}

    def call(self, fn, **kw):
        require(self.context.get_remaining_time_in_millis() >= 6000)
        return fn(**kw)

    def preflight(self):
        for name in self.tables.values():
            table = self.call(self.d.describe_table, TableName=name)['Table']
            arn = f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{name}'
            require(table['TableArn'] == arn and table['TableStatus'] == 'ACTIVE')
            require(table['KeySchema'] == [{'AttributeName': 'PK', 'KeyType': 'HASH'}, {'AttributeName': 'SK', 'KeyType': 'RANGE'}])
            require({(x['AttributeName'], x['AttributeType']) for x in table['AttributeDefinitions']} == {('PK','S'), ('SK','S')})
            tags = self.call(self.d.list_tags_of_resource, ResourceArn=arn)
            require(not tags.get('NextToken') and {x['Key']: x['Value'] for x in tags['Tags']} == self.config['tags'])
        key = self.call(self.kms.describe_key, KeyId=self.config['key'])['KeyMetadata']
        require(key['Arn'] == self.config['key'] and key['KeyState'] == 'Enabled'
                and key['KeySpec'] == 'HMAC_256' and key['KeyUsage'] == 'GENERATE_VERIFY_MAC')
        tags = self.call(self.kms.list_resource_tags, KeyId=self.config['key'])
        require(not tags.get('Truncated') and {x['TagKey']: x['TagValue'] for x in tags['Tags']} == self.config['tags'])

    def rows(self, kind):
        result, cursor = [], None
        for _ in range(2):
            args = {'TableName': self.tables[kind], 'ConsistentRead': True, 'Limit': 100}
            if cursor: args['ExclusiveStartKey'] = cursor
            page = self.call(self.d.scan, **args)
            result.extend(R.plain(row) for row in page['Items'])
            cursor = page.get('LastEvaluatedKey')
            if not cursor: return sorted(result, key=lambda row: (row['PK'], row['SK']))
        raise QualificationFailure('QUALIFICATION_BOUND_EXCEEDED')

    def snapshot(self):
        return {kind: self.rows(kind) for kind in self.tables}

    def put(self, kind, row):
        self.call(self.d.put_item, TableName=self.tables[kind], Item=R.wire(row))

    def get(self, kind, pk, sk):
        raw = self.call(self.d.get_item, TableName=self.tables[kind], Key=R.wire(R.key(pk, sk)), ConsistentRead=True).get('Item')
        return R.plain(raw) if raw else None

    def delete(self, kind, pk, sk):
        self.call(self.d.delete_item, TableName=self.tables[kind], Key=R.wire(R.key(pk, sk)))

    def seed(self):
        # Preflight is mandatory before fixture cleanup/seed. Tables are dedicated
        # to this run, never a namespaced region of a production table.
        existing = self.snapshot()
        for kind, rows in existing.items():
            for row in rows: self.delete(kind, row['PK'], row['SK'])
        mac = self.call(self.kms.generate_mac, KeyId=self.config['key'],
                        Message=TOKEN_DOMAIN+self.subject.encode(), MacAlgorithm='HMAC_SHA_256')
        require(mac['KeyId'] == self.config['key'] and mac['MacAlgorithm'] == 'HMAC_SHA_256' and len(mac['Mac']) == 32)
        self.token = base64.urlsafe_b64encode(mac['Mac']).decode().rstrip('=')
        self.put('pipeline', {'PK':'INVENTORY#dev','SK':'CAMPAIGN_LOCATORS','recordType':'CAMPAIGN_LOCATOR_INVENTORY',
            'schemaVersion':1,'revision':1,'environment':'dev','coverage':'VERIFIED_COMPLETE','manifestSha256':'a'*64,
            'approvedAtEpoch':self.when-100,'locatorSchemaVersion':1,'minimumPeriodId':self.period-1,
            'priorPeriodsErased':True,'writers':WRITERS})
        self.put('ledger', {'PK':'INVENTORY#dev','SK':'CAMPAIGN_RECOVERY_INVENTORY','recordType':'CAMPAIGN_RECOVERY_INVENTORY',
            'schemaVersion':1,'revision':1,'environment':'dev','coverage':'VERIFIED_COMPLETE','manifestSha256':'b'*64,
            'approvedAtEpoch':self.when-100,'legacyCoverageVerified':True,'writers':R.WRITERS})
        self.put('ledger', {'PK':'INVENTORY#dev','SK':'CAMPAIGN_COMPLETION_INVENTORY','recordType':'CAMPAIGN_COMPLETION_INVENTORY',
            'schemaVersion':1,'revision':1,'environment':'dev','coverage':'VERIFIED_COMPLETE','manifestSha256':'c'*64,
            'approvedAtEpoch':self.when-100,'locatorManifestSha256':'a'*64,'locatorInventoryRevision':1,
            'recoveryManifestSha256':'b'*64,'recoveryInventoryRevision':1,'invariants':INVARIANTS})
        self.put('ledger', self.cmd)
        self.put('ledger', R.new_job(self.cmd)); self.put('ledger', R.new_control(self.cmd['PK'], 'dev'))
        self.put('users', {'PK':'USER#'+self.subject,'SK':'PROFILE','sub':self.subject,
            'status':'DELETION_REQUESTED','deletionOperationId':OP})
        for period in (self.period-1, self.period):
            self.put('pipeline', {'PK':f'PERIOD#{period}','SK':'HMAC_KEY','keyArn':self.config['key'],
                'status':'ENABLED','periodId':period,'retireAfterEpoch':(period+1)*PERIOD_SECONDS+RECOVERY_SECONDS})
            self.put('pipeline', {'PK':self.partition(period),'SK':'TOMBSTONE','recordType':'CAMPAIGN_DELETION_TOMBSTONE',
                'schemaVersion':2,'environment':'dev','periodId':period,'createdAtEpoch':self.when,
                'deletionDeadlineEpoch':self.when+21*86400,'GSI3PK':'EXPIRY#dev','GSI3SK':self.when+21*86400,
                'locatorCleanupRevision':1,'locatorCleanupState':{'operationId':OP,'phase':'SEEK','cursor':None}})

    def partition(self, period): return f'CONTRIB#{period}#{self.token}'

    def candidate(self, *, client=None, now=None, remaining=None):
        return Completion(dynamodb=client or self.d,kms=self.kms,pipeline_table=self.tables['pipeline'],
            ledger_table=self.tables['ledger'],users_table=self.tables['users'],environment='dev',
            aws_account_id=ACCOUNT,aws_region=REGION,manifest_sha256='c'*64,inventory_revision=1,
            locator_manifest_sha256='a'*64,locator_inventory_revision=1,recovery_manifest_sha256='b'*64,
            recovery_inventory_revision=1,now=lambda:self.now if now is None else now,
            remaining_ms=remaining or self.context.get_remaining_time_in_millis)

    def complete(self, **kw):
        result = self.candidate(**kw).complete(self.cmd)
        require(result['campaignComplete'] and result['accountComplete'] is False)
        return result

    def refused(self, **kw):
        before = self.snapshot()
        try: self.complete(**kw)
        except CompletionUnavailable: pass
        else: raise QualificationFailure('EXPECTED_REFUSAL')
        require(self.snapshot() == before)

    def withdrawal(self):
        self.delete('ledger', self.cmd['PK'], self.cmd['SK'])
        self.cmd = self.cmd | {'SK':'CAMPAIGN_WITHDRAWAL#'+OP,'consentEpochId':OP,
                              'eventType':'campaign.consent.withdrawn','status':'PENDING'}
        self.put('ledger',self.cmd); self.put('ledger',R.new_job(self.cmd))
        self.put('users',{'PK':'USER#'+self.subject,'SK':'PROFILE','sub':self.subject,'status':'ACTIVE'})
        self.put('users',{'PK':'USER#'+self.subject,'SK':'CAMPAIGN_PARTICIPATION','schemaVersion':1,'recordVersion':1,
            'environment':'dev','state':'withdrawal_pending','stateVersion':2,
            'noticeVersion':'research-consent-2026-09-21-v2','policyVersion':'independent-research-v1',
            'consentEpochId':OP,'lastOperationId':OP,'effectiveFrom':stamp(self.when-100),'updatedAt':stamp(self.when),
            'effectiveUntil':stamp(self.when),'withdrawalRequestedAt':stamp(self.when),
            'deletionDeadlineAt':stamp(self.when+86400)})

    def locator(self):
        # Deliberate restored legacy/unknown locator; must be preserved/refused.
        return {'PK':self.partition(self.period-1),'SK':'LOCATOR#EVENT#'+OP,'legacyFixture':True}

    def run(self, case):
        if case in ('wrong_resource','wrong_environment'):
            env = dict(os.environ)
            env['QUALIFICATION_PIPELINE_TABLE' if case=='wrong_resource' else 'AWS_REGION'] = 'not-the-fixture'
            event={'schemaVersion':1,'operation':'qualify-campaign-completion','runId':self.config['run'],'case':case}
            try: configuration(env,self.context,event)
            except QualificationFailure: return
            raise QualificationFailure('EXPECTED_PREFLIGHT_REFUSAL')
        self.seed()
        if case.startswith('withdrawal') or case=='expired_withdrawal_audit': self.withdrawal()
        if case in ('account_complete_replay','withdrawal_complete_replay','expired_withdrawal_audit'):
            require(not self.complete()['alreadyComplete']); before=self.snapshot()
            if case=='expired_withdrawal_audit': self.refused(now=self.now+400*86400)
            else: require(self.complete()['alreadyComplete'])
            require(self.snapshot()==before)
            require(self.get('ledger',self.cmd['PK'],R.JOB_PREFIX+OP) is None)
            control=self.get('ledger',self.cmd['PK'],R.CONTROL_SK)
            require(control is None if case!='account_complete_replay' else control['state']=='SEALED' and control['pendingJobs']==0)
            return
        if case=='terminal_account':
            self.put('ledger',self.cmd|{'eventType':'account.deletion.completed','status':'COMPLETE',
                'completedAtEpoch':self.now,'retainUntilEpoch':self.now+120*86400})
            before=self.snapshot();value=self.candidate().complete(self.cmd)
            require(value['campaignComplete'] is False and value['terminalAcknowledged'] is True)
            require(self.snapshot()==before);return
        if case=='overlapping_withdrawal':
            account=deepcopy(self.cmd);self.withdrawal()
            other='00000000-0000-4000-8000-000000000002'
            self.delete('ledger',self.cmd['PK'],self.cmd['SK'])
            self.cmd=self.cmd|{'SK':'CAMPAIGN_WITHDRAWAL#'+other,'operationId':other,'consentEpochId':other}
            self.put('ledger',account);self.put('ledger',self.cmd)
            self.put('ledger',R.new_job(account));self.put('ledger',R.new_job(self.cmd))
            self.put('ledger',R.new_control(account['PK'],'dev')|{'pendingJobs':2})
            profile={'PK':'USER#'+self.subject,'SK':'PROFILE','sub':self.subject,
                     'status':'DELETION_REQUESTED','deletionOperationId':OP}
            self.put('users',profile)
            state=self.get('users',profile['PK'],'CAMPAIGN_PARTICIPATION')
            self.put('users',state|{'consentEpochId':other,'lastOperationId':other})
            withdrawal=deepcopy(self.cmd);self.cmd=account;self.refused();self.cmd=withdrawal
            for period in (self.period-1,self.period):
                row=self.get('pipeline',self.partition(period),'TOMBSTONE')
                row['locatorCleanupState']['operationId']=other;self.put('pipeline',row)
            self.complete();require(self.get('ledger',account['PK'],R.CONTROL_SK)['pendingJobs']==1)
            for period in (self.period-1,self.period):
                row=self.get('pipeline',self.partition(period),'TOMBSTONE')
                row['locatorCleanupState']['operationId']=OP;self.put('pipeline',row)
            self.cmd=account;self.complete()
            require(self.get('ledger',account['PK'],R.CONTROL_SK)['state']=='SEALED');return
        if case=='replay_expiry_race':
            self.complete();before=self.snapshot();parent=self
            clock=[self.now+120*86400-1];dispatched=[]
            class Advance:
                def __getattr__(self,name):
                    def call(**kw):
                        if name=='transact_write_items':dispatched.append(True)
                        out=getattr(parent.d,name)(**kw)
                        if name=='query':clock[0]+=1
                        return out
                    return call
            candidate=self.candidate(client=Advance());candidate.now=lambda:clock[0]
            try:candidate.complete(self.cmd)
            except CompletionUnavailable:pass
            else:raise QualificationFailure('EXPIRED_REPLAY_ACCEPTED')
            require(self.snapshot()==before and not dispatched);return
        if case in ('post_completion_locator','replay_proof_race','delayed_producer'):
            self.complete()
            if case=='post_completion_locator': self.put('pipeline',self.locator());self.refused();return
            if case=='delayed_producer':
                before=self.snapshot()
                try:
                    actions=backfill_actions(self.d,self.tables['ledger'],self.tables['users'],self.cmd)
                    self.call(self.d.transact_write_items,TransactItems=R.serialize_actions(actions))
                except R.RecoveryUnavailable: pass
                else: raise QualificationFailure('DELAYED_PRODUCER_ACCEPTED')
                require(self.snapshot()==before);return
        locations={
            'old_users_current_ledger':('users','USER#'+self.subject,'PROFILE',{'status':'ACTIVE'}),
            'old_ledger_current_pipeline':('ledger','INVENTORY#dev','CAMPAIGN_COMPLETION_INVENTORY',{'revision':2}),
            'sealed_with_job':('ledger',self.cmd['PK'],R.CONTROL_SK,{'state':'SEALED','pendingJobs':0}),
            'retired_prior_key':('pipeline',f'PERIOD#{self.period-1}','HMAC_KEY',{'status':'RETIRED'}),
            'stale_completion_marker':('ledger','INVENTORY#dev','CAMPAIGN_COMPLETION_INVENTORY',{'manifestSha256':'d'*64}),
            'stale_recovery_marker':('ledger','INVENTORY#dev','CAMPAIGN_RECOVERY_INVENTORY',{'revision':2}),
            'stale_job':('ledger',self.cmd['PK'],R.JOB_PREFIX+OP,{'commandOccurredAtEpoch':self.when-10}),
            'stale_control':('ledger',self.cmd['PK'],R.CONTROL_SK,{'pendingJobs':2})}
        if case in locations:
            kind,pk,sk,change=locations[case];self.put(kind,self.get(kind,pk,sk)|change);self.refused();return
        if case=='missing_prior_key': self.delete('pipeline',f'PERIOD#{self.period-1}','HMAC_KEY');self.refused();return
        if case=='old_pipeline_locator': self.put('pipeline',self.locator());self.refused();return
        if case=='budget_cutoff':
            parent=self; budget=[30000];late=[]
            class Cutoff:
                def __getattr__(self,name):
                    def call(**kw):
                        if budget[0]<6000:late.append(name)
                        result=getattr(parent.d,name)(**kw)
                        if name=='query':budget[0]=5000
                        return result
                    return call
            self.refused(client=Cutoff(),remaining=lambda:budget[0]);require(not late);return
        self.race(case)

    def race(self, case):
        parent=self; first=[True]; mutations=[0]
        before=self.snapshot()
        class Proxy:
            def __getattr__(self,name):return getattr(parent.d,name)
            def transact_write_items(self,**kw):
                checks=all('ConditionCheck' in a for a in kw['TransactItems'])
                if not checks: mutations[0]+=1
                if first[0]:
                    first[0]=False
                    if case=='concurrent_completion':
                        require(parent.complete()['alreadyComplete'] is False)
                    elif case=='lost_ack':
                        parent.d.transact_write_items(**kw)
                        raise RuntimeError('SYNTHETIC_LOST_ACK')
                    elif case=='writer_tombstone_race':
                        actions=[R.absent(parent.tables['pipeline'],parent.partition(parent.period-1),'TOMBSTONE'),
                                 R.put(parent.tables['pipeline'],parent.locator())]
                        try: parent.d.transact_write_items(TransactItems=R.serialize_actions(actions))
                        except parent.d.exceptions.TransactionCanceledException: pass
                        else: raise QualificationFailure('WRITER_FENCE_FAILED')
                    else:
                        kind,pk,sk,change=(('pipeline',parent.partition(parent.period-1),'TOMBSTONE',{'locatorCleanupRevision':2})
                            if case in ('tomb_race','replay_proof_race') else
                            ('pipeline',f'PERIOD#{parent.period-1}','HMAC_KEY',{'status':'RETIRED'}) if case=='key_race' else
                            ('ledger','INVENTORY#dev','CAMPAIGN_COMPLETION_INVENTORY',{'revision':2}))
                        parent.put(kind,parent.get(kind,pk,sk)|change)
                return parent.d.transact_write_items(**kw)
        if case in ('lost_ack','writer_tombstone_race','concurrent_completion'):
            value=self.complete(client=Proxy())
            require(value['alreadyComplete']==(case in ('lost_ack','concurrent_completion')) and mutations[0]==1)
            require(self.get('ledger',self.cmd['PK'],R.CONTROL_SK)['pendingJobs']==0)
        else:
            try:self.complete(client=Proxy())
            except CompletionUnavailable:pass
            else:raise QualificationFailure('RACE_NOT_REFUSED')
            require(self.rows('users')==before['users'])
            if case!='replay_proof_race':
                require(self.get('ledger',self.cmd['PK'],R.JOB_PREFIX+OP) is not None)
                require(self.get('ledger',self.cmd['PK'],'ACCOUNT_DELETION#CAMPAIGN') is None)


def lambda_handler(event, context):
    stage='configuration'
    try:
        config=configuration(os.environ,context,event)
        stage='package';verify_package(config)
        stage='preflight'
        sdk=Config(connect_timeout=2,read_timeout=3,retries={'total_max_attempts':1})
        ddb=boto3.client('dynamodb',region_name=REGION,config=sdk)
        kms=boto3.client('kms',region_name=REGION,config=sdk)
        try:
            runner=Runner(config,ddb,kms,context)
            runner.preflight();stage='case';runner.run(event['case'])
        finally:
            ddb.close();kms.close()
        return {'schemaVersion':1,'case':event['case'],'passed':True,'sourceSha':config['source'],
                'syntheticOnly':True,'historicalCoverageApproved':False,'productionActivation':False}
    except Exception as exc:
        category=('SDK_ACCESS_DENIED' if isinstance(exc,ClientError) and exc.response.get('Error',{}).get('Code') in
            ('AccessDenied','AccessDeniedException','UnauthorizedOperation') else 'SDK_FAILURE' if isinstance(exc,ClientError) else
            'COMPLETION_UNVERIFIED' if isinstance(exc,CompletionUnavailable) else 'FIXTURE_ASSERTION_FAILED' if isinstance(exc,QualificationFailure) else 'UNEXPECTED_FAILURE')
        # Deliberately no exception, row, token, key, account or event logging.
        return {'schemaVersion':1,'passed':False,'code':'QUALIFICATION_FAILED','failureStage':stage,'failureCategory':category,
                'syntheticOnly':True,'historicalCoverageApproved':False,'productionActivation':False}
