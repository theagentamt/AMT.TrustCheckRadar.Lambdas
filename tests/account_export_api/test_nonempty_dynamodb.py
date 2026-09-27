"""All-family SDK reader composition; Cognito/KMS are injected, never live providers."""
import base64
import hashlib
import json
from collections import Counter
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID
import pytest
import importlib.util
from pathlib import Path
_spec=importlib.util.spec_from_file_location('export_sdk_world',Path(__file__).with_name('test_dynamodb.py'))
_world=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_world)
world,play_export,NOW=_world.world,_world.play_export,_world.NOW
NAMES=_world.NAMES
from account_export_api.cursor import ExportError
from shared_check_authority.core import AuthorityError


def modern(service, resource, monkeypatch):
    from shared_campaign_work import configuration as C
    generation='12345678-1234-4234-8234-123456789abc'
    env={'APP_ENVIRONMENT':'dev','AWS_REGION':'us-east-1',
         'CAMPAIGN_PERIOD_ADMISSION_ENABLED':'true','CAMPAIGN_PERIOD_ADMISSION_GENERATION':generation,
         'CAMPAIGN_PERIOD_ADMISSION_ACCOUNT_ID':'107827791950','CAMPAIGN_PERIOD_WORK_ENABLED':'true',
         'CAMPAIGN_PERIOD_WORK_MANIFEST_SHA256':'a'*64,'CAMPAIGN_PERIOD_WORK_INVENTORY_REVISION':'1'}
    for index, family in enumerate(('pipeline','outbox')):
        env['CAMPAIGN_PERIOD_WORK_'+family.upper()+'_TABLE_NAME']=family
        env['CAMPAIGN_PERIOD_WORK_'+family.upper()+'_TABLE_ID']=str(UUID(int=index+1))
    for key,value in env.items():monkeypatch.setenv(key,value)
    import boto3
    client=boto3.client('dynamodb',region_name='us-east-1')
    describe=client.describe_table
    def bound(**kwargs):
        result=describe(**kwargs);name=kwargs['TableName']
        # Moto has no real AWS account binding; all item reads remain real SDK calls.
        result['Table']['TableId']=env['CAMPAIGN_PERIOD_WORK_'+name.upper()+'_TABLE_ID']
        result['Table']['TableArn']='arn:aws:dynamodb:us-east-1:107827791950:table/'+name
        return result
    monkeypatch.setattr(client,'describe_table',bound)
    service.reader.campaign_work_client=client
    config=C.pins();period=NOW//1209600
    marker={'PK':'INVENTORY#dev','SK':'CAMPAIGN_PERIOD_WORK','recordType':'CAMPAIGN_PERIOD_WORK_INVENTORY',
            'schemaVersion':1,'environment':'dev','coverage':'VERIFIED_COMPLETE','revision':1,
            'manifestSha256':'a'*64,'approvedAtEpoch':NOW-1,'admissionGeneration':generation,
            'locatorManifestSha256':'b'*64,'locatorInventoryRevision':1,'minimumPeriodId':period-1,
            'resources':config['resources'],'writers':C.WRITERS,'baseline':'EXACT_ALL_TARGETS_INDEXED',
            'restoreInvalidation':'REQUIRES_NEW_GENERATION'}
    resource.Table('pipeline').put_item(Item=marker)
    for p in (period-1,period):
        table=resource.Table('pipeline');row=table.get_item(Key={'PK':f'PERIOD#{p}','SK':'HMAC_KEY'})['Item']
        row.update(admissionSchemaVersion=2,admissionGeneration=generation,admissionState='OPEN',
                   admissionRevision=1,admissionManifestSha256='b'*64,admissionInventoryRevision=1,
                   admissionChangedAtEpoch=NOW-1,workSchemaVersion=1,workManifestSha256='a'*64,workInventoryRevision=1)
        table.put_item(Item=row)
    return marker


