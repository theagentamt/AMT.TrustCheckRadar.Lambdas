"""Persisted DynamoDB command clocks must produce the actual JSON HTTP response."""
import importlib.util
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run isolated with AMT_AUTHORITY_INTEGRATION=1', allow_module_level=True)
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src/account_data_api'))
sys.path.insert(0, str(ROOT/'src'))
import boto3
from moto import mock_aws
from service import AccountDeletionService
spec = importlib.util.spec_from_file_location('status_http_app', ROOT/'src/account_data_api/app.py')
app = importlib.util.module_from_spec(spec); spec.loader.exec_module(app)
SUBJECT = '01993d31-cafe-7abc-8abc-0123456789ab'
OPERATION = '11111111-1111-4111-8111-111111111111'

@pytest.mark.parametrize('method', ['GET', 'POST'])
@pytest.mark.parametrize('malformed', [False, True])
def test_persisted_command_http_json_and_replay_preserve_original_state(monkeypatch, method, malformed):
    with mock_aws():
        resource = boto3.resource('dynamodb', region_name='us-east-1')
        ledger = resource.create_table(TableName='synthetic-ledger', KeySchema=[{'AttributeName':'PK','KeyType':'HASH'}, {'AttributeName':'SK','KeyType':'RANGE'}], AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'}, {'AttributeName':'SK','AttributeType':'S'}], BillingMode='PAY_PER_REQUEST')
        now = int(time.time())
        command = {'PK':'ACCOUNT#'+SUBJECT,'SK':'ACCOUNT_DELETION','schemaVersion':1,'recordVersion':1,'environment':'dev','eventType':'account.deletion.requested','accountId':SUBJECT,'operationId':OPERATION,'status':'REQUESTED','occurredAtEpoch':Decimal(now-10),'deleteByEpoch':Decimal(now-10+86400)}
        if malformed:command['occurredAtEpoch'] += Decimal('0.5')
        ledger.put_item(Item=command)
        service = AccountDeletionService(environment='dev', ledger_table=ledger, users_table_name='unused', ledger_table_name=ledger.name, dynamodb_client=Mock(), required_components=('HISTORY',))
        monkeypatch.setattr(app.config, 'validate_config', lambda:None)
        monkeypatch.setattr(app.config, 'APP_ENVIRONMENT', 'dev')
        monkeypatch.setattr(app.config, 'COGNITO_ISSUER', 'synthetic-issuer')
        monkeypatch.setattr(app.config, 'COGNITO_APP_CLIENT_ID', 'synthetic-client')
        monkeypatch.setenv('ACCOUNT_DELETION_HTTP_SUBJECTS_JSON',json.dumps([SUBJECT]))
        monkeypatch.setattr(app, '_service', lambda:service)
        cleanup = Mock();monkeypatch.setattr(app,'_attempt_post_fence_cleanup',cleanup)
        claims={'sub':SUBJECT,'iss':'synthetic-issuer','client_id':'synthetic-client','token_use':'access','exp':str(now+600),'scope':app.config.COGNITO_REQUIRED_SCOPE,'auth_time':str(now),'iat':str(now)}
        event={'routeKey':method+' /v1/users/account-deletion','requestContext':{'authorizer':{'jwt':{'claims':claims}}}}
        if method=='POST':event['body']=json.dumps({'schemaVersion':1,'action':'DELETE_ACCOUNT','operationId':OPERATION})
        before=ledger.scan(ConsistentRead=True)['Items']
        result=app.lambda_handler(event,None);body=json.loads(result['body'])
        if malformed:
            assert result['statusCode']==500 and body['error']['code']=='INTERNAL_ERROR'
            cleanup.assert_not_called()
        else:
            assert result['statusCode']==(200 if method=='GET' else 202)
            assert body['status']=='REQUESTED' and body['operationId']==OPERATION
            assert type(body['requestedAtEpoch']) is int and body['requestedAtEpoch']==now-10
            assert type(body['deleteByEpoch']) is int and body['deleteByEpoch']==now-10+86400
            assert body['components']==[{'component':'HISTORY','status':'PENDING'}]
        assert ledger.scan(ConsistentRead=True)['Items']==before
        service.dynamodb_client.transact_write_items.assert_not_called()
