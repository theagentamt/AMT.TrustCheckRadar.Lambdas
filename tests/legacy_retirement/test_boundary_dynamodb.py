"""Isolated real SDK: retirement reads must never mutate an item or redispatch."""
import copy
from decimal import Decimal
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run isolated with AMT_AUTHORITY_INTEGRATION=1', allow_module_level=True)
os.environ.update(AWS_DEFAULT_REGION='us-east-1', AWS_ACCESS_KEY_ID='testing', AWS_SECRET_ACCESS_KEY='testing', AWS_EC2_METADATA_DISABLED='true')
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src/conversation_analysis'), str(ROOT/'src')]
import boto3
from moto import mock_aws
from retired_replay import LegacyReplay, payload_digest, digest
from errors import AppError
from shared_history.errors import HistoryError
from shared_history.contracts import build_history_items
from shared_entitlements import service as entitlements

NOW = 1800000000
PAYLOAD = {'requestId':'request-1', 'schemaVersion':'1.0', 'sourceType':'pasted_text',
 'localSanitizationApplied':True, 'sanitizedText':'reviewed', 'entities':[],
 'campaignConsentGranted':False, 'appFeatures':None}
RESPONSE = {'schemaVersion':'1.0','requestId':'request-1','scamScore':20,'riskLevel':'low',
 'confidence':Decimal('0.5'),'summary':'Bounded assessment','signals':[],'recommendedActions':['Check independently.']}
from datetime import datetime, UTC
STAMP = datetime.fromtimestamp(NOW-100, UTC).isoformat()
HASH = digest('a')
KEY = {'PK':'ANALYSIS#REQUEST#'+HASH,'SK':'request-1'}
CONSUMPTION_KEY = {'PK':'ANALYSIS#CONSUMPTION#'+HASH,'SK':'request-1'}
ROW = {**KEY,'status':'COMPLETED','payloadHash':payload_digest(PAYLOAD),'completedAt':STAMP,
 'expiresAt':NOW+800,'ttl':NOW+800,'response':RESPONSE,'createdAt':STAMP,'resultReadyAt':STAMP,'updatedAt':STAMP}
CONSUMPTION = {**CONSUMPTION_KEY,'accountIdHash':HASH,'consumptionType':'monthly','createdAt':STAMP,
 'expiresAt':NOW+800,'ttl':NOW+800}
SETTINGS = SimpleNamespace(users_table_name='users',deletion_ledger_table_name='ledger',
 device_bindings_table_name='devices',content_table_name='',control_table_name='',
 max_summary_bytes=4096,max_list_items=20,max_text_field_bytes=1024,schema_version=1,
 retention_days=90,dedup_retention_days=120)
EVENT = {'headers':{'x-device-binding-fingerprint':'fp'}}

@pytest.fixture
def world():
 with mock_aws():
  resource=boto3.resource('dynamodb',region_name='us-east-1')
  for name in ('abuse','users','ledger','devices','control','content'):
   resource.create_table(TableName=name,KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}],BillingMode='PAY_PER_REQUEST')
  resource.Table('users').put_item(Item={'PK':'USER#a','SK':'PROFILE','sub':'a','status':'ACTIVE','ageVerified':True})
  resource.Table('devices').put_item(Item={'PK':'USER#a','SK':'ACTIVE_BINDING','recordType':'ACTIVE_BINDING_POINTER','stateVersion':1,'bindingFingerprint':'fp'})
  resource.Table('devices').put_item(Item={'PK':'USER#a','SK':'DEVICE#fp','accountId':'a','status':'ACTIVE','bindingFingerprint':'fp'})
  yield resource, LegacyReplay(resource=resource,settings=copy.copy(SETTINGS),abuse_table_name='abuse',now=lambda:NOW), None


def seed(resource):
 resource.Table('abuse').put_item(Item=ROW)
 resource.Table('abuse').put_item(Item=CONSUMPTION)


def snapshots(resource):
 return {name:sorted(resource.Table(name).scan()['Items'],key=lambda x:(x['PK'],x['SK'])) for name in ('abuse','users','ledger','devices','control','content')}


def test_replay_owned_completed_requires_original_consumption_and_changes_nothing(world):
 resource,replay,device=world;seed(resource);before=snapshots(resource)
 assert replay.replay(EVENT,'a',PAYLOAD)==RESPONSE
 assert replay.replay(EVENT,'a',PAYLOAD)==RESPONSE
 assert snapshots(resource)==before


