"""Disposable SDK lifecycle composition; never an application initializer.

The runner owns five fresh tagged tables and one dedicated HMAC key. Inventories,
consent/command inputs and clocks are synthetic. Producer, indexed mutations,
scheduler, retained cleanup and completion are application code. SQS and metric
transport are captured; no delivery, TTL timing, live inventory or IAM claim.
"""
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
import time
from uuid import UUID

from shared_campaign_work import configuration as C, records as R
from shared_campaign_work.transactions import TrackedClient, operation
from shared_campaign_work.scheduler import tick
from shared_campaign_work.proofs import Proofs
from shared_campaign_locators import period as P
from shared_campaign_locators.core import WRITERS as LOCATOR_WRITERS
from shared_campaign_recovery import records as REC
from shared_research_consent import CURRENT_NOTICE, CURRENT_POLICY

ACCOUNT='107827791950'
REGION='us-east-1'
CASES=('complete_replay','poison_recovery','lost_ack','retire_then_complete')
FAMILIES=('pipeline','outbox','users','ledger','intelligence')


def need(value):
    if not value:raise RuntimeError('PERIOD_LIFECYCLE_QUALIFICATION_FAILED')


@contextmanager
def pins(values):
    previous={k:os.environ.get(k) for k in values};os.environ.update(values)
    try:yield
    finally:
        for k,v in previous.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v


@contextmanager
def worker(folder):
    """Keep legacy unqualified module aliases private to this fixture import."""
    directory=Path(__file__).resolve().parents[2]/'src'/folder
    need(directory.is_dir())
    aliases={p.stem for p in directory.glob('*.py')}
    old={name:sys.modules.get(name) for name in aliases};path=list(sys.path)
    try:
        for name in aliases:sys.modules.pop(name,None)
        sys.path.insert(0,str(directory))
        yield lambda name:importlib.import_module(name)
    finally:
        sys.path[:]=path
        for name,value in old.items():
            if value is None:sys.modules.pop(name,None)
            else:sys.modules[name]=value


