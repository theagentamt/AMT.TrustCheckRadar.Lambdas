"""Isolated real-SDK transaction coverage for privacy admission eligibility."""
import os
import sys
from pathlib import Path
from uuid import uuid4
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run isolated with AMT_AUTHORITY_INTEGRATION=1', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/account_data_api'))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from shared_account_finalization.service import REQUIRED_COMPONENTS
import boto3
from moto import mock_aws
from service import AccountDeletionService
from errors import AppError

@pytest.mark.parametrize('status,age,subject,accepted', [
    ('PENDING_AGE_GATE', False, 'account-a', True),
    ('ACTIVE', True, 'account-a', True),
    ('PENDING_AGE_GATE', False, 'other-owner', False),
    ('SUSPENDED', True, 'account-a', False),
])
def test_owned_incomplete_onboarding_can_delete_without_analysis_eligibility(status, age, subject, accepted):
    with mock_aws():
        resource = boto3.resource('dynamodb', region_name='us-east-1')
        for name in ('users', 'ledger'):
            resource.create_table(TableName=name, KeySchema=[{'AttributeName':'PK','KeyType':'HASH'}, {'AttributeName':'SK','KeyType':'RANGE'}], AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'}, {'AttributeName':'SK','AttributeType':'S'}], BillingMode='PAY_PER_REQUEST')
        resource.Table('users').put_item(Item={'PK':'USER#account-a','SK':'PROFILE','sub':subject,'status':status,'ageVerified':age})
        resource.Table('ledger').put_item(Item={'PK':'INVENTORY#dev','SK':'ACCOUNT_DATA_INVENTORY','recordType':'ACCOUNT_DATA_INVENTORY','schemaVersion':1,'revision':1,'environment':'dev','coverage':'VERIFIED_COMPLETE','manifestSha256':'a'*64,'requiredComponents':list(REQUIRED_COMPONENTS),'usernameIsSubVerified':True,'approvedAtEpoch':1799999999})
        service = AccountDeletionService(environment='dev', ledger_table=resource.Table('ledger'), users_table_name='users', ledger_table_name='ledger', dynamodb_client=boto3.client('dynamodb',region_name='us-east-1'), required_components=REQUIRED_COMPONENTS,inventory_manifest_sha256='a'*64,inventory_revision=1, now=lambda:1800000000)
        if accepted:
            assert service.request('account-a', str(uuid4()))['status'] == 'REQUESTED'
        else:
            with pytest.raises(AppError, match='Account state changed'):
                service.request('account-a', str(uuid4()))
            assert resource.Table('ledger').get_item(Key={'PK':'ACCOUNT#account-a','SK':'ACCOUNT_DELETION'}).get('Item') is None
