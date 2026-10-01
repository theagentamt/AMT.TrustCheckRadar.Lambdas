"""Dedicated synthetic AWS retirement qualification; never an application entrypoint.

Root provisions the exact two empty fixture tables and a separate period HMAC
key. This module never creates, enables, retags, cancels, or cleans up resources.
The empty sealed proof is synthetic; this does not qualify production inventory.
"""
from contextlib import contextmanager
from datetime import datetime
import os
import re
import time
from uuid import UUID
from botocore.exceptions import ClientError
from shared_campaign_work import configuration as C, records as R, retirement as K
from shared_campaign_locators import period as P

ACCOUNT='107827791950'
REGION='us-east-1'
CASES=('gate_closed','preproof_refusal','complete_replay','guards_and_complete_replay','lost_ack')
MESSAGE=b'amt-synthetic-retirement-qualification:v1'


def need(value):
    if not value:raise RuntimeError('PERIOD_RETIREMENT_QUALIFICATION_FAILED')


@contextmanager
def pins(values):
    previous={k:os.environ.get(k) for k in values}
    os.environ.update(values)
    try:yield
    finally:
        for k,v in previous.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v


def run(client,kms,*,run_id,pipeline_table,outbox_table,key_arn,case,
        now=time.time,remaining_ms=lambda:60000):
    need(type(run_id) is str and re.fullmatch('[0-9a-f]{12}',run_id))
    need(case in CASES)
    prefix='amt-campaign-retirement-qual-'+run_id
    tables={'pipeline':pipeline_table,'outbox':outbox_table}
    need(tables=={k:prefix+'-'+k for k in tables})
    need(type(key_arn) is str and re.fullmatch(
        rf'arn:aws:kms:{REGION}:{ACCOUNT}:key/[0-9a-f]{{8}}(?:-[0-9a-f]{{4}}){{3}}-[0-9a-f]{{12}}',key_arn))
    def call(method,**kwargs):
        need(remaining_ms()>=6000)
        return method(**kwargs)
    timestamp=int(now());need(timestamp>0)
    period=timestamp//P.PERIOD_SECONDS-2;need(period>=0)
    generation=str(UUID(hex='00000000000040008000'+run_id))
    resources={}
    expected_tags={'Project':'trustcheckradar','Environment':'dev',
        'Purpose':'campaign-retirement-qualification','QualificationRunId':run_id}
    def snapshot():
        result={}
        for family,table in tables.items():
            page=call(client.scan,TableName=table,ConsistentRead=True,Limit=20)
            need(type(page.get('Items')) is list and not page.get('LastEvaluatedKey'))
            result[family]=sorted([C.plain(v) for v in page['Items']],key=lambda v:(v['PK'],v['SK']))
        return result
    for family,table in tables.items():
        info=call(client.describe_table,TableName=table).get('Table')
        need(type(info) is dict and info.get('TableName')==table and info.get('TableStatus')=='ACTIVE'
            and info.get('TableArn')==f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{table}'
            and info.get('KeySchema')==[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}]
            and info.get('AttributeDefinitions')==[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}]
            and not info.get('GlobalSecondaryIndexes') and not info.get('LocalSecondaryIndexes'))
        table_id=info.get('TableId')
        try:need(type(table_id) is str and str(UUID(table_id))==table_id)
        except (ValueError,TypeError,AttributeError):need(False)
        tags=call(client.list_tags_of_resource,ResourceArn=info['TableArn'])
        need(not tags.get('NextToken') and type(tags.get('Tags')) is list
            and len(tags['Tags'])==len(expected_tags)
            and all(type(t) is dict and set(t)=={'Key','Value'} for t in tags['Tags'])
            and {t['Key']:t['Value'] for t in tags['Tags']}==expected_tags)
        resources[family]={'tableName':table,'tableId':table_id}
    metadata=call(kms.describe_key,KeyId=key_arn).get('KeyMetadata')
    need(type(metadata) is dict and metadata.get('Arn')==key_arn
        and metadata.get('KeyId')==key_arn.rsplit('/',1)[1]
        and metadata.get('KeyState')=='Enabled' and metadata.get('Enabled') is True
        and metadata.get('Description')==prefix
        and metadata.get('KeyManager')=='CUSTOMER' and metadata.get('Origin')=='AWS_KMS'
        and metadata.get('KeySpec')=='HMAC_256' and metadata.get('KeyUsage')=='GENERATE_VERIFY_MAC'
        and metadata.get('MultiRegion') is False)
    key_tags=call(kms.list_resource_tags,KeyId=key_arn)
    expected_key_tags={'Project':'trustcheckradar','Environment':'dev',
        'Purpose':'campaign-contributor-token','PeriodId':str(period)}
    need(not key_tags.get('Truncated') and not key_tags.get('NextMarker')
        and type(key_tags.get('Tags')) is list and len(key_tags['Tags'])==4
        and all(type(t) is dict and set(t)=={'TagKey','TagValue'} for t in key_tags['Tags'])
        and {t['TagKey']:t['TagValue'] for t in key_tags['Tags']}==expected_key_tags)
    need(snapshot()=={'pipeline':[],'outbox':[]})  # Refuse reuse; never reset old proof.
    env={'CAMPAIGN_PERIOD_ADMISSION_ENABLED':'true','CAMPAIGN_PERIOD_ADMISSION_GENERATION':generation,
        'CAMPAIGN_PERIOD_ADMISSION_ACCOUNT_ID':ACCOUNT,'AWS_REGION':REGION,'AWS_DEFAULT_REGION':REGION,
        'APP_ENVIRONMENT':'dev','CAMPAIGN_PERIOD_WORK_ENABLED':'true',
        'CAMPAIGN_PERIOD_WORK_MANIFEST_SHA256':'b'*64,'CAMPAIGN_PERIOD_WORK_INVENTORY_REVISION':'1',
        'CAMPAIGN_PERIOD_RETIREMENT_ENABLED':'false' if case=='gate_closed' else 'true'}
    for family,resource in resources.items():
        env['CAMPAIGN_PERIOD_WORK_'+family.upper()+'_TABLE_NAME']=resource['tableName']
        env['CAMPAIGN_PERIOD_WORK_'+family.upper()+'_TABLE_ID']=resource['tableId']
    marker={'PK':'INVENTORY#dev','SK':'CAMPAIGN_PERIOD_WORK','recordType':'CAMPAIGN_PERIOD_WORK_INVENTORY',
        'schemaVersion':1,'environment':'dev','coverage':'VERIFIED_COMPLETE','revision':1,'manifestSha256':'b'*64,
        'approvedAtEpoch':timestamp-1,'admissionGeneration':generation,'locatorManifestSha256':'a'*64,
        'locatorInventoryRevision':1,'minimumPeriodId':period,'resources':resources,'writers':C.WRITERS,
        'baseline':'EXACT_ALL_TARGETS_INDEXED','restoreInvalidation':'REQUIRES_NEW_GENERATION'}
    control=R.control_key(period)|{'recordType':'CAMPAIGN_PERIOD_WORK_CONTROL','schemaVersion':1,
        'environment':'dev','generation':generation,'periodId':period,'manifestSha256':'b'*64,
        'inventoryRevision':1,'revision':1,'nextOrdinal':1,'pendingCount':0,'passRevision':0,
        'passCursor':0,'passHighWater':0,'lastProgressAtEpoch':0,'lastFullPassAtEpoch':0}
    registry={'PK':f'PERIOD#{period}','SK':'HMAC_KEY','periodId':period,'keyArn':key_arn,'status':'ENABLED',
        'retireAfterEpoch':(period+1)*P.PERIOD_SECONDS+P.RECOVERY_SECONDS,
        'admissionSchemaVersion':2,'admissionGeneration':generation,'admissionState':'SEALED',
        'admissionRevision':1,'admissionManifestSha256':'a'*64,'admissionInventoryRevision':1,
        'admissionChangedAtEpoch':timestamp-1,'workSchemaVersion':1,'workManifestSha256':'b'*64,
        'workInventoryRevision':1,'sealSchemaVersion':1,'sealedAtEpoch':timestamp-1,
        'sealManifestSha256':'b'*64,'sealInventoryRevision':1,'sealGeneration':generation,
        'sealPipelineTableId':resources['pipeline']['tableId'],
        'sealOutboxTableId':resources['outbox']['tableId'],'sealNextOrdinal':1}
    if case=='preproof_refusal':registry={k:v for k,v in registry.items() if k not in K.SEAL_FIELDS}|{'admissionState':'DRAINING'}
    # Fresh table plus create-only transaction prevents overwriting prior state.
    call(client.transact_write_items,TransactItems=[{'Put':{'TableName':pipeline_table,'Item':C.wire(row),
        'ConditionExpression':'attribute_not_exists(PK) AND attribute_not_exists(SK)'}} for row in (marker,control,registry)])
    before=snapshot();kms_calls=[];ddb_calls=[];mac_checks=[]
    def rejected_mac():
        try:call(kms.generate_mac,KeyId=key_arn,MacAlgorithm='HMAC_SHA_256',Message=MESSAGE)
        except ClientError as exc:
            need(exc.response.get('Error',{}).get('Code') in ('DisabledException','KMSInvalidStateException'))
            return True
        return False
    class Ddb:
        def __getattr__(self,name):
            def invoke(**kwargs):
                ddb_calls.append(name)
                return call(getattr(client,name),**kwargs)
            return invoke
    class Kms:
        def __getattr__(self,name):
            need(name in ('describe_key','list_resource_tags','disable_key','schedule_key_deletion'))
            def invoke(**kwargs):
                need(kwargs.get('KeyId')==key_arn)
                kms_calls.append(name)
                result=call(getattr(kms,name),**kwargs)
                if name=='disable_key':mac_checks.append(rejected_mac())
                if case=='lost_ack' and name in ('disable_key','schedule_key_deletion'):
                    raise RuntimeError('SYNTHETIC_LOST_ACK')
                return result
            return invoke
    with pins(env):
        if case=='guards_and_complete_replay':
            with pins({'CAMPAIGN_PERIOD_RETIREMENT_ENABLED':'false'}):
                try:K.retire(Ddb(),Kms(),period,now=lambda:int(now()),remaining_ms=remaining_ms)
                except R.WorkUnavailable:pass
                else:need(False)
            need(not ddb_calls and not kms_calls and snapshot()==before)
            invalid={k:v for k,v in registry.items() if k not in K.SEAL_FIELDS}|{'admissionState':'DRAINING'}
            call(client.transact_write_items,TransactItems=[{'Put':{'TableName':pipeline_table,
                'Item':C.wire(invalid),**C.exact(registry)}}])
            invalid_snapshot=snapshot()
            try:K.retire(Ddb(),Kms(),period,now=lambda:int(now()),remaining_ms=remaining_ms)
            except R.WorkUnavailable:pass
            else:need(False)
            need(not kms_calls and snapshot()==invalid_snapshot)
            call(client.transact_write_items,TransactItems=[{'Put':{'TableName':pipeline_table,
                'Item':C.wire(registry),**C.exact(invalid)}}])
            need(snapshot()==before)
        if case in ('gate_closed','preproof_refusal'):
            try:K.retire(Ddb(),Kms(),period,now=lambda:int(now()),remaining_ms=remaining_ms)
            except R.WorkUnavailable:pass
            else:need(False)
            need(snapshot()==before and not kms_calls)
            if case=='gate_closed':need(not ddb_calls)
            current=call(kms.describe_key,KeyId=key_arn)['KeyMetadata']
            need(current==metadata)
            return {'schemaVersion':1,'case':case,'passed':True,'syntheticProof':True,
                'productionActivation':False,'kmsMutationCount':0,'scheduled':False,'keyDestroyedObserved':False}
        mac=call(kms.generate_mac,KeyId=key_arn,MacAlgorithm='HMAC_SHA_256',Message=MESSAGE)
        need(mac.get('KeyId')==key_arn and mac.get('MacAlgorithm')=='HMAC_SHA_256'
             and type(mac.get('Mac')) is bytes and len(mac['Mac'])==32)
        result=K.retire(Ddb(),Kms(),period,now=lambda:int(now()),remaining_ms=remaining_ms)
        need(result=={'state':'SCHEDULED','keyDestroyedObserved':False,'changed':True})
        after=snapshot();need(after['outbox']==[])
        records={row['SK']:row for row in after['pipeline'] if row['PK']==registry['PK']}
        stored=records.get('HMAC_KEY');need(stored is not None)
        K.validate_retired(stored,C.pins(),marker,int(now()))
        need(len(after['pipeline'])==3 and marker in after['pipeline'] and control in after['pipeline'])
        need(mac_checks==[True] and rejected_mac())
        need(kms_calls.count('disable_key')==1 and kms_calls.count('schedule_key_deletion')==1)
        replay=K.retire(Ddb(),Kms(),period,now=lambda:int(now()),remaining_ms=remaining_ms)
        need(replay=={'state':'SCHEDULED','keyDestroyedObserved':False,'changed':False}
            and snapshot()==after and kms_calls.count('disable_key')==1
            and kms_calls.count('schedule_key_deletion')==1)
        final=call(kms.describe_key,KeyId=key_arn)['KeyMetadata']
        need(final['KeyState']=='PendingDeletion' and isinstance(final.get('DeletionDate'),datetime)
            and int(final['DeletionDate'].timestamp())==stored['scheduledDeletionAtEpoch'])
        return {'schemaVersion':1,'case':case,'passed':True,'syntheticProof':True,
            'productionActivation':False,'kmsMutationCount':2,'scheduled':True,'keyDestroyedObserved':False,
            'replayUnchanged':True,'generateMacRejectedDisabled':True,'generateMacRejectedScheduled':True,
            'injectedLostAcknowledgmentCount':2 if case=='lost_ack' else 0,
            'guardRefusalCount':2 if case=='guards_and_complete_replay' else 0}
