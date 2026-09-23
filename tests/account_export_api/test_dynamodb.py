"""Real SDK + Moto traversal, isolated from legacy test modules' global SDK stubs."""
import os,sys,hashlib,base64
from pathlib import Path
from types import SimpleNamespace
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':
    pytest.skip('Run isolated with AMT_AUTHORITY_INTEGRATION=1',allow_module_level=True)
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
import boto3
from botocore.config import Config
from moto import mock_aws
from account_export_api.runtime import Settings
from account_export_api.reader import Reader
from account_export_api.cursor import Cursor,ExportError
from account_export_api.service import Export,SCOPE
from shared_check_authority.core import Authority,AuthorityError
from shared_check_authority.inventory import INVENTORY_KEY
from shared_purchase_ownership import OwnershipStore

NOW=1800000000
NAMES=('users','devices','deletion','authority','entitlements','recovery','history_content','history_control','pipeline','outbox','abuse')

@pytest.fixture
def world():
    with mock_aws():
        resource=boto3.resource('dynamodb',region_name='us-east-1',config=Config(retries={'total_max_attempts':1}))
        for name in NAMES:
            kwargs={'TableName':name,'KeySchema':[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],
                    'AttributeDefinitions':[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}], 'BillingMode':'PAY_PER_REQUEST'}
            if name=='pipeline':
                kwargs['AttributeDefinitions'] += [{'AttributeName':'GSI1PK','AttributeType':'S'},{'AttributeName':'GSI1SK','AttributeType':'S'}]
                kwargs['GlobalSecondaryIndexes']=[{'IndexName':'ContributorPeriodIndex','KeySchema':[{'AttributeName':'GSI1PK','KeyType':'HASH'},{'AttributeName':'GSI1SK','KeyType':'RANGE'}],'Projection':{'ProjectionType':'ALL'}}]
            resource.create_table(**kwargs)
        put=lambda name,row:resource.Table(name).put_item(Item=row)
        put('users',{'PK':'USER#account-a','SK':'PROFILE','sub':'account-a','status':'ACTIVE','ageVerified':True,'email':'synthetic@example.invalid'})
        put('devices',{'PK':'USER#account-a','SK':'ACTIVE_BINDING','recordType':'ACTIVE_BINDING_POINTER','stateVersion':1,'bindingFingerprint':'device-a'})
        put('devices',{'PK':'USER#account-a','SK':'DEVICE#device-a','accountId':'account-a','bindingFingerprint':'device-a','status':'ACTIVE','platform':'android'})
        put('authority',INVENTORY_KEY|{'recordType':'V1_HMAC_KEY_INVENTORY','schemaVersion':1,'revision':1,'coverage':'VERIFIED_COMPLETE','issuedKeys':{'k1':hashlib.sha256(b'x'*32).hexdigest()}})
        put('entitlements',{'PK':'PURCHASE#CONTROL','SK':'OWNERSHIP_INVENTORY','recordType':'PURCHASE_OWNERSHIP_INVENTORY','schemaVersion':1,'revision':1,'coverage':'VERIFIED_COMPLETE','environment':'dev'})
        put('history_control',{'PK':'USER#account-a','SK':'STATE','accountStatus':'ACTIVE','historyGeneration':0,'recognitionGeneration':0})
        put('history_control',{'PK':'USER#account-a','SK':'PROGRESS#0','qualifyingChecks':0,'awardedBadgeIds':[]})
        period=NOW//(14*86400)
        for item in (period,period-1):
            put('pipeline',{'PK':'PERIOD#'+str(item),'SK':'HMAC_KEY','status':'ENABLED','periodId':item,'retireAfterEpoch':(item+1)*14*86400+7*86400,'keyArn':'arn:aws:kms:us-east-1:107827791950:key/12345678-1234-1234-1234-123456789abc'})
        settings=Settings('users','devices','deletion','authority','issuer','client','aws.cognito.signin.user.admin','k1',{'k1':b'x'*32})
        authority=Authority(settings,resource,now=lambda:NOW)
        store=OwnershipStore(table=resource.Table('entitlements'),ledger=resource.Table('deletion'),users_table_name='users',table_name='entitlements',ledger_table_name='deletion',client=boto3.client('dynamodb',region_name='us-east-1'),environment='dev',now=lambda:NOW)
        reader=Reader(authority,{k:k for k in NAMES},SimpleNamespace(admin_get_user=lambda **kw:{'Username':'account-a','UserAttributes':[{'Name':'sub','Value':'account-a'}]}),'pool',store,SimpleNamespace(generate_mac=lambda **kw:{'Mac':b'm'*32}))
        event={'headers':{'x-device-binding-fingerprint':'device-a'},'requestContext':{'authorizer':{'jwt':{'claims':{'sub':'account-a','iss':'issuer','client_id':'client','token_use':'access','exp':str(NOW+3600),'scope':'aws.cognito.signin.user.admin','auth_time':str(NOW),'iat':str(NOW)}}}}}
        yield Export(reader,Cursor('dev','k1',{'k1':b'z'*32}),now=lambda:NOW),event,resource


def test_actual_owned_empty_and_nonempty_families_all_traverse_to_complete(world):
    service,event,resource=world
    page=service.page(event,{'action':'START_EXPORT'});pages=[page]
    while page['nextCursor']:
        page=service.page(event,{'action':'CONTINUE_EXPORT','cursor':page['nextCursor']});pages.append(page)
        assert len(pages)<100
    assert page['status']=='COMPLETE'
    assert {p['family'] for p in pages}==set(SCOPE['included'])
    assert pages[0]['items'][0]['email']=='synthetic@example.invalid'
    assert 'sub' not in pages[0]['items'][0]
    assert [p['pageNumber'] for p in pages]==list(range(len(pages)))
    assert all(p['operationId']==pages[0]['operationId'] for p in pages)


