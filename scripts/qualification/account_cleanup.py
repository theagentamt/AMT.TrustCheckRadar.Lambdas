"""Contained all-component fixture; no production import or customer identity API."""
import base64
import hashlib
import importlib.util
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import boto3
from botocore.config import Config

EXTRA_TABLES = ('devices', 'recovery', 'abuse', 'outbox', 'entitlements',
                'history-control', 'history-content', 'authority', 'tokens')
CASES = ('handler_all_components', 'handler_all_components_lost_ack',
         'handler_all_components_unknown_recovery', 'handler_all_components_history_retry')


def _account_modules():
    root = Path(__file__).resolve().parent
    src = root / '_qualification_account'
    if not src.exists():
        src = root.parents[1] / 'src' / 'account_data_api'
    old = {name: sys.modules.get(name) for name in ('errors', 'service')}
    try:
        values = []
        for name in ('errors', 'service', 'lifecycle'):
            spec = importlib.util.spec_from_file_location('_qualification_account_' + name, src / (name + '.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            values.append(module)
            if name in old: sys.modules[name] = module
        return values[1:]
    finally:
        for name, value in old.items():
            if value is None: sys.modules.pop(name, None)
            else: sys.modules[name] = value


class Interrupted(Exception):
    pass


class Resources:
    """Real SDK resources, with the runner's per-call time and table boundaries."""
    def __init__(self, runner):
        self.r = runner
        self.after = lambda name, kw: None
        self.resource = boto3.resource('dynamodb', region_name='us-east-1',
            config=Config(connect_timeout=2, read_timeout=3, retries={'total_max_attempts': 1}))
        self.meta = SimpleNamespace(client=self.Client(self, self.resource.meta.client))
        self.raw = self.Client(self, runner.d)

    class Client:
        def __init__(self, owner, client): self.o, self.c, self.meta = owner, client, client.meta
        def __getattr__(self, name):
            def call(**kw):
                tables = [kw['TableName']] if 'TableName' in kw else []
                for action in kw.get('TransactItems', []):
                    tables.append(next(iter(action.values()))['TableName'])
                self.o.check(tables)
                result = self.o.r.call(getattr(self.c, name), **kw)
                self.o.after(name, kw)
                return result
            return call

    def check(self, names):
        if any(name not in self.r.tables.values() for name in names):
            raise ValueError('FIXTURE_RESOURCE_INVALID')

    def Table(self, name):
        self.check([name]); owner = self; table = self.resource.Table(name)
        class Table:
            def __getattr__(self, method):
                def call(**kw):
                    result = owner.r.call(getattr(table, method), **kw)
                    owner.after(method, {'TableName':name, **kw})
                    return result
                return call
        return Table()

    def close(self): self.resource.meta.client.close()


class Identity:
    def __init__(self, subject): self.subject, self.calls, self.present = subject, [], True
    def _check(self, kw):
        if kw != {'UserPoolId': 'us-east-1_Synthetic', 'Username': self.subject}:
            raise ValueError('FIXTURE_IDENTITY_INVALID')
    def admin_user_global_sign_out(self, **kw): self._check(kw); self.calls.append('signout')
    def admin_get_user(self, **kw):
        self._check(kw); self.calls.append('get')
        if not self.present:
            from botocore.exceptions import ClientError
            raise ClientError({'Error': {'Code': 'UserNotFoundException'}}, 'AdminGetUser')
        return {'Username': self.subject, 'UserAttributes': [{'Name': 'sub', 'Value': self.subject}]}
    def admin_delete_user(self, **kw):
        self._check(kw); self.calls.append('delete'); self.present = False


def run(r, campaign_app, stream, case, require):
    from shared_account_finalization.service import Finalizer, FinalizationError, REQUIRED_COMPONENTS
    from shared_purchase_ownership import OwnershipStore
    from shared_purchase_ownership.service import owner_key, locator_item
    from shared_check_authority.deletion import AuthorityDeletion
    from shared_check_authority import purchase_usage as usage
    from shared_play_lifecycle.deletion import TokenDeletion
    from shared_play_lifecycle.bindings import reverse_key, locator_key
    from v1_play_handoff.service import account_binding
    from history_account_deletion_bridge.service import start_history_deletion
    from history_lifecycle.service import HistoryLifecycleService
    service, lifecycle_module = _account_modules()
    resources = Resources(r)
    try:
        table = lambda kind: resources.Table(r.tables[kind])
        ledger, users = table('ledger'), table('users')
        now, account, pk = r.now, r.subject, 'USER#' + r.subject
        sha = hashlib.sha256(account.encode()).hexdigest()
        identity = Identity(account)
        r.put('ledger', {'PK':'INVENTORY#dev','SK':'ACCOUNT_DATA_INVENTORY','recordType':'ACCOUNT_DATA_INVENTORY',
            'schemaVersion':1,'revision':1,'environment':'dev','coverage':'VERIFIED_COMPLETE','manifestSha256':'d'*64,
            'requiredComponents':list(REQUIRED_COMPONENTS),'usernameIsSubVerified':True,'approvedAtEpoch':r.when-1})
        r.put('users', r.get('users',pk,'PROFILE') | {'deletionRequestedAtEpoch':r.when})
        # Sentinels use valid unrelated partitions in every store and must survive byte-for-byte.
        sentinels = {}
        for kind in EXTRA_TABLES + ('users',):
            row = {'PK':'USER#synthetic-other','SK':'PRESERVE','synthetic':True}
            r.put(kind,row); sentinels[kind]=row
        r.put('devices',{'PK':pk,'SK':'ACTIVE_BINDING','deviceId':'synthetic'})
        for i in range(101): r.put('devices',{'PK':pk,'SK':f'DEVICE#{i:03d}','synthetic':True})
        r.put('recovery', {'PK':pk,'SK':'RATE#'+str(now),'requestCount':1,'expiresAt':now+100})
        recovery = {'PK':pk,'SK':'RECOVERY#'+r.cmd['operationId'],'recordType':'DEVICE_RECOVERY_RECEIPT','schemaVersion':1,
            'operationId':r.cmd['operationId'],'operation':'REPLACE_ACTIVE_BINDING','payloadHash':'a'*64,
            'result':'RECOVERED','status':'COMPLETE','bindingFingerprint':'synthetic-sensitive',
            'completedAtEpoch':now-10,'expiresAt':now-10+7*86400}
        r.put('recovery', recovery)
        audit={'PK':pk,'SK':f'AUDIT#{now}#'+r.cmd['operationId'],'recordType':'DEVICE_RECOVERY_AUDIT','schemaVersion':1,
            'operationId':r.cmd['operationId'],'actorType':'SELF','action':'REPLACE_ACTIVE_BINDING','result':'RECOVERED',
            'bindingFingerprint':'synthetic-sensitive','occurredAtEpoch':now,'expiresAt':now+90*86400}
        r.put('recovery',audit)
        consent={'PK':pk,'SK':f'CAMPAIGN_CONSENT#AUDIT#{r.when}#'+r.cmd['operationId'],'schemaVersion':1,'recordVersion':1,
            'eventType':'campaign.participation.withdrawal_completed','occurredAt':str(r.when),'consentEpochId':r.cmd['operationId'],
            'operationId':r.cmd['operationId'],'resultingState':'withdrawn','expiresAt':r.when+400*86400}
        r.put('users',consent)
        if case.endswith('unknown_recovery'): r.put('recovery',{'PK':pk,'SK':'UNKNOWN','synthetic':True})
        for family in ('RATE','SCAN_RATE','CONSUMPTION'):
            r.put('abuse',{'PK':f'ANALYSIS#{family}#{sha}','SK':'synthetic','expiresAt':now+900})
        request_pk=f'ANALYSIS#REQUEST#{sha}'
        r.put('abuse',{'PK':request_pk,'SK':'synthetic','status':'COMPLETED','payloadHash':'a'*64,
            'response':{'summary':'synthetic-sensitive'},'expiresAt':now+500,'ttl':now+500})
        eid='77777777-7777-4777-8777-777777777777'
        r.put('outbox',{'PK':'ACCOUNT#'+sha,'SK':'OUTBOX#'+eid,'recordType':'CAMPAIGN_OUTBOX_LOCATOR',
            'schemaVersion':1,'recordVersion':1,'environment':'dev','accountIdHash':sha,'statisticsEventId':eid,
            'eventPK':'EVENT#'+eid,'eventSK':'OBSERVATION_READY','eventExpiresAt':now+500,'expiresAt':now+500+86400})
        r.put('outbox',{'PK':'EVENT#'+eid,'SK':'OBSERVATION_READY','statisticsEventId':eid,
            'accountId':account,'environment':'dev','sanitizedText':'synthetic-sensitive'})
        r.put('entitlements',{'PK':'PURCHASE#CONTROL','SK':'OWNERSHIP_INVENTORY','recordType':'PURCHASE_OWNERSHIP_INVENTORY',
            'schemaVersion':1,'revision':1,'coverage':'VERIFIED_COMPLETE','environment':'dev'})
        r.put('entitlements',{'PK':pk,'SK':'ENTITLEMENT','subscriptionStatus':'expired','monthlyScansRemaining':3})
        owner_digest=hashlib.sha256(b'synthetic-owned-purchase').hexdigest()
        r.put('entitlements',locator_item(account,owner_digest))
        r.put('entitlements',owner_key(owner_digest)|{'recordType':'PURCHASE_OWNERSHIP','schemaVersion':1,'revision':1,
            'accountId':account,'purchaseTokenHash':owner_digest,'platform':'google_play','productId':'synthetic-product','verifiedAtEpoch':r.when})
        finalizer=Finalizer(ledger_table=ledger,ledger_table_name=r.tables['ledger'],client=resources.raw,cognito=identity,
            user_pool_id='us-east-1_Synthetic',environment='dev',now=lambda:int(time.time()),enabled=True,manifest_sha256='d'*64,inventory_revision=1)
        ownership=OwnershipStore(table=table('entitlements'),ledger=ledger,users_table_name=r.tables['users'],
            table_name=r.tables['entitlements'],ledger_table_name=r.tables['ledger'],client=resources.raw,environment='dev',now=lambda:now)
        lifecycle=lifecycle_module.Lifecycle(ledger=ledger,ledger_name=r.tables['ledger'],client=resources.raw,ownership=ownership,
            finalizer=finalizer,environment='dev',manifest_sha256='d'*64,inventory_revision=1,now=lambda:now)
        def blocked():
            try: finalizer.finalize(r.cmd)
            except FinalizationError: pass
            else: raise ValueError('EARLY_IDENTITY_COMPLETION')
            require('delete' not in identity.calls)
        def drain(fn, limit=20):
            for _ in range(limit):
                result=fn()
                if result['complete']:return result
            raise ValueError('FIXTURE_BOUNDED_DRAIN_UNFINISHED')
        def receipt(component): return r.get('ledger',r.cmd['PK'],'ACCOUNT_DELETION#'+component)
        blocked()
        if case.endswith('lost_ack'):
            fired=[]
            def lost_ack(name, kw):
                if not fired and name=='put_item' and kw.get('Item',{}).get('component')=='SESSION_REVOCATION':
                    fired.append(True);raise Interrupted('SYNTHETIC_ACK_LOSS')
            resources.after=lost_ack
            try: service.ensure_session_revoked(r.cmd,user_pool_id='us-east-1_Synthetic',cognito=identity,ledger_table=ledger,now_epoch=now)
            except Interrupted:pass
            else:raise ValueError('EXPECTED_ACK_LOSS')
            require(fired==[True] and receipt('SESSION_REVOCATION') is not None)
            resources.after=lambda name,kw:None
        require(service.ensure_session_revoked(r.cmd,user_pool_id='us-east-1_Synthetic',cognito=identity,ledger_table=ledger,now_epoch=now))
        before=receipt('SESSION_REVOCATION')
        require(service.ensure_session_revoked(r.cmd,user_pool_id='us-east-1_Synthetic',cognito=identity,ledger_table=ledger,now_epoch=now+1))
        require(identity.calls==['signout'] and receipt('SESSION_REVOCATION')==before)
        first=service.delete_device_bindings(r.cmd,device_table=table('devices'),ledger_table=ledger,now_epoch=now)
        require(not first['complete'] and first['deleted']==100 and receipt('DEVICE_BINDINGS') is None)
        drain(lambda:service.delete_device_bindings(r.cmd,device_table=table('devices'),ledger_table=ledger,now_epoch=now))
        require(not table('devices').query(KeyConditionExpression='PK = :p',ExpressionAttributeValues={':p':pk},ConsistentRead=True)['Items'])
        if case.endswith('unknown_recovery'):
            try:service.delete_device_recovery_control(r.cmd,recovery_table=table('recovery'),ledger_table=ledger,now_epoch=now)
            except ValueError:pass
            else:raise ValueError('UNKNOWN_RECOVERY_ACCEPTED')
            require(receipt('DEVICE_RECOVERY') is None and r.get('recovery',pk,'UNKNOWN') is not None)
            blocked()
            for kind,row in sentinels.items():require(r.get(kind,row['PK'],row['SK'])==row)
            return
        drain(lambda:service.delete_device_recovery_control(r.cmd,recovery_table=table('recovery'),ledger_table=ledger,now_epoch=now))
        minimal=r.get('recovery',pk,recovery['SK'])
        require('bindingFingerprint' not in minimal and minimal['expiresAt']==recovery['expiresAt'])
        minimal_audit=r.get('recovery',pk,audit['SK'])
        require('bindingFingerprint' not in minimal_audit and minimal_audit['expiresAt']==audit['expiresAt'])
        # Start actual bridge, then drain each bounded lifecycle stage on strong refreshed jobs.
        r.put('history-control',{'PK':pk,'SK':'STATE','accountStatus':'ACTIVE','historyGeneration':2,
            'recognitionGeneration':0,'acceptedSequence':2})
        for generation in range(3):
            for i in range(26):
                request=f'g{generation}-r{i:03d}'; content_sk=f'COMPLETE#{now:013d}#{request}'
                r.put('history-content',{'PK':pk+f'#HISTORY#{generation}','SK':content_sk,'assessment':{'summary':'synthetic-sensitive'}})
                r.put('history-control',{'PK':pk,'SK':'REQUEST#'+request,'historyGeneration':generation,
                    'contentSortKey':content_sk,'status':'ACTIVE','expiresAt':r.when+120*86400})
        history_args=dict(control_table=table('history-control'),control_table_name=r.tables['history-control'],
            deletion_ledger_table=ledger,deletion_ledger_table_name=r.tables['ledger'],dynamodb_client=resources.raw,
            schema_version=1,erasure_sla_hours=24,now_epoch=now)
        require(start_history_deletion(r.cmd,**history_args)['started'])
        require(start_history_deletion(r.cmd,**history_args)['alreadyPending'])
        settings=SimpleNamespace(environment='dev',schema_version=1,control_table_name=r.tables['history-control'],
            deletion_ledger_table_name=r.tables['ledger'],erasure_batch_size=25,dedup_retention_days=120,
            mutation_retention_days=120,completion_recheck_seconds=60)
        history=HistoryLifecycleService(settings=settings,content_table=table('history-content'),
            control_table=table('history-control'),abuse_table=table('abuse'),deletion_ledger_table=ledger,
            dynamodb_client=resources.raw,now=lambda:now)
        jobs=table('history-control').query(KeyConditionExpression='PK = :p AND begins_with(SK, :s)',
            ExpressionAttributeValues={':p':pk,':s':'ERASURE#'},ConsistentRead=True)['Items']
        require(len(jobs)==1);job_key={k:jobs[0][k] for k in ('PK','SK')}
        interrupted=[]
        if case.endswith('history_retry'):
            def interrupt_stage(name, kw):
                if not interrupted and name=='update_item' and kw.get('Key',{}).get('SK','').startswith('ERASURE#'):
                    interrupted.append(True);raise Interrupted('SYNTHETIC_ACK_LOSS')
            resources.after=interrupt_stage
        for iteration in range(20):
            job=table('history-control').get_item(Key=job_key,ConsistentRead=True)['Item']
            try:complete,_=history._process_erasure_job(job,now)
            except Interrupted:
                require(case.endswith('history_retry') and receipt('HISTORY') is None)
                resources.after=lambda name,kw:None
                continue
            if complete:break
            require(receipt('HISTORY') is None)
        else:raise ValueError('HISTORY_DRAIN_UNFINISHED')
        require(iteration>=6 and receipt('HISTORY') is not None)
        require(bool(interrupted)==case.endswith('history_retry'))
        resources.after=lambda name,kw:None
        for generation in range(3):require(not table('history-content').query(KeyConditionExpression='PK = :p',
            ExpressionAttributeValues={':p':pk+f'#HISTORY#{generation}'},ConsistentRead=True)['Items'])
        history_replay=r.get('abuse',request_pk,'synthetic')
        require('response' not in history_replay and history_replay['expiresAt']==r.when+120*86400)
        require(table('history-control').get_item(Key=job_key,ConsistentRead=True)['Item']['expiresAt']==now+120*86400)
        for generation in range(3):
            for i in range(26):
                locator=r.get('history-control',pk,f'REQUEST#g{generation}-r{i:03d}')
                require(locator['status']=='CLEARED' and 'contentSortKey' not in locator
                    and locator['expiresAt']==r.when+120*86400)
        drain(lambda:service.delete_analysis_abuse_control(r.cmd,abuse_table=table('abuse'),ledger_table=ledger,now_epoch=now,
            request_dedupe_policy_status='approved',legacy_request_retention_policy_status='approved',consumption_deletion_policy_status='approved'))
        drain(lambda:service.delete_campaign_outbox(r.cmd,outbox_table=table('outbox'),ledger_table=ledger,now_epoch=now,locator_coverage_status='approved'))
        require(r.get('outbox','EVENT#'+eid,'OBSERVATION_READY') is None)
        require(not lifecycle.entitlements(r.cmd)['complete']);require(lifecycle.entitlements(r.cmd)['complete'])
        require(r.get('entitlements',owner_key(owner_digest)['PK'],'IDEMPOTENCY') is None)
        # Synthetic paid accounting evidence only: no store purchase or funded proof claim.
        keyring={'k1':b'synthetic-key-material-for-fixture-000000'}
        r.put('authority',{'PK':'V1#CONTROL','SK':'HMAC_KEY_INVENTORY','recordType':'V1_HMAC_KEY_INVENTORY',
            'schemaVersion':1,'revision':1,'coverage':'VERIFIED_COMPLETE','issuedKeys':{k:hashlib.sha256(v).hexdigest() for k,v in keyring.items()}})
        args=dict(authority_table=r.tables['authority'],ledger_table=r.tables['ledger'],environment='dev',keyring=keyring,
            receipt_retention_seconds=120*86400,now=lambda:int(time.time()))
        authority=AuthorityDeletion(resources,**args);partition=authority._partition(account,'k1')
        other_partition=authority._partition('synthetic-other','k1')
        for kind in ('authority','tokens'):
            other={'PK':other_partition,'SK':'ACCESS' if kind=='authority' else 'PLAY_BINDING','synthetic':True}
            r.put(kind,other);sentinels[kind]=other
        pointer=usage.key('a'*64,'b'*64);global_usage=usage.new_period_usage(pointer,now-100,now+1000)|{'usedChecks':7,'reservedChecks':1}
        r.put('authority',global_usage)
        period={'PK':partition,'SK':'PERIOD#synthetic','recordType':'V1_ALLOWANCE_PERIOD','policyVersion':usage.OWNER_POLICY,
            'grantRevision':1,'limit':200,'usedChecks':7,'reservedChecks':1,'startEpoch':now-100,'endEpoch':now+1000,'purchaseUsageKey':pointer}
        r.put('authority',period)
        r.put('authority',{'PK':partition,'SK':'ACCESS','recordType':'V1_ACCESS_AUTHORITY','state':'ACTIVE'})
        r.put('authority',{'PK':partition,'SK':'CHECK#synthetic','recordType':'V1_CHECK_RECEIPT','policyVersion':usage.OWNER_POLICY,
            'basis':'paid','state':'ADMITTED','periodSK':period['SK'],'grantRevision':1,'purchaseUsageKey':pointer})
        r.put('authority',{'PK':partition,'SK':'INFLIGHT','activeCount':1})
        drain(lambda:authority.delete_batch(r.cmd,page_size=1,max_pages=1),limit=12)
        require(r.get('authority',pointer['PK'],pointer['SK'])==global_usage|{'reservedChecks':0})
        # Token + reverse binding pair, generated fixture ciphertext is never decrypted.
        binding=account_binding(account);digest=hashlib.sha256(b'synthetic-token').hexdigest()
        r.put('tokens',{'PK':partition,'SK':'PLAY_TOKEN#'+digest,'recordType':'V1_PLAY_TOKEN','schemaVersion':2,
            'tokenDigest':digest,'kmsKeyArn':r.config['key'],'ciphertext':base64.b64encode(b'synthetic-ciphertext-placeholder-000000').decode(),
            'wrappedDataKey':base64.b64encode(b'synthetic-wrapped-key').decode(),'verifiedAccessUntilEpoch':now+1000,
            'expiresAt':now+1000+604800,'nextAttemptAtEpoch':now,'revision':1,'GSI1PK':'V1_PLAY_RECONCILE',
            'GSI1SK':f'{now:012d}#{partition}#PLAY_TOKEN#{digest}','attemptCount':0,'acknowledgment':'pending','lastOutcome':'verified'})
        r.put('tokens',reverse_key(binding)|{'recordType':'PLAY_ACCOUNT_BINDING','schemaVersion':1,'accountId':account,
            'accountPartition':partition,'bindingHash':binding,'createdAtEpoch':now})
        r.put('tokens',locator_key(partition)|{'recordType':'PLAY_ACCOUNT_BINDING_LOCATOR','schemaVersion':1,'bindingHash':binding,'createdAtEpoch':now})
        token=TokenDeletion(resources,token_table=r.tables['tokens'],**args)
        drain(lambda:token.delete_batch(r.cmd,page_size=1,max_pages=1),limit=12)
        require(r.get('tokens',reverse_key(binding)['PK'],'ACCOUNT') is None)
        blocked()
        from shared_campaign_locators.core import locator_for_target
        candidate='11111111-1111-4111-8111-111111111111';candidate_pk='CANDIDATE#'+candidate
        r.put('pipeline',{'PK':candidate_pk,'SK':'SUMMARY','candidateId':candidate,'periodId':r.period,'version':1,
            'expiresAt':now+1000,'GSI3PK':'EXPIRY#dev','GSI3SK':now+1000,'lifecycleState':'FROZEN',
            'lifecycleOperationId':r.cmd['operationId'],'lifecycleStartedAtEpoch':r.when-100,'lifecycleInventoryRevision':1})
        contribution={'PK':candidate_pk,'SK':'CONTRIB#'+r.token,'GSI1PK':r.partition(r.period),'GSI1SK':candidate_pk,
            'periodId':r.period,'expiresAt':now+1000,'submissionCount':1,'vectorApplied':True,'vector':[1],
            'metadataSchemaVersion':1,'lexicalFingerprint':[],'signalIds':[],'indicatorIds':[]}
        r.put('pipeline',contribution);r.put('pipeline',locator_for_target(contribution,'dev'))
        for _ in range(6):
            try:
                require(campaign_app.lambda_handler(stream,r.context)['completed']==1);break
            except RuntimeError as exc:
                require(str(exc)=='CAMPAIGN_DELETION_RECONCILIATION_REQUIRED' and receipt('CAMPAIGN') is None)
        else:raise ValueError('CAMPAIGN_DRAIN_UNFINISHED')
        require(r.get('pipeline',candidate_pk,'SUMMARY') is None)
        require(r.get('ledger',r.cmd['PK'],'CAMPAIGN_RECOVERY_CONTROL')['state']=='SEALED')
        blocked()
        drain(lambda:service.delete_user_profile_state(r.cmd,users_table=users,ledger_table=ledger,policy_status='approved',now_epoch=now))
        require(r.get('users',pk,'PROFILE') is None and r.get('users',pk,consent['SK'])==consent)
        upstream={c:receipt(c) for c in REQUIRED_COMPONENTS if c!='IDENTITY'}
        require(all(row is not None for row in upstream.values()))
        require(all(row['retainUntilEpoch']==row['occurredAtEpoch']+120*86400 for row in upstream.values()))
        require(finalizer.finalize(r.cmd)['complete'])
        require(identity.calls==['signout','get','delete'])
        require(finalizer.finalize(r.cmd)['alreadyComplete'] and identity.calls==['signout','get','delete'])
        require(authority.delete_batch(r.cmd)['alreadyComplete'] and token.delete_batch(r.cmd)['alreadyComplete'])
        require(start_history_deletion(r.cmd,**(history_args|{'now_epoch':int(time.time())}))['alreadyCompleted'])
        require(campaign_app.lambda_handler(stream,r.context)['completed']==0)
        require({c:receipt(c) for c in upstream}==upstream)
        for kind,row in sentinels.items():require(r.get(kind,row['PK'],row['SK'])==row)
    finally:
        resources.close()