def seed(service,resource):
    from shared_history import build_history_items
    from shared_purchase_ownership.service import token_hash
    put=lambda name,row:resource.Table(name).put_item(Item=row)
    pk='USER#account-a';part=service.reader.a._partition('account-a','k1')
    secret='NEVER_EXPORT_CREDENTIAL_SENTINEL'
    service.reader.cognito=SimpleNamespace(admin_get_user=lambda **kw:{'Username':'account-a','UserAttributes':[
        {'Name':'sub','Value':'account-a'},{'Name':'email','Value':'synthetic@example.invalid'},
        {'Name':'custom:private_token','Value':secret}]})
    put('recovery',{'PK':pk,'SK':'RECOVERY#1','operation':'recover','status':'COMPLETE','completedAtEpoch':NOW,'recoverySecret':secret})
    put('entitlements',{'PK':pk,'SK':'USAGE#2027-01','periodKey':'2027-01','usedCount':7,'purchaseToken':secret})
    entitlement={'PK':pk,'SK':'ENTITLEMENT#google_play#trustcheck_radar_pro_monthly','accountId':'account-a',
                 'platform':'google_play','productId':'trustcheck_radar_pro_monthly','entitlementTier':'PRO','remainingMonthlyScans':0}
    store=service.reader.purchase_reader
    for i in range(27):
        store.claim('account-a',(token_hash('synthetic-export-purchase-'+str(i)),),product_id=entitlement['productId'],
                    entitlement=entitlement,expected_entitlement=store._get({'PK':pk,'SK':entitlement['SK']}))
    # Inactive/exhausted grant data is exported, never an export admission gate.
    put('authority',{'PK':part,'SK':'ACCESS','state':'INACTIVE','sources':{'trial':{'state':'EXPIRED','validUntilEpoch':NOW-1}},'capability':secret})
    put('authority',{'PK':part,'SK':'PERIOD#1','startEpoch':NOW-100,'endEpoch':NOW+100,'limit':0,'usedChecks':0,'reservedChecks':0})
    put('authority',{'PK':part,'SK':'TRIAL_HISTORY','activatedAtEpoch':NOW-100,'policyVersion':'v1'})
    nonce='a'*16;digest='d'*64;expiry=f'{NOW+600:08x}'
    tag=service.reader.a._mac('k1','operation','account-a\0'+digest+'\0'+expiry+'\0'+nonce)[:24]
    check=f'v1_k1_{expiry}_{nonce}_{tag}'
    put('authority',{'PK':part,'SK':'CHECK#'+check,'recordType':'V1_CHECK_RECEIPT','state':'SETTLED',
                     'retentionDeadlineEpoch':NOW+1000,'assessmentEpoch':NOW,'processingOutcome':'failed',
                     'chargedChecks':0,'resultSummary':{'schemaVersion':1,'checkId':'client-private-id','verdict':'unknown',
                         'processingOutcome':'failed','coverage':'not_assessed','reasonCodes':[],
                         'transportWarnings':[],'threatTypes':[],'lookupCount':0,'providerCallCount':0,'observedHopCount':0,
                         'scope':'HTTP_REDIRECTS_AND_GOOGLE_LOOKUP','consumerAccessEnabled':False},
                     'projectionScope':'full_url','clientCheckId':'client-private-id','payloadHmac':digest,'checkId':check,'rawUrl':secret})
    settings=SimpleNamespace(retention_days=90,dedup_retention_days=120,schema_version=1,
                             max_summary_bytes=4096,max_list_items=20,max_text_field_bytes=1024)
    content,_=build_history_items(account_id='account-a',payload_hash='a'*64,
        accepted={'historyGeneration':0,'recognitionGeneration':0,'acceptedSequence':1,'acceptedAtEpochMs':NOW*1000},
        payload={'requestId':'export-history-1','sourceType':'ocr','sanitizedText':secret},
        response={'schemaVersion':'1.0','requestId':'export-history-1','scamScore':88,'riskLevel':'high','confidence':0.9,
                  'summary':'Likely fraud.','signals':['urgent_payment'],'recommendedActions':['Do not pay.']},now_epoch=NOW,settings=settings)
    put('history_content',content)
    hashed=hashlib.sha256(b'account-a').hexdigest()
    put('abuse',{'PK':'ANALYSIS#REQUEST#'+hashed,'SK':'REQ#1','status':'COMPLETE','createdAt':'now','expiresAt':NOW+1000,'rawText':secret})
    put('abuse',{'PK':'ANALYSIS#CONSUMPTION#'+hashed,'SK':'SCAN#1','consumptionType':'trial','createdAt':'now','expiresAt':NOW+1000})
    token=base64.urlsafe_b64encode(b'm'*32).decode().rstrip('=');period=NOW//1209600
    for i in range(27):
        event=str(UUID(int=i+100,version=4));eventpk='EVENT#'+event
        put('outbox',{'PK':'ACCOUNT#'+hashed,'SK':'OUTBOX#'+event,'recordType':'CAMPAIGN_OUTBOX_LOCATOR',
             'schemaVersion':2,'recordVersion':1,'environment':'dev','accountIdHash':hashed,'statisticsEventId':event,
             'eventPK':eventpk,'eventSK':'OBSERVATION_READY','eventExpiresAt':NOW+1000,'logicalExpiresAt':NOW+1000+86400})
        put('outbox',{'PK':eventpk,'SK':'OBSERVATION_READY','accountId':'account-a','statisticsEventId':event,'environment':'dev',
             'observedAtEpoch':NOW,'sourceType':'ocr','riskLevel':'high','signalIds':['urgent_payment'],'expiresAt':NOW+1000,'rawText':secret})
        put('pipeline',{'PK':eventpk,'SK':'FEATURE','GSI1PK':f'CONTRIB#{period}#{token}','GSI1SK':eventpk+'#FEATURE',
             'periodId':period,'environment':'dev','contributorToken':token,'languageId':'en','signalIds':['urgent_payment'],
             'confidence':Decimal('0.9'),'expiresAt':NOW+1000,'rawText':secret})
    put('users',{'PK':pk,'SK':'CAMPAIGN_PARTICIPATION','state':'ACTIVE','noticeVersion':'v1','policyVersion':'v1','consentEpoch':secret})
    put('users',{'PK':pk,'SK':'CAMPAIGN_CONSENT#1','eventType':'JOIN','occurredAt':'now','resultingState':'ACTIVE','consentEpoch':secret})
    # Same public shape in a different account must never appear in any page.
    other={'PK':'USER#other-account','SK':'PROFILE','email':'OTHER_ACCOUNT_SENTINEL','status':'ACTIVE','ageVerified':True}
    put('users',other)
    return secret,other,check,part