@pytest.mark.parametrize('status',[None,'PROCESSING','RETRYABLE','RESULT_READY','COMPLETED_ERASED','unknown'])
def test_missing_or_ambiguous_never_becomes_work(world,status):
 resource,replay,_=world
 if status is not None:resource.Table('abuse').put_item(Item=ROW|{'status':status})
 before=snapshots(resource)
 with pytest.raises(AppError):replay.replay(EVENT,'a',PAYLOAD)
 assert snapshots(resource)==before


@pytest.mark.parametrize('change',[{'payloadHash':'x'*64},{'expiresAt':NOW-1},{'ttl':NOW+801},{'completedAt':'invalid'},
 {'expiresAt':NOW+100000,'ttl':NOW+100000},{'response':RESPONSE|{'rawText':'sensitive'}},{'unknownSchemaField':1},{'schemaVersion':2}])
def test_invalid_original_evidence_fails_closed(world,change):
 resource,replay,_=world;seed(resource);resource.Table('abuse').put_item(Item=ROW|change)
 before=snapshots(resource)
 with pytest.raises((AppError,HistoryError)):replay.replay(EVENT,'a',PAYLOAD)
 assert snapshots(resource)==before


@pytest.mark.parametrize('change',[None,{'consumptionType':'invented'},{'accountIdHash':'other'},{'expiresAt':NOW+801},{'createdAt':'another period'}])
def test_missing_or_mismatched_accounting_cannot_be_reconstructed(world,change):
 resource,replay,_=world;seed(resource)
 if change is None:resource.Table('abuse').delete_item(Key=CONSUMPTION_KEY)
 else:resource.Table('abuse').put_item(Item=CONSUMPTION|change)
 with pytest.raises(AppError):replay.replay(EVENT,'a',PAYLOAD)


def test_other_owner_cannot_read_replay(world):
 resource,replay,_=world;seed(resource)
 with pytest.raises(HistoryError):replay.replay(EVENT,'b',PAYLOAD)


def test_deletion_between_reads_blocks_release(world):
 resource,replay,_=world;seed(resource)
 original=replay.authority;calls=0
 def authority(event,account):
  nonlocal calls
  calls+=1
  if calls==2:resource.Table('ledger').put_item(Item={'PK':'ACCOUNT#a','SK':'ACCOUNT_DELETION','status':'REQUESTED'})
  return original(event,account)
 replay.authority=authority
 with pytest.raises(HistoryError):replay.replay(EVENT,'a',PAYLOAD)


def seed_history(resource,replay):
 replay.settings.control_table_name='control';replay.settings.content_table_name='content'
 resource.Table('control').put_item(Item={'PK':'USER#a','SK':'STATE','accountStatus':'ACTIVE','historyGeneration':0})
 content,locator=build_history_items(account_id='a',payload_hash=ROW['payloadHash'],accepted={'historyGeneration':0,'recognitionGeneration':0,'acceptedSequence':1,'acceptedAtEpochMs':(NOW-120)*1000},payload=PAYLOAD,response=RESPONSE,now_epoch=NOW-100,settings=replay.settings)
 resource.Table('control').put_item(Item=locator);resource.Table('content').put_item(Item=content)
 return locator


def test_history_visible_evidence_remains_required_even_flags_disabled(world):
 resource,replay,_=world;seed(resource);seed_history(resource,replay)
 assert replay.replay(EVENT,'a',PAYLOAD)==RESPONSE
 resource.Table('control').update_item(Key={'PK':'USER#a','SK':'STATE'},UpdateExpression='SET historyGeneration = :g',ExpressionAttributeValues={':g':1})
 with pytest.raises(AppError):replay.replay(EVENT,'a',PAYLOAD)


def test_absent_history_locators_never_fall_back_to_old_response(world):
 resource,replay,_=world;seed(resource);locator=seed_history(resource,replay)
 resource.Table('control').delete_item(Key={'PK':locator['PK'],'SK':locator['SK']})
 with pytest.raises(AppError):replay.replay(EVENT,'a',PAYLOAD)


def test_history_erasure_between_read_and_release_is_not_replayed(world):
 resource,replay,_=world;seed(resource);locator=seed_history(resource,replay)
 original=replay._history
 def history(*args):
  result=original(*args)
  resource.Table('control').delete_item(Key={'PK':locator['PK'],'SK':locator['SK']})
  return result
 replay._history=history
 with pytest.raises(AppError):replay.replay(EVENT,'a',PAYLOAD)