def test_global_deletion_fence_invalidates_existing_encrypted_cursor(world):
    service,event,resource=world
    page=service.page(event,{'action':'START_EXPORT'})
    resource.Table('deletion').put_item(Item={'PK':'ACCOUNT#account-a','SK':'ACCOUNT_DELETION','status':'REQUESTED'})
    with pytest.raises(AuthorityError,match='ACCOUNT_UNAVAILABLE'):
        service.page(event,{'action':'CONTINUE_EXPORT','cursor':page['nextCursor']})


def test_inventory_rotation_invalidates_continuation(world):
    service,event,resource=world
    page=service.page(event,{'action':'START_EXPORT'})
    resource.Table('entitlements').update_item(Key={'PK':'PURCHASE#CONTROL','SK':'OWNERSHIP_INVENTORY'},UpdateExpression='SET revision = :r',ExpressionAttributeValues={':r':2})
    with pytest.raises(ExportError,match='SOURCE_CHANGED'):
        service.page(event,{'action':'CONTINUE_EXPORT','cursor':page['nextCursor']})


def test_owner_requires_completed_onboarding_and_active_device_for_export(world):
    service,event,resource=world
    resource.Table('users').update_item(Key={'PK':'USER#account-a','SK':'PROFILE'},
        UpdateExpression='SET #s=:s, ageVerified=:v',ExpressionAttributeNames={'#s':'status'},
        ExpressionAttributeValues={':s':'PENDING_AGE_GATE',':v':False})
    with pytest.raises(AuthorityError,match='ACCOUNT_UNAVAILABLE'):
        service.page(event,{'action':'START_EXPORT'})
    resource.Table('users').update_item(Key={'PK':'USER#account-a','SK':'PROFILE'},
        UpdateExpression='SET #s=:s, ageVerified=:v',ExpressionAttributeNames={'#s':'status'},
        ExpressionAttributeValues={':s':'ACTIVE',':v':True})
    resource.Table('devices').delete_item(Key={'PK':'USER#account-a','SK':'ACTIVE_BINDING'})
    with pytest.raises(AuthorityError,match='ACTIVE_DEVICE_REQUIRED'):
        service.page(event,{'action':'START_EXPORT'})


@pytest.fixture
def play_export(world):
    from shared_play_lifecycle.tokens import prepare_put
    service,event,resource=world
    resource.create_table(TableName='play-tokens',BillingMode='PAY_PER_REQUEST',
        KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],
        AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}])
    authority=service.reader.a
    service.reader.play_token_table='play-tokens'
    candidate=Export(service.reader,service.cursor,now=service.now,play_verification=True)
    partition=authority._partition('account-a','k1')
    class SyntheticCipher:
        def encrypt(self,token,partition):
            return ('arn:aws:kms:us-east-1:107827791950:key/12345678-1234-1234-1234-123456789abc',
                    base64.b64encode(hashlib.sha256(token.encode()).digest()).decode(),base64.b64encode(b'wrapped').decode())
    tokens=[]
    for index in range(27):
        item=prepare_put('play-tokens',partition,'synthetic-export-token-'+str(index),
            access_until_epoch=NOW+1000,next_attempt_epoch=NOW,now=NOW,cipher=SyntheticCipher())['Put']['Item']
        resource.Table('play-tokens').put_item(Item=item);tokens.append(item)
    expired=prepare_put('play-tokens',partition,'synthetic-expired-export-token',
        access_until_epoch=NOW-604801,next_attempt_epoch=NOW-604802,now=NOW-604802,cipher=SyntheticCipher())['Put']['Item']
    resource.Table('play-tokens').put_item(Item=expired)
    yield candidate,event,resource,tokens,expired


def test_candidate_three_traverses_actual_paginated_metadata_without_credentials(play_export):
    service,event,resource,tokens,expired=play_export
    current=service.page(event,{'action':'START_EXPORT'});pages=[current]
    while current['nextCursor']:
        current=service.page(event,{'action':'CONTINUE_EXPORT','cursor':current['nextCursor']});pages.append(current)
        assert len(pages)<100
    selected=[p for p in pages if p['family']=='play_verification']
    assert len(selected)==2 and sum(len(p['items']) for p in selected)==27
    assert current['status']=='COMPLETE'
    assert all(p['exportTransportVersion']=='1.0.0-account-export-candidate.3' for p in pages)
    assert {p['family'] for p in pages}==set(current['scope']['included'])
    assert 'purchase_credentials' in current['scope']['excluded']
    public=repr([p['items'] for p in selected])
    for token in tokens+[expired]:
        assert all(secret not in public for secret in (token['ciphertext'],token['wrappedDataKey'],token['tokenDigest'],token['PK'],token['kmsKeyArn']))
    assert resource.Table('play-tokens').get_item(Key={k:expired[k] for k in ('PK','SK')})['Item']==expired


def test_deletion_between_play_metadata_read_and_response_rejects_page(play_export,monkeypatch):
    service,event,resource,_,_=play_export
    current=service.page(event,{'action':'START_EXPORT'})
    original=service.reader.read
    def deletion_race(*args,**kwargs):
        result=original(*args,**kwargs)
        if result[0]=='play_verification':
            resource.Table('deletion').put_item(Item={'PK':'ACCOUNT#account-a','SK':'ACCOUNT_DELETION','status':'REQUESTED'})
        return result
    monkeypatch.setattr(service.reader,'read',deletion_race)
    with pytest.raises(AuthorityError,match='ACCOUNT_UNAVAILABLE'):
        while current['nextCursor']:
            current=service.page(event,{'action':'CONTINUE_EXPORT','cursor':current['nextCursor']})