def traverse(service,event):
    pages=[];page=service.page(event,{'action':'START_EXPORT'})
    while True:
        pages.append(page)
        if not page['nextCursor']:return pages
        assert len(pages)<100
        page=service.page(event,{'action':'CONTINUE_EXPORT','cursor':page['nextCursor']})


def test_all_twenty_nonempty_families_modern_inventory_and_real_pagination(play_export,monkeypatch):
    service,event,resource,tokens,_=play_export
    modern(service,resource,monkeypatch);secret,other,check,part=seed(service,resource)
    before={name:resource.Table(name).scan()['Items'] for name in (*NAMES,'play-tokens')}
    pages=traverse(service,event);counts=Counter()
    for page in pages:counts[page['family']]+=len(page['items'])
    assert len(counts)==20 and set(counts)==set(pages[-1]['scope']['included']) and all(counts.values())
    for family in ('purchases','research_observations','research_contributions','play_verification'):
        assert counts[family]==27 and sum(p['family']==family for p in pages)>=2
    public=json.dumps([p['items'] for p in pages])
    for forbidden in (secret,'OTHER_ACCOUNT_SENTINEL',check,part,'client-private-id','wrappedDataKey','ciphertext','tokenDigest','consentEpoch','rawText','payloadHmac','GSI1PK'):
        assert forbidden not in public
    assert all(token['ciphertext'] not in public and token['tokenDigest'] not in public for token in tokens)
    assert pages[-1]['status']=='COMPLETE' and all(p['startedAtEpoch']==NOW and p['expiresAtEpoch']==NOW+900 for p in pages)
    assert before=={name:resource.Table(name).scan()['Items'] for name in before}