def test_missing_history_configuration_preserves_unknown_work(world):
 resource,replay,_=world;seed(resource)
 resource.Table('abuse').put_item(Item=ROW|{'historyAuthorization':{'historyGeneration':0}})
 with pytest.raises(AppError):replay.replay(EVENT,'a',PAYLOAD)


def test_bonus_helper_preserves_usage_paid_binding_and_legacy_row(world):
 legacy={'accountId':'a','entitlementTier':'FREE','monthlyScanLimit':15,'remainingMonthlyScans':8,'remainingCredits':2}
 with patch.object(entitlements,'load_campaign_participation',side_effect=AssertionError('research must not be consulted')):
  assert entitlements.apply_campaign_participation_allowance('a',legacy)==legacy
  paid={**legacy,'entitlementTier':'PRO','monthlyScanLimit':100,'remainingMonthlyScans':37,
   'billingPeriodStartUtc':'2026-09-01T00:00:00Z','purchaseTokenHash':'same-owner-token'}
  verification={'normalizedStatus':'active','isAccessGranted':True,'billingPeriodStartUtc':paid['billingPeriodStartUtc'],'billingPeriodEndUtc':'2026-10-01T00:00:00Z'}
  result=entitlements.apply_google_play_subscription(paid,verification,{'platform':'google_play','productId':'trustcheck_radar_pro_monthly','purchaseTokenHash':'same-owner-token'},'2026-09-21T00:00:00Z')
  assert result['remainingMonthlyScans']==37 and result['remainingCredits']==2
  assert result['purchaseTokenHash']=='same-owner-token'
  assert legacy['monthlyScanLimit']==15 and legacy['remainingMonthlyScans']==8


def test_old_notice_never_authorizes_publication(world):
 resource,_,_=world
 original={'PK':'USER#a','SK':'CAMPAIGN_PARTICIPATION','state':'enrolled','stateVersion':1,
  'consentEpochId':'9947a35d-c734-4eba-af93-ce5a0143f5e6','noticeVersion':'2026-09-07','policyVersion':'policy-1'}
 resource.Table('users').put_item(Item=original)
 with patch.object(entitlements,'participation_table',resource.Table('users')):
  assert entitlements.load_campaign_participation('a')['state']=='not_enrolled'
 assert resource.Table('users').get_item(Key={'PK':'USER#a','SK':'CAMPAIGN_PARTICIPATION'})['Item']==original


def test_planner_only_aggregates_unknown_and_ambiguous_without_input_identifiers():
 spec=importlib.util.spec_from_file_location('retirement_plan',ROOT/'tools/legacy_access_inventory/plan.py')
 planner=importlib.util.module_from_spec(spec);spec.loader.exec_module(planner)
 records=[{'family':'request','item':ROW|{'status':state}} for state in ('PROCESSING','RESULT_READY','COMPLETED','COMPLETED_ERASED')]
 records += [{'family':'entitlement','item':{'PK':'USER#secret-account','SK':'ENTITLEMENT','entitlementTier':'FREE','monthlyScanLimit':True,'remainingMonthlyScans':10,'remainingCredits':0}}]
 report=planner.plan(json.loads(json.dumps({'schemaVersion':1,'records':records},default=float)))
 assert report['inputRecordCount']==5 and report['classifications']['unknown_shape']==1
 assert not report['applyAvailable'] and not report['inventoryComplete']
 assert HASH not in json.dumps(report) and 'secret-account' not in json.dumps(report)


@pytest.mark.parametrize('change',['missing_pointer','replaced_pointer','revoked_device'])
def test_authoritative_device_evidence_is_required_and_stable(world,change):
 resource,replay,_=world;seed(resource)
 if change=='missing_pointer':
  resource.Table('devices').delete_item(Key={'PK':'USER#a','SK':'ACTIVE_BINDING'})
 else:
  original=replay._history
  def history(*args):
   result=original(*args)
   if change=='replaced_pointer':
    resource.Table('devices').update_item(Key={'PK':'USER#a','SK':'ACTIVE_BINDING'},UpdateExpression='SET stateVersion=:v',ExpressionAttributeValues={':v':2})
   else:
    resource.Table('devices').update_item(Key={'PK':'USER#a','SK':'DEVICE#fp'},UpdateExpression='SET #s=:v',ExpressionAttributeNames={'#s':'status'},ExpressionAttributeValues={':v':'INACTIVE'})
   return result
  replay._history=history
 with pytest.raises(AppError) as error: replay.replay(EVENT,'a',PAYLOAD)
 assert error.value.code=='DEVICE_BINDING_MISMATCH'
