"""Synthetic isolated SDK/Moto evidence for both History receipt writers."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run isolated with AMT_AUTHORITY_INTEGRATION=1',allow_module_level=True)
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
import boto3
from moto import mock_aws
from history_account_deletion_bridge.service import start_history_deletion
from history_lifecycle.service import HistoryLifecycleService
from shared_account_finalization.history_receipts import write_receipt
from shared_account_finalization.service import RETENTION_SECONDS,FinalizationError

NOW=1800000000
COMMAND={'PK':'ACCOUNT#a','SK':'ACCOUNT_DELETION','schemaVersion':1,'recordVersion':1,
 'environment':'dev','eventType':'account.deletion.requested','accountId':'a',
 'operationId':'3fefbf1a-caf4-4e72-ab61-4fb36bf925b4','status':'REQUESTED',
 'occurredAtEpoch':NOW-100,'deleteByEpoch':NOW-100+86400}
TERMINAL=COMMAND|{'status':'COMPLETE','eventType':'account.deletion.completed',
 'completedAtEpoch':NOW-10,'retainUntilEpoch':NOW-10+RETENTION_SECONDS}
JOB={'PK':'USER#a','SK':'ERASURE#test','reason':'ACCOUNT_DELETION','status':'PENDING',
 'stage':'REPLAY','historyGeneration':0,'maxHistoryGeneration':0,'operationId':'test',
 'deletionLedgerPK':'ACCOUNT#a','deletionLedgerSK':'ACCOUNT_DELETION',
 'deletionRequestedAtEpoch':NOW-100,'deletionOperationId':COMMAND['operationId']}

@pytest.fixture
def world():
 with mock_aws():
  client=boto3.client('dynamodb',region_name='us-east-1')
  resource=boto3.resource('dynamodb',region_name='us-east-1')
  for name in ('ledger','control'):
   client.create_table(TableName=name,KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}],BillingMode='PAY_PER_REQUEST')
  ledger=resource.Table('ledger');control=resource.Table('control')
  ledger.put_item(Item=COMMAND)
  yield client,ledger,control


def bridge(world,client=None,now=NOW):
 d,ledger,control=world
 return start_history_deletion(COMMAND,control_table=control,control_table_name='control',
  deletion_ledger_table=ledger,deletion_ledger_table_name='ledger',dynamodb_client=client or d,
  schema_version=1,erasure_sla_hours=24,now_epoch=now)


def receipt(ledger):
 return ledger.get_item(Key={'PK':'ACCOUNT#a','SK':'ACCOUNT_DELETION#HISTORY'},ConsistentRead=True).get('Item')


def lifecycle(world,client=None):
 d,ledger,control=world
 return HistoryLifecycleService(settings=SimpleNamespace(environment='dev',schema_version=1,
  control_table_name='control',deletion_ledger_table_name='ledger'),content_table=object(),
  control_table=control,abuse_table=object(),deletion_ledger_table=ledger,
  dynamodb_client=client or d,now=lambda:NOW)


def proxy(client,callback):
 class Client:
  def transact_write_items(self,**kwargs):return callback(kwargs)
 return Client()


def test_missing_history_receipt_is_atomic_and_retry_keeps_deadline(world):
 d,ledger,control=world
 assert bridge(world)['receiptCreated']
 original=receipt(ledger)
 assert original['occurredAtEpoch']==NOW and original['retainUntilEpoch']==NOW+RETENTION_SECONDS
 assert not bridge(world,now=NOW+30)['receiptCreated']
 assert receipt(ledger)==original


@pytest.mark.parametrize('elapsed',[0,RETENTION_SECONDS+1])
def test_terminal_fence_never_recreates_removed_receipt_even_after_retention(world,elapsed):
 d,ledger,control=world;ledger.put_item(Item=TERMINAL)
 assert bridge(world,now=NOW+elapsed)['alreadyCompleted']
 assert receipt(ledger) is None and control.scan()['Items']==[]


def test_forged_or_other_operation_terminal_is_not_success(world):
 d,ledger,control=world
 ledger.put_item(Item=TERMINAL|{'operationId':'8d155963-d41c-476f-a773-9c738f95f703'})
 with pytest.raises(FinalizationError):bridge(world)
 assert receipt(ledger) is None


def test_terminal_race_between_read_and_receipt_transaction_is_safe(world):
 d,ledger,control=world
 def race(kwargs):
  ledger.put_item(Item=TERMINAL)
  return d.transact_write_items(**kwargs)
 assert bridge(world,client=proxy(d,race))['alreadyCompleted']
 assert receipt(ledger) is None


def test_history_state_appearing_during_no_history_receipt_cancels_transaction(world):
 d,ledger,control=world
 def race(kwargs):
  control.put_item(Item={'PK':'USER#a','SK':'STATE','accountStatus':'ACTIVE'})
  return d.transact_write_items(**kwargs)
 with pytest.raises(Exception):bridge(world,client=proxy(d,race))
 assert receipt(ledger) is None


def test_lost_ack_reads_original_receipt_without_extending_retention(world):
 d,ledger,control=world
 def lost(kwargs):
  d.transact_write_items(**kwargs)
  raise RuntimeError('synthetic lost acknowledgment')
 assert not bridge(world,client=proxy(d,lost))['receiptCreated']
 original=receipt(ledger)
 assert not bridge(world,now=NOW+20)['receiptCreated']
 assert receipt(ledger)==original


def test_requested_operation_change_cannot_create_receipt(world):
 d,ledger,control=world
 def race(kwargs):
  ledger.put_item(Item=COMMAND|{'operationId':'8d155963-d41c-476f-a773-9c738f95f703'})
  return d.transact_write_items(**kwargs)
 with pytest.raises(FinalizationError):bridge(world,client=proxy(d,race))
 assert receipt(ledger) is None


def test_lifecycle_writer_uses_identical_guard_and_preserves_receipt(world):
 d,ledger,control=world
 control.put_item(Item={'PK':'USER#a','SK':'STATE','accountStatus':'DELETED'})
 service=lifecycle(world)
 assert service._write_account_deletion_receipt(JOB,NOW)['receiptCreated']
 original=receipt(ledger)
 assert not service._write_account_deletion_receipt(JOB,NOW+15)['receiptCreated']
 assert receipt(ledger)==original


def test_lifecycle_receipt_race_cannot_recreate_after_terminal(world):
 d,ledger,control=world
 control.put_item(Item={'PK':'USER#a','SK':'STATE','accountStatus':'DELETED'})
 def race(kwargs):
  ledger.put_item(Item=TERMINAL)
  return d.transact_write_items(**kwargs)
 assert lifecycle(world,proxy(d,race))._write_account_deletion_receipt(JOB,NOW)['terminal']
 assert receipt(ledger) is None


def test_restored_pending_job_with_terminal_fence_does_no_cleanup_or_receipt_write(world):
 d,ledger,control=world;ledger.put_item(Item=TERMINAL)
 service=lifecycle(world)
 assert service._process_erasure_job(JOB,NOW)==(False,0)
 assert receipt(ledger) is None and control.scan()['Items']==[]


def test_lifecycle_cannot_bind_another_account_job(world):
 with pytest.raises(Exception):
  lifecycle(world)._write_account_deletion_receipt(JOB|{'PK':'USER#victim'},NOW)
 assert receipt(world[1]) is None


def test_active_history_job_start_cannot_race_terminal_fence(world):
 d,ledger,control=world
 control.put_item(Item={'PK':'USER#a','SK':'STATE','accountStatus':'ACTIVE',
  'historyGeneration':0,'recognitionGeneration':0,'acceptedSequence':0})
 def race(kwargs):
  ledger.put_item(Item=TERMINAL)
  return d.transact_write_items(**kwargs)
 assert bridge(world,client=proxy(d,race))['alreadyCompleted']
 assert len(control.scan()['Items'])==1
 assert control.scan()['Items'][0]['accountStatus']=='ACTIVE'


def test_terminal_race_through_lifecycle_completion_does_not_extend_job_retention(world):
 d,ledger,control=world
 control.put_item(Item={'PK':'USER#a','SK':'STATE','accountStatus':'DELETING'})
 control.put_item(Item=JOB)
 def race(kwargs):
  ledger.put_item(Item=TERMINAL)
  return d.transact_write_items(**kwargs)
 service=lifecycle(world,proxy(d,race))
 assert service._complete_erasure(JOB,0,NOW) is False
 assert receipt(ledger) is None
 assert control.get_item(Key={'PK':JOB['PK'],'SK':JOB['SK']})['Item']==JOB


def test_forged_receipt_numeral_type_is_not_reused(world):
 d,ledger,control=world
 bridge(world)
 existing=receipt(ledger)
 ledger.put_item(Item=existing|{'schemaVersion':True})
 with pytest.raises(FinalizationError):bridge(world)
