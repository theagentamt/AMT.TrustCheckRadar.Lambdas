"""Isolated DynamoDB lifecycle integration; synthetic data only."""
import os,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run isolated with AMT_AUTHORITY_INTEGRATION=1',allow_module_level=True)
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src/account_data_api'))
import boto3
from moto import mock_aws
from shared_account_finalization.service import Finalizer,FinalizationError,REQUIRED_COMPONENTS
from shared_purchase_ownership import OwnershipStore
from lifecycle import Lifecycle
from service import AccountDeletionService,AppError

NOW=1800000000

@pytest.fixture
def world():
    with mock_aws():
        resource=boto3.resource('dynamodb',region_name='us-east-1');client=boto3.client('dynamodb',region_name='us-east-1')
        for name in ('ledger','users','entitlements'):
            resource.create_table(TableName=name,KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}],BillingMode='PAY_PER_REQUEST')
        ledger=resource.Table('ledger');table=resource.Table('entitlements')
        command={'PK':'ACCOUNT#account-a','SK':'ACCOUNT_DELETION','schemaVersion':1,'recordVersion':1,'environment':'dev','eventType':'account.deletion.requested','accountId':'account-a','operationId':'ee9a078b-ae70-4d6f-8229-e8a34a72c9ec','status':'REQUESTED','occurredAtEpoch':NOW-10,'deleteByEpoch':NOW-10+86400}
        inventory={'PK':'INVENTORY#dev','SK':'ACCOUNT_DATA_INVENTORY','recordType':'ACCOUNT_DATA_INVENTORY','schemaVersion':1,'revision':1,'environment':'dev','coverage':'VERIFIED_COMPLETE','manifestSha256':'a'*64,'requiredComponents':list(REQUIRED_COMPONENTS),'usernameIsSubVerified':True,'approvedAtEpoch':NOW-20}
        ledger.put_item(Item=command);ledger.put_item(Item=inventory)
        table.put_item(Item={'PK':'PURCHASE#CONTROL','SK':'OWNERSHIP_INVENTORY','recordType':'PURCHASE_OWNERSHIP_INVENTORY','schemaVersion':1,'revision':1,'coverage':'VERIFIED_COMPLETE','environment':'dev'})
        ownership=OwnershipStore(table=table,ledger=ledger,users_table_name='users',table_name='entitlements',ledger_table_name='ledger',client=client,environment='dev',now=lambda:NOW)
        finalizer=Finalizer(ledger_table=ledger,ledger_table_name='ledger',client=client,cognito=SimpleNamespace(),user_pool_id='us-east-1_example',environment='dev',now=lambda:NOW,manifest_sha256='a'*64,inventory_revision=1)
        lifecycle=Lifecycle(ledger=ledger,ledger_name='ledger',client=client,ownership=ownership,finalizer=finalizer,environment='dev',manifest_sha256='a'*64,inventory_revision=1,now=lambda:NOW)
        yield lifecycle,ledger,table,command,inventory


def test_only_verified_empty_pass_writes_guarded_receipt(world):
    lifecycle,ledger,table,command,_=world
    table.put_item(Item={'PK':'USER#account-a','SK':'ENTITLEMENT','subscriptionStatus':'expired'})
    assert lifecycle.entitlements(command)=={'complete':False,'deleted':1}
    key={'PK':command['PK'],'SK':'ACCOUNT_DELETION#ENTITLEMENTS'}
    assert ledger.get_item(Key=key).get('Item') is None
    assert lifecycle.entitlements(command)=={'complete':True,'deleted':0}
    receipt=ledger.get_item(Key=key)['Item']
    assert receipt['requestOccurredAtEpoch']==command['occurredAtEpoch'] and receipt['retainUntilEpoch']==NOW+120*86400
    assert lifecycle.entitlements(command)=={'complete':True,'deleted':0}


@pytest.mark.parametrize('target',['account','purchase'])
def test_inventory_race_prevents_receipt(world,target):
    lifecycle,ledger,table,command,inventory=world
    original=lifecycle.ownership.delete_owned_batch
    def race(*a,**kw):
        result=original(*a,**kw)
        selected=ledger if target=='account' else table
        key={'PK':'INVENTORY#dev','SK':'ACCOUNT_DATA_INVENTORY'} if target=='account' else {'PK':'PURCHASE#CONTROL','SK':'OWNERSHIP_INVENTORY'}
        selected.update_item(Key=key,UpdateExpression='SET revision=:r',ExpressionAttributeValues={':r':2})
        return result
    lifecycle.ownership.delete_owned_batch=race
    with pytest.raises(RuntimeError,match='receipt unconfirmed'):lifecycle.entitlements(command)
    assert ledger.get_item(Key={'PK':command['PK'],'SK':'ACCOUNT_DELETION#ENTITLEMENTS'}).get('Item') is None


def test_same_second_or_preapproval_command_never_starts_cleanup(world):
    lifecycle,ledger,table,command,inventory=world
    ledger.put_item(Item=inventory|{'approvedAtEpoch':command['occurredAtEpoch']})
    with pytest.raises(FinalizationError,match='INVENTORY_UNVERIFIED'):lifecycle.entitlements(command)


def test_default_disabled_finalizer_no_identity_call(world):
    lifecycle,_,_,command,_=world
    assert lifecycle.finalize(command)=={'complete':False,'policyBlocked':True}


def test_completed_fixed_fence_skips_stale_stream_and_denies_authenticated_poll(world):
    lifecycle,ledger,_,command,_=world
    ledger.put_item(Item=command|{'status':'COMPLETE','eventType':'account.deletion.completed','completedAtEpoch':NOW,'retainUntilEpoch':NOW+120*86400})
    assert lifecycle.verify(command) is None
    service=AccountDeletionService(environment='dev',ledger_table=ledger,users_table_name='users',ledger_table_name='ledger',dynamodb_client=lifecycle.client,required_components=REQUIRED_COMPONENTS,now=lambda:NOW)
    with pytest.raises(AppError) as error:service.status('account-a')
    assert error.value.code=='UNAUTHORIZED'
    with pytest.raises(AppError) as error:service.request('account-a',command['operationId'])
    assert error.value.code=='UNAUTHORIZED'