def run(client,kms,*,run_id,tables,key_arn,case,now=time.time,remaining_ms=lambda:60000):
    need(type(run_id) is str and re.fullmatch('[0-9a-f]{12}',run_id) and case in CASES)
    prefix='amt-campaign-lifecycle-qual-'+run_id
    need(type(tables) is dict and tables=={f:prefix+'-'+f for f in FAMILIES})
    need(type(key_arn) is str and re.fullmatch(rf'arn:aws:kms:{REGION}:{ACCOUNT}:key/[0-9a-f]{{8}}(?:-[0-9a-f]{{4}}){{3}}-[0-9a-f]{{12}}',key_arn))
    def call(fn,**kw):need(remaining_ms()>=6000);return fn(**kw)
    d=C.BudgetClient(client,remaining_ms)
    def rows(family):
        page=d.scan(TableName=tables[family],ConsistentRead=True,Limit=500)
        need(type(page.get('Items')) is list and not page.get('LastEvaluatedKey'))
        return sorted([C.plain(row) for row in page['Items']],key=lambda r:(r['PK'],r['SK']))
    def snapshot():return {f:rows(f) for f in FAMILIES}
    def get(family,pk,sk):
        raw=d.get_item(TableName=tables[family],Key=C.wire({'PK':pk,'SK':sk}),ConsistentRead=True).get('Item')
        return C.plain(raw) if raw else None
    def put(family,row,before=None):
        d.transact_write_items(TransactItems=[operation('Put',tables[family],row,observed=before)])
    real_now=int(now());period=real_now//P.PERIOD_SECONDS-2;need(period>0)
    tags={'Project':'trustcheckradar','Environment':'dev','Purpose':'campaign-lifecycle-qualification','QualificationRunId':run_id}
    resources={}
    for family in FAMILIES:
        table=tables[family];info=d.describe_table(TableName=table).get('Table')
        need(type(info) is dict and info.get('TableName')==table and info.get('TableStatus')=='ACTIVE'
            and info.get('TableArn')==f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{table}'
            and info.get('KeySchema')==[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}]
            and not info.get('LocalSecondaryIndexes'))
        attrs={a['AttributeName']:a['AttributeType'] for a in info.get('AttributeDefinitions',[])}
        expected_attrs={'PK':'S','SK':'S'}
        if family=='pipeline':expected_attrs|={'GSI2PK':'S','GSI2SK':'S'}
        elif family=='intelligence':expected_attrs|={'expiryPartition':'S','expiresAt':'N','GSI1PK':'S','GSI1SK':'S'}
        need(attrs==expected_attrs)
        indexes=info.get('GlobalSecondaryIndexes',[])
        if family=='pipeline':
            need(len(indexes)==1 and indexes[0].get('IndexName')=='CandidateBucketIndex'
                 and indexes[0].get('IndexStatus')=='ACTIVE' and indexes[0].get('Projection')=={'ProjectionType':'ALL'}
                 and indexes[0].get('KeySchema')==[{'AttributeName':'GSI2PK','KeyType':'HASH'},{'AttributeName':'GSI2SK','KeyType':'RANGE'}])
        elif family=='intelligence':
            by_name={i['IndexName']:i for i in indexes};need(set(by_name)=={'ExpirationIndex','PublicationIndex'} and len(indexes)==2)
            for name,hash_key,range_key,projection in [('ExpirationIndex','expiryPartition','expiresAt','KEYS_ONLY'),('PublicationIndex','GSI1PK','GSI1SK','ALL')]:
                index=by_name[name];need(index.get('IndexStatus')=='ACTIVE' and index.get('Projection')=={'ProjectionType':projection}
                    and index.get('KeySchema')==[{'AttributeName':hash_key,'KeyType':'HASH'},{'AttributeName':range_key,'KeyType':'RANGE'}])
        else:need(not indexes)
        table_id=info.get('TableId')
        try:need(type(table_id) is str and str(UUID(table_id))==table_id)
        except (ValueError,TypeError,AttributeError):need(False)
        actual=d.list_tags_of_resource(ResourceArn=info['TableArn'])
        need(not actual.get('NextToken') and len(actual.get('Tags',[]))==4
             and {t['Key']:t['Value'] for t in actual['Tags']}==tags)
        if family in ('pipeline','outbox'):resources[family]={'tableName':table,'tableId':table_id}
        if family=='intelligence':intelligence_id=table_id
    metadata=call(kms.describe_key,KeyId=key_arn).get('KeyMetadata',{})
    need(metadata.get('Arn')==key_arn and metadata.get('KeyId')==key_arn.rsplit('/',1)[1]
         and metadata.get('Description')==prefix and metadata.get('KeyState')=='Enabled' and metadata.get('Enabled') is True
         and metadata.get('KeyManager')=='CUSTOMER' and metadata.get('Origin')=='AWS_KMS'
         and metadata.get('KeySpec')=='HMAC_256' and metadata.get('KeyUsage')=='GENERATE_VERIFY_MAC' and metadata.get('MultiRegion') is False)
    keytags=call(kms.list_resource_tags,KeyId=key_arn)
    need(not keytags.get('Truncated') and len(keytags.get('Tags',[]))==4
         and {t['TagKey']:t['TagValue'] for t in keytags['Tags']}=={'Project':'trustcheckradar','Environment':'dev',
             'Purpose':'campaign-contributor-token','PeriodId':str(period)})
    need(all(not value for value in snapshot().values()))
    start=period*P.PERIOD_SECONDS+100;clock=[start]
    generation=str(UUID(hex='00000000000040008000'+run_id))
    env={'APP_ENVIRONMENT':'dev','AWS_REGION':REGION,'CAMPAIGN_PERIOD_ADMISSION_ACCOUNT_ID':ACCOUNT,
         'CAMPAIGN_PERIOD_ADMISSION_ENABLED':'true','CAMPAIGN_PERIOD_ADMISSION_GENERATION':generation,
         'CAMPAIGN_PERIOD_WORK_ENABLED':'true','CAMPAIGN_PERIOD_WORK_MANIFEST_SHA256':'d'*64,
         'CAMPAIGN_PERIOD_WORK_INVENTORY_REVISION':'1','CAMPAIGN_PERIOD_LIFECYCLE_ENABLED':'true',
         'CAMPAIGN_PERIOD_RETIREMENT_ENABLED':'false','INTELLIGENCE_TABLE_NAME':tables['intelligence'],
         'CAMPAIGN_AGGREGATE_TABLE_ID':intelligence_id}
    for family,resource in resources.items():
        env['CAMPAIGN_PERIOD_WORK_'+family.upper()+'_TABLE_NAME']=resource['tableName']
        env['CAMPAIGN_PERIOD_WORK_'+family.upper()+'_TABLE_ID']=resource['tableId']
    marker={'PK':'INVENTORY#dev','SK':'CAMPAIGN_PERIOD_WORK','recordType':'CAMPAIGN_PERIOD_WORK_INVENTORY',
        'schemaVersion':1,'environment':'dev','coverage':'VERIFIED_COMPLETE','revision':1,'manifestSha256':'d'*64,
        'approvedAtEpoch':start-1,'admissionGeneration':generation,'locatorManifestSha256':'a'*64,
        'locatorInventoryRevision':1,'minimumPeriodId':period,'resources':resources,'writers':C.WRITERS,
        'baseline':'EXACT_ALL_TARGETS_INDEXED','restoreInvalidation':'REQUIRES_NEW_GENERATION'}
    registry={'PK':f'PERIOD#{period}','SK':'HMAC_KEY','periodId':period,'keyArn':key_arn,'status':'ENABLED',
        'retireAfterEpoch':(period+1)*P.PERIOD_SECONDS+P.RECOVERY_SECONDS,
        'admissionSchemaVersion':2,'admissionGeneration':generation,'admissionState':'OPEN',
        'admissionRevision':1,'admissionManifestSha256':'a'*64,'admissionInventoryRevision':1,
        'admissionChangedAtEpoch':start-1,'workSchemaVersion':1,'workManifestSha256':'d'*64,'workInventoryRevision':1}
    control=R.control_key(period)|{'recordType':'CAMPAIGN_PERIOD_WORK_CONTROL','schemaVersion':1,
        'environment':'dev','generation':generation,'periodId':period,'manifestSha256':'d'*64,
        'inventoryRevision':1,'revision':1,'nextOrdinal':1,'pendingCount':0,'passRevision':0,
        'passCursor':0,'passHighWater':0,'lastProgressAtEpoch':0,'lastFullPassAtEpoch':0}
    locator={'PK':'INVENTORY#dev','SK':'CAMPAIGN_LOCATORS','recordType':'CAMPAIGN_LOCATOR_INVENTORY','schemaVersion':1,
        'revision':1,'environment':'dev','coverage':'VERIFIED_COMPLETE','manifestSha256':'a'*64,'approvedAtEpoch':start-1,
        'locatorSchemaVersion':1,'minimumPeriodId':period,'priorPeriodsErased':True,'writers':LOCATOR_WRITERS}
    preserved={'PK':'UNRELATED#qualification','SK':'KEEP','value':'untouched'}
    for row in (marker,registry,control,locator,preserved):put('pipeline',row)
    metric_calls=[];sent=[];mac_calls=[];later_mac=[];retirement_mutations=[]
    retirement_result=None;retirement_replay=False;retirement_mac_rejected=False
    class ProducerKms:
        def generate_mac(self,**kw):
            need(kw.get('KeyId')==key_arn and kw.get('MacAlgorithm')=='HMAC_SHA_256')
            need(kw.get('Message') in [b'campaign-contributor:v1\0'+(prefix+'-'+a).encode() for a in ('account','withdrawal')])
            mac_calls.append(1);return call(kms.generate_mac,**kw)
    class NoKms:
        def __getattr__(self,name):
            def denied(**kw):later_mac.append(name);raise RuntimeError('NO_KMS_AFTER_SEAL')
            return denied
    class Queue:
        def send_message(self,**kw):sent.append(kw);return {}
    class Metrics:
        def put_metric_data(self,**kw):metric_calls.append(kw);return {}
    with pins(env),worker('campaign_observation_publisher') as load:
        publisher=load('service')
    with worker('campaign_cluster_aggregator') as load:cluster=load('service')
    with worker('campaign_deletion_bridge') as load:
        completion=load('completion');retained=load('retained_periods')
    with pins(env):
        events=[];subjects={kind:prefix+'-'+kind for kind in ('account','withdrawal')}
        epochs={kind:str(UUID(int=(4<<76)|(2<<62)|i)) for i,kind in enumerate(subjects,1)}
        for kind,subject in subjects.items():
            put('users',{'PK':'USER#'+subject,'SK':'PROFILE','sub':subject,'status':'ACTIVE'})
            put('users',{'PK':'USER#'+subject,'SK':'CAMPAIGN_PARTICIPATION','state':'enrolled',
                'consentEpochId':epochs[kind],'environment':'dev','noticeVersion':CURRENT_NOTICE,'policyVersion':CURRENT_POLICY})
        for index in range(6):
            kind='account' if index%2==0 else 'withdrawal';subject=subjects[kind]
            event=str(UUID(int=(4<<76)|(2<<62)|(100+index)))
            observation={'schemaVersion':1,'recordVersion':1,'environment':'dev','statisticsEventId':event,
                'accountId':subject,'campaignConsentGranted':True,'consentEpochId':epochs[kind],
                'noticeVersion':CURRENT_NOTICE,'observedAtEpoch':start-1,'expiresAt':start+1000,
                'appFeatures':{'schemaVersion':1,'extractorVersion':'android-1.0.0','languageId':'en',
                    'taxonomyBucket':'advance_fee','vector':[1.0,0.0],'lexicalFingerprint':['0123456789abcdef'],
                    'signalIds':['payment_request'],'indicatorIds':['payment.crypto'],'confidence':0.9}}
            row=json.loads(json.dumps(observation|{'PK':'EVENT#'+event,'SK':'OBSERVATION_READY','eventType':'campaign.observation.ready'}),parse_float=Decimal)
            hashed=hashlib.sha256(subject.encode()).hexdigest()
            outloc={'PK':'ACCOUNT#'+hashed,'SK':'OUTBOX#'+event,'recordType':'CAMPAIGN_OUTBOX_LOCATOR',
                'schemaVersion':2,'recordVersion':1,'environment':'dev','accountIdHash':hashed,'statisticsEventId':event,
                'eventPK':row['PK'],'eventSK':row['SK'],'eventExpiresAt':row['expiresAt'],'logicalExpiresAt':row['expiresAt']+86400}
            TrackedClient(d,registries=[registry],now=lambda:clock[0],remaining_ms=remaining_ms).transact_write_items(
                TransactItems=[operation('Put',tables['outbox'],item) for item in (row,outloc)])
            need(publisher.publish_observation(observation,pipeline_table_name=tables['pipeline'],users_table_name=tables['users'],
                deletion_ledger_table_name=tables['ledger'],cluster_queue_url='synthetic',hmac_key_id=key_arn,transient_retention_days=21,
                dynamodb_client=d,kms_client=ProducerKms(),sqs_client=Queue(),now_epoch=clock[0],locator_manifest_sha256='a'*64,
                locator_inventory_revision=1,remaining_ms=remaining_ms)=='published')
            outcome=cluster.process_message(sent[-1]['MessageBody'],environment='dev',schema_version=1,table_name=tables['pipeline'],
                retention_days=21,max_submissions=3,dynamodb=d,now_epoch=clock[0],locator_manifest_sha256='a'*64,locator_inventory_revision=1,
                users_table_name=tables['users'],deletion_ledger_table_name=tables['ledger'],outbox_table_name=tables['outbox'],remaining_ms=remaining_ms)
            need(outcome in ('candidate-created','matched','counted-repeat'))
            events.append(observation)
        works=[row for row in rows('pipeline') if row['PK']==R.work_key(period,1)['PK']]
        targets=[row for row in rows('pipeline') if row['PK'].startswith(('EVENT#','CONTRIB#','CANDIDATE#','BUCKET#'))]
        need(len(works)==len(targets)+len(rows('outbox')) and len(works)>16)
        for work in works:
            lookup=get('pipeline',R.lookup_key(work['targetTableFamily'],work['targetPK'],work['targetSK'])['PK'],'RECORD')
            R.validate_pair(work,lookup,'dev',generation)
        # Expired observations erase together with their later logical locators,
        # while unrelated future pipeline evidence remains until its own bound.
        clock[0]=start+1001;early_ticks=0
        while rows('outbox') and early_ticks<12:
            tick(d,NoKms(),Metrics(),now=lambda:clock[0],remaining_ms=remaining_ms);early_ticks+=1
        need(not rows('outbox') and early_ticks>1 and get('pipeline',registry['PK'],'HMAC_KEY')['admissionState']=='OPEN')
        need(any(row['PK'].startswith('EVENT#') for row in rows('pipeline')) and not later_mac)
        recovery={'PK':'INVENTORY#dev','SK':'CAMPAIGN_RECOVERY_INVENTORY','recordType':'CAMPAIGN_RECOVERY_INVENTORY',
            'schemaVersion':1,'environment':'dev','revision':1,'coverage':'VERIFIED_COMPLETE','manifestSha256':'b'*64,
            'approvedAtEpoch':start-1,'legacyCoverageVerified':True,'writers':REC.WRITERS}
        finish_marker={'PK':'INVENTORY#dev','SK':'CAMPAIGN_COMPLETION_INVENTORY','recordType':'CAMPAIGN_COMPLETION_INVENTORY',
            'schemaVersion':1,'environment':'dev','revision':1,'coverage':'VERIFIED_COMPLETE','manifestSha256':'c'*64,
            'approvedAtEpoch':start-1,'locatorManifestSha256':'a'*64,'locatorInventoryRevision':1,
            'recoveryManifestSha256':'b'*64,'recoveryInventoryRevision':1,'invariants':completion.INVARIANTS}
        put('ledger',recovery);put('ledger',finish_marker)
        commands=[]
        for kind,subject in subjects.items():
            op=epochs[kind];at=start+500
            command={'PK':'ACCOUNT#'+subject,'SK':'ACCOUNT_DELETION','schemaVersion':1,'recordVersion':1,
                'environment':'dev','eventType':'account.deletion.requested','accountId':subject,'status':'REQUESTED',
                'occurredAtEpoch':at,'deleteByEpoch':at+86400,'operationId':op}
            profile=get('users','USER#'+subject,'PROFILE')
            if kind=='account':put('users',profile|{'status':'DELETION_REQUESTED','deletionOperationId':op},profile)
            else:
                command|={'SK':'CAMPAIGN_WITHDRAWAL#'+op,'consentEpochId':op,'eventType':'campaign.consent.withdrawn','status':'PENDING'}
                old=get('users','USER#'+subject,'CAMPAIGN_PARTICIPATION')
                state=old|{'schemaVersion':1,'recordVersion':1,'state':'withdrawal_pending','stateVersion':2,'lastOperationId':op,
                    'effectiveFrom':completion.stamp(start-1),'updatedAt':completion.stamp(at),'effectiveUntil':completion.stamp(at),
                    'withdrawalRequestedAt':completion.stamp(at),'deletionDeadlineAt':completion.stamp(at+86400)}
                put('users',state,old)
            for item in (command,REC.new_job(command),REC.new_control(command['PK'],'dev')):put('ledger',item)
            commands.append(command)
        clock[0]=registry['retireAfterEpoch']
        damaged=None
        if case=='poison_recovery':
            damaged=next(row for row in rows('pipeline') if row['PK']==R.work_key(period,1)['PK'] and row['targetSK']=='CREATION_CONTROL')
            put('pipeline',damaged|{'recordType':'POISON'},damaged)
        lost=[False]
        class Lost:
            def __getattr__(self,name):return getattr(d,name)
            def transact_write_items(self,**kw):
                result=d.transact_write_items(**kw)
                if not lost[0] and any('Delete' in a and C.plain(a['Delete']['Key'])['PK'].startswith('PERIOD_WORK#') for a in kw['TransactItems']):
                    lost[0]=True;raise RuntimeError('SYNTHETIC_LOST_ACK')
                return result
        selected=Lost() if case=='lost_ack' else d
        drain_ticks=0;poison_blocked=False
        for _ in range(20):
            value=tick(selected,NoKms(),Metrics(),now=lambda:clock[0],remaining_ms=remaining_ms);drain_ticks+=1
            current=get('pipeline',registry['PK'],'HMAC_KEY')
            if damaged is not None and get('pipeline',control['PK'],'STATE')['pendingCount']==1:
                need(current['admissionState']=='DRAINING' and value['metrics']['LifecycleWorkUnverified']>0)
                poison_blocked=True
                put('pipeline',damaged,get('pipeline',damaged['PK'],damaged['SK']));damaged=None
            if current['admissionState']=='SEALED':break
        else:need(False)
        need(drain_ticks>1 and (case!='poison_recovery' or poison_blocked) and (case!='lost_ack' or lost[0]))
        need(get('pipeline',control['PK'],'STATE')['pendingCount']==0 and not rows('outbox'))
        need(not any(row['PK'].startswith(('EVENT#','CANDIDATE#','CONTRIB#','BUCKET#','PERIOD_WORK#','WORK_LOOKUP#')) for row in rows('pipeline')))
        need(Proofs(d,now=lambda:clock[0],remaining_ms=remaining_ms).sealed(period))
        sealed=get('pipeline',registry['PK'],'HMAC_KEY')
        tick(d,NoKms(),Metrics(),now=lambda:clock[0],remaining_ms=remaining_ms)
        need(get('pipeline',registry['PK'],'HMAC_KEY')==sealed and sealed['status']=='ENABLED')
        if case=='retire_then_complete':
            # Real provider scheduling timestamps must use current wall time,
            # not the earlier synthetic deadline used to exercise the drain.
            from shared_campaign_work import retirement as K
            from botocore.exceptions import ClientError
            real_retirement_now=now()
            need(type(real_retirement_now) in (int,float) and real_retirement_now>=clock[0])
            real_retirement_now=int(real_retirement_now);clock[0]=real_retirement_now
            class RetirementKms:
                def __getattr__(self,name):
                    need(name in ('describe_key','list_resource_tags','disable_key','schedule_key_deletion'))
                    def invoke(**kw):
                        need(kw==({'KeyId':key_arn,'PendingWindowInDays':7} if name=='schedule_key_deletion' else {'KeyId':key_arn}))
                        if name in ('disable_key','schedule_key_deletion'):retirement_mutations.append(name)
                        return call(getattr(kms,name),**kw)
                    return invoke
            with pins({'CAMPAIGN_PERIOD_RETIREMENT_ENABLED':'true'}):
                retirement_result=K.retire(d,RetirementKms(),period,now=lambda:int(now()),remaining_ms=remaining_ms)
                need(retirement_result=={'state':'SCHEDULED','keyDestroyedObserved':False,'changed':True})
                retired=get('pipeline',registry['PK'],'HMAC_KEY')
                K.validate_retired(retired,C.pins(),marker,int(now()))
                need(all(retired[field]==sealed[field] for field in K.BASE_FIELDS-{'status'}))
                before_retirement_replay=snapshot()
                replay=K.retire(d,RetirementKms(),period,now=lambda:int(now()),remaining_ms=remaining_ms)
                need(replay=={'state':'SCHEDULED','keyDestroyedObserved':False,'changed':False}
                     and snapshot()==before_retirement_replay)
                retirement_replay=True
            need(retirement_mutations==['disable_key','schedule_key_deletion'])
            try:
                call(kms.generate_mac,KeyId=key_arn,MacAlgorithm='HMAC_SHA_256',Message=b'isolated-retirement-negative-probe')
            except ClientError as exc:
                need(exc.response.get('Error',{}).get('Code') in ('KMSInvalidStateException','DisabledException'))
                retirement_mac_rejected=True
            need(retirement_mac_rejected)
            clock[0]=int(now())
            need(clock[0]>=real_retirement_now)
        def candidate():return completion.Completion(dynamodb=d,kms=NoKms(),pipeline_table=tables['pipeline'],ledger_table=tables['ledger'],
            users_table=tables['users'],environment='dev',aws_account_id=ACCOUNT,aws_region=REGION,manifest_sha256='c'*64,inventory_revision=1,
            locator_manifest_sha256='a'*64,locator_inventory_revision=1,recovery_manifest_sha256='b'*64,recovery_inventory_revision=1,
            now=lambda:clock[0],remaining_ms=remaining_ms,max_periods=1)
        for command in commands:
            cleanup=retained.delete_retained_contributions(command,environment='dev',aws_account_id=ACCOUNT,aws_region=REGION,
                table_name=tables['pipeline'],deletion_ledger_table_name=tables['ledger'],retention_days=21,dynamodb=d,kms=NoKms(),
                locator_manifest_sha256='a'*64,locator_inventory_revision=1,now_epoch=clock[0],remaining_ms=remaining_ms)
            need(cleanup['reason']=='SEALED_PERIOD_RANGE_OBSERVED' and cleanup['selectedLocatorPassEnded'])
            need(candidate().complete(command)['campaignComplete'])
            after=snapshot();need(candidate().complete(command)['alreadyComplete'] and snapshot()==after)
        need(not later_mac and len(mac_calls)==6 and len(sent)==6 and get('pipeline',preserved['PK'],preserved['SK'])==preserved)
        for request in metric_calls:
            need(request['Namespace']=='TrustCheckRadar/Campaign')
            need(all(m['Dimensions']==[{'Name':'Environment','Value':'dev'}] for m in request['MetricData']))
        final=snapshot()
        for message in sent:
            need(cluster.process_message(message['MessageBody'],environment='dev',schema_version=1,table_name=tables['pipeline'],
                retention_days=21,max_submissions=3,dynamodb=d,now_epoch=clock[0],locator_manifest_sha256='a'*64,locator_inventory_revision=1,
                users_table_name=tables['users'],deletion_ledger_table_name=tables['ledger'],outbox_table_name=tables['outbox'],remaining_ms=remaining_ms)=='missing')
        need(snapshot()==final)
        # This separately seeded anonymous aggregate tests expiry composition,
        # not publication anonymity or eventual-index completeness.
        from shared_campaign_work import aggregates as A
        aggregate_id='99999999-9999-4999-8999-999999999999'
        expired={'PK':'CAMPAIGN#'+aggregate_id,'SK':'AGGREGATE','campaignId':aggregate_id,'schemaVersion':1,'taxonomyVersion':1,
            'categoryId':'other','periodWeek':'2026-W01','state':'PENDING_REVIEW','contributorCount':10,'submissionCount':10,
            'dimensionSchemaVersion':1,'languageIds':[],'tacticIds':[],'channelIds':[],'contributorCountBand':'10-24',
            'submissionCountBand':'10-24','riskBand':'high','summaryKey':'campaign.other','trendDirection':'new','expiresAt':clock[0]-1,
            'version':1,'environment':'dev','expiryPartition':A.partition('dev',aggregate_id)}
        future_id='88888888-8888-4888-8888-888888888888'
        future=expired|{'PK':'CAMPAIGN#'+future_id,'campaignId':future_id,'expiresAt':clock[0]+86400,'expiryPartition':A.partition('dev',future_id)}
        put('intelligence',expired);put('intelligence',future)
        aggregate_deleted=0
        for _ in range(8):
            outcome=A.tick(d,Metrics(),now=lambda:clock[0],remaining_ms=remaining_ms)
            need(not outcome['aggregateErasureComplete'])
            aggregate_deleted+=outcome['metrics']['AggregateWorkDeleted']
        need(aggregate_deleted==1 and rows('intelligence')==[future])
    return {'schemaVersion':1,'case':case,'passed':True,'syntheticClock':True,'syntheticInventoriesAndCommands':True,
        'productionActivation':False,'capturedTransportOnly':True,'ttlTimingQualified':False,'producerEvents':6,
        'indexedTargets':len(works),'earlyDeadlineTicks':early_ticks,'drainTicks':drain_ticks,'sealed':True,
        'accountAndWithdrawalComplete':True,'laterKmsCallCount':len(later_mac),'retirementEnabled':case=='retire_then_complete',
        'syntheticAggregateExpired':True,'futureAggregatePreserved':True,'aggregateDiscoveryCompletenessClaim':False,
        'poisonRefusedSeal':poison_blocked,'injectedLostAcknowledgmentCount':int(lost[0]),'completionReplayUnchanged':True,
        **({'retirementState':retirement_result['state'],'retirementMutationCount':len(retirement_mutations),
            'retirementReplayUnchanged':retirement_replay,'retirementMacRejected':retirement_mac_rejected,
            'computedSealPreserved':True,'keyDestroyedObserved':False} if retirement_result is not None else {})}
