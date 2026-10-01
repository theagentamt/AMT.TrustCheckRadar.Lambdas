import os,sys,importlib
from pathlib import Path
from uuid import uuid4
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('Isolated SDK only',allow_module_level=True)
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src/campaign_participation'))
import boto3
from moto import mock_aws

@pytest.mark.parametrize('enabled',[False,True])
@pytest.mark.parametrize('race',[False,True])
def test_actual_withdrawal_atomically_enqueues_and_fences(enabled,race,monkeypatch):
    for k,v in {'AWS_DEFAULT_REGION':'us-east-1','USERS_TABLE_NAME':'users','DELETION_LEDGER_TABLE_NAME':'ledger',
                'ENVIRONMENT':'dev','CAMPAIGN_PARTICIPATION_NOTICE_VERSION':'research-consent-2026-09-21-v2',
                'CAMPAIGN_PARTICIPATION_POLICY_VERSION':'independent-research-v1','CONSENT_INDEPENDENCE_ENABLED':'true',
                'CAMPAIGN_RECOVERY_WRITES_ENABLED':str(enabled).lower()}.items():monkeypatch.setenv(k,v)
    with mock_aws():
        r=boto3.resource('dynamodb',region_name='us-east-1')
        for name in ['users','ledger']:
            r.create_table(TableName=name,KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}],BillingMode='PAY_PER_REQUEST')
        for name in ['config','errors','service']:sys.modules.pop(name,None)
        service=importlib.import_module('service')
        profile={'PK':'USER#owner','SK':'PROFILE','sub':'owner','status':'ACTIVE','ageVerified':True}
        r.Table('users').put_item(Item=profile)
        payload={'schemaVersion':2,'noticeVersion':service.config.CURRENT_NOTICE,'operationId':str(uuid4()),'action':'join','expectedStateVersion':0}
        service.update_participation('owner',payload,now_epoch=1800000000)
        payload.update(action='withdraw',operationId=str(uuid4()),expectedStateVersion=1)
        real=service.dynamodb.transact_write_items
        if race:
            def write(**kw):
                r.Table('users').put_item(Item=profile|{'status':'DELETION_REQUESTED'})
                return real(**kw)
            service.dynamodb.transact_write_items=write
            with pytest.raises(Exception):service.update_participation('owner',payload,now_epoch=1800000001)
        else:service.update_participation('owner',payload,now_epoch=1800000001)
        row=r.Table('ledger').get_item(Key={'PK':'ACCOUNT#owner','SK':'CAMPAIGN_RECOVERY_CONTROL'}).get('Item')
        assert (row is not None)==(enabled and not race)
        if row:assert row['pendingJobs']==1
        command=r.Table('ledger').get_item(Key={'PK':'ACCOUNT#owner','SK':'CAMPAIGN_WITHDRAWAL#'+payload['operationId']}).get('Item')
        assert (command is not None)==(not race)