def test_fixed_900_second_window_survives_fresh_auth_but_never_restarts(world):
    service,event,_=world;clock=[NOW];service.now=lambda:clock[0];service.reader.a.now=service.now
    first=service.page(event,{'action':'START_EXPORT'});claims=event['requestContext']['authorizer']['jwt']['claims']
    clock[0]=NOW+301;claims['iat']=str(clock[0]);claims['exp']=str(NOW+3600)
    with pytest.raises(ExportError,match='REAUTHENTICATION_REQUIRED'):
        service.page(event,{'action':'CONTINUE_EXPORT','cursor':first['nextCursor']})
    clock[0]=NOW+899;claims['auth_time']=claims['iat']=str(clock[0])
    page=service.page(event,{'action':'CONTINUE_EXPORT','cursor':first['nextCursor']})
    assert page['startedAtEpoch']==NOW and page['expiresAtEpoch']==NOW+900
    clock[0]=NOW+900;claims['auth_time']=claims['iat']=str(clock[0])
    with pytest.raises(ExportError,match='EXPORT_EXPIRED'):
        service.page(event,{'action':'CONTINUE_EXPORT','cursor':page['nextCursor']})


@pytest.mark.parametrize('change',['marker','table_generation','history_generation'])
def test_real_inventory_changes_during_page_prevent_release(world,monkeypatch,change):
    service,event,resource=world;modern(service,resource,monkeypatch)
    original=service.reader.read
    def read(*args):
        result=original(*args)
        if change=='marker':
            resource.Table('pipeline').update_item(Key={'PK':'INVENTORY#dev','SK':'CAMPAIGN_PERIOD_WORK'},
                UpdateExpression='SET revision=:r',ExpressionAttributeValues={':r':2})
        elif change=='history_generation':
            resource.Table('history_control').update_item(Key={'PK':'USER#account-a','SK':'STATE'},
                UpdateExpression='SET historyGeneration=:r',ExpressionAttributeValues={':r':1})
        else:
            original_describe=service.reader.campaign_work_client.describe_table
            def replaced(**kwargs):
                result=original_describe(**kwargs);result['Table']['TableId']=str(UUID(int=999));return result
            monkeypatch.setattr(service.reader.campaign_work_client,'describe_table',replaced)
        return result
    monkeypatch.setattr(service.reader,'read',read)
    from account_export_api import app
    monkeypatch.setattr(app,'load',lambda:service)
    response=app.lambda_handler(event|{'version':'2.0','routeKey':'POST /v1/users/account-export',
        'body':json.dumps({'schemaVersion':1,'action':'START_EXPORT'})},None)
    assert response['statusCode']==(409 if change=='history_generation' else 503)
    assert 'nextCursor' not in response['body'] and 'synthetic@example.invalid' not in response['body']


def test_lost_page_retry_is_as_read_same_operation_and_never_snapshot_claim(world):
    service,event,resource=world
    first=service.page(event,{'action':'START_EXPORT'})
    # Retry the same opaque continuation after an unacknowledged response.
    retry={'action':'CONTINUE_EXPORT','cursor':first['nextCursor']}
    observed=service.page(event,retry)
    service.reader.cognito=SimpleNamespace(admin_get_user=lambda **kw:{'Username':'account-a','UserAttributes':[
        {'Name':'sub','Value':'account-a'},{'Name':'email','Value':'updated@example.invalid'}]})
    repeated=service.page(event,retry)
    assert observed['operationId']==repeated['operationId']==first['operationId']
    assert observed['pageNumber']==repeated['pageNumber']==1
    assert observed['items']!=repeated['items'] and repeated['items']==[{'email':'updated@example.invalid'}]
    assert repeated['startedAtEpoch']==first['startedAtEpoch'] and repeated['expiresAtEpoch']==first['expiresAtEpoch']


def test_nested_receipt_capability_is_rejected_by_actual_reader(play_export,monkeypatch):
    service,event,resource,_,_=play_export;modern(service,resource,monkeypatch)
    _,_,check,partition=seed(service,resource)
    key={'PK':partition,'SK':'CHECK#'+check}
    row=resource.Table('authority').get_item(Key=key)['Item']
    row['resultSummary']['capability']='NEVER_EXPORT_NESTED_TOKEN'
    resource.Table('authority').put_item(Item=row)
    with pytest.raises(AuthorityError,match='RESULT_SUMMARY_INVALID'):traverse(service,event)
