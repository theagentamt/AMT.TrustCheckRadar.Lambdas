"""Real SDK/Moto transaction composition; every Play response is synthetic."""
import os,sys
from pathlib import Path
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('Isolated authority integration',allow_module_level=True)
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'tests/shared_check_authority'));sys.path.insert(0,str(ROOT/'tests/shared_play_verification'))
from test_transactions import world,ACCOUNT,PAYLOAD,WORKER
from test_proof import fixture,TOKEN,NOW
from copy import deepcopy
from shared_purchase_ownership.service import OwnershipStore,INVENTORY_KEY,OwnershipError,owner_key,token_hash
from shared_check_authority.entitlements import EntitlementWriter
from shared_check_authority.core import AuthorityError
from shared_play_verification.proof import PRODUCT,PlayVerificationError
from v1_play_handoff.service import Handoff,account_binding

RID='f3b9c62a-4e6f-4b38-9d1a-233341aec322'
class Client:
 def __init__(self):self.head,self.ordered=fixture();self.head['externalAccountIdentifiers']['obfuscatedExternalAccountId']=account_binding(ACCOUNT);self.head['testPurchase']={};self.calls=0;self.acks=0;self.hook=lambda:None;self.fail_ack=False
 def subscription(self,token):self.calls+=1;self.hook();return deepcopy(self.head)
 def order(self,order):return deepcopy(self.ordered)
 def acknowledge(self,token):
  self.acks+=1
  if self.fail_ack:raise PlayVerificationError('PLAY_PROVIDER_UNAVAILABLE')
  self.head['acknowledgementState']='ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED'

@pytest.fixture
def handoff(world):
 a,e,put,row,change,clock=world;clock[0]=NOW;e['requestContext']['authorizer']['jwt']['claims']['exp']=str(NOW+90)
 pk=a._partition(ACCOUNT,'k1')
 for sk in ('ACCESS','PERIOD#p1'):a.ddb.Table('authority').delete_item(Key={'PK':pk,'SK':sk})
 a.ddb.create_table(TableName='ownership',BillingMode='PAY_PER_REQUEST',KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}])
 table=a.ddb.Table('ownership');table.put_item(Item={**INVENTORY_KEY,'recordType':'PURCHASE_OWNERSHIP_INVENTORY','schemaVersion':1,'revision':1,'coverage':'VERIFIED_COMPLETE','environment':'dev'})
 owner=OwnershipStore(table=table,ledger=a.ddb.Table('deletion'),users_table_name='users',table_name='ownership',ledger_table_name='deletion',client=a.ddb.meta.client,environment='dev',now=a.now)
 w=EntitlementWriter(a,approved_products=frozenset({('google_play',PRODUCT)}),operator_principals=frozenset(),verification_max_age_seconds=20)
 c=Client();h=Handoff(w,owner,c,allow_test=True,require_test=True)
 return h,world,{'schemaVersion':1,'requestId':RID,'intent':'PURCHASE','purchaseToken':TOKEN}

def test_atomic_grant_ack_and_same_request_replay_preserves_spending(handoff):
 h,world,p=handoff;a,e,_,row,_,_=world
 out=h.handle(e,p);assert out['status']=='committed' and out['acknowledgment']=='acknowledged' and h.client.acks==1
 period=row('PERIOD#'+row('ACCESS')['periodId']);assert period['limit']==200
 assert h.ownership._get(owner_key(token_hash(TOKEN)))['accountId']==ACCOUNT
 assert not any(x['SK'].startswith('ENTITLEMENT') for x in h.ownership.table.scan()['Items'])
 check=a.prepare(e,PAYLOAD,'one');admit=a.admit(e,PAYLOAD,check);a.settle(WORKER,check,admit['executionToken'],'complete')
 retry=h.handle(e,p);assert retry['idempotencyReplay'] and h.client.acks==1
 assert row(period['SK'])['usedChecks']==1

def test_ack_failure_keeps_durable_grant_retry_no_second_allowance(handoff):
 h,w,p=handoff;_,e,_,row,_,_=w;h.client.fail_ack=True
 assert h.handle(e,p)['acknowledgment']=='pending';rev=row('ACCESS')['revision']
 h.client.fail_ack=False;assert h.handle(e,p)['acknowledgment']=='acknowledged';assert row('ACCESS')['revision']==rev

def test_lost_commit_ack_reconciles_before_google_ack(handoff,monkeypatch):
 h,w,p=handoff;a,e,_,row,_,_=w;original=a._transact
 def lost(items):original(items);raise AuthorityError('TRANSACTION_UNCERTAIN')
 monkeypatch.setattr(a,'_transact',lost)
 assert h.handle(e,p)['status']=='committed' and h.client.acks==1 and row('ACCESS')

@pytest.mark.parametrize('kind',['device','deletion','inventory'])
def test_provider_race_no_grant_no_ack(handoff,kind):
 h,w,p=handoff;a,e,put,row,change,_=w
 def race():
  if h.client.calls!=2:return
  if kind=='device':change('devices','ACTIVE_BINDING',stateVersion=2)
  elif kind=='deletion':put('deletion','ACCOUNT_DELETION',status='REQUESTED')
  else:
   marker=h.ownership.inventory();marker['revision']=2;h.ownership.table.put_item(Item=marker)
 h.client.hook=race
 with pytest.raises((AuthorityError,OwnershipError)):h.handle(e,p)
 assert row('ACCESS') is None and h.client.acks==0 and h.ownership._get(owner_key(token_hash(TOKEN))) is None

def test_conflicting_request_rejected_before_provider(handoff):
 h,w,p=handoff;_,e,*_=w;h.handle(e,p);calls=h.client.calls
 with pytest.raises(PlayVerificationError,match='REQUEST_CONFLICT'):h.handle(e,{**p,'intent':'RESTORE'})
 assert h.client.calls==calls

def test_real_purchase_refused_on_dev(handoff):
 h,w,p=handoff;_,e,_,row,_,_=w;h.client.head.pop('testPurchase')
 with pytest.raises(PlayVerificationError,match='PLAY_TEST_PURCHASE_REQUIRED'):h.handle(e,p)
 assert row('ACCESS') is None and h.client.acks==0

def test_pending_proof_does_not_create_ownership(handoff):
 h,w,p=handoff;_,e,_,row,_,_=w;h.client.head['subscriptionState']='SUBSCRIPTION_STATE_PENDING'
 out=h.handle(e,p);assert out['status']=='pending' and out['acknowledgment']=='not_applicable'
 assert row('ACCESS') is None and h.client.acks==0 and h.ownership._get(owner_key(token_hash(TOKEN))) is None

def new_account(a,event,account='synthetic-new-account'):
 e=deepcopy(event);e['requestContext']['authorizer']['jwt']['claims']['sub']=account
 for table,row in [('users',{'PK':'USER#'+account,'SK':'PROFILE','sub':account,'status':'ACTIVE','ageVerified':True}),('devices',{'PK':'USER#'+account,'SK':'ACTIVE_BINDING','recordType':'ACTIVE_BINDING_POINTER','stateVersion':1,'bindingFingerprint':'device-one'}),('devices',{'PK':'USER#'+account,'SK':'DEVICE#device-one','accountId':account,'status':'ACTIVE','bindingFingerprint':'device-one'})]:a.ddb.Table(table).put_item(Item=row)
 return e,account

def test_restore_cannot_take_active_owner(handoff):
 h,w,p=handoff;a,e,*_=w;h.handle(e,p);other,account=new_account(a,e)
 with pytest.raises(OwnershipError,match='PURCHASE_OWNERSHIP_CONFLICT'):h.handle(other,{**p,'intent':'RESTORE'})
 assert a._get('authority',{'PK':a._partition(account,'k1'),'SK':'ACCESS'}) is None

def test_deletion_then_restore_retains_used_releases_pending_and_supports_renewal(handoff):
 from test_deletion import setup,finish
 h,w,p=handoff;a,e,_,row,_,clock=w;h.handle(e,p)
 check=a.prepare(e,PAYLOAD,'completed');admission=a.admit(e,PAYLOAD,check);a.settle(WORKER,check,admission['executionToken'],'complete')
 pending=a.prepare(e,PAYLOAD,'pending');a.admit(e,PAYLOAD,pending)
 local=row('PERIOD#'+row('ACCESS')['periodId']);key=local['purchaseUsageKey'];assert a._get('authority',key)['usedChecks']==1
 bridge,command,_=setup(w);finish(bridge,command)
 import boto3
 h.ownership.client=boto3.client('dynamodb',region_name='us-east-1')
 for _ in range(5):
  if h.ownership.delete_owned_batch(command)['complete']:break
 else:pytest.fail('ownership cleanup did not finish')
 global_row=a._get('authority',key);assert global_row['usedChecks']==1 and global_row['reservedChecks']==0 and ACCOUNT not in str(global_row)
 other,account=new_account(a,e);out=h.handle(other,{**p,'intent':'RESTORE'})
 assert out['status']=='committed'
 pk=a._partition(account,'k1');access=a._get('authority',{'PK':pk,'SK':'ACCESS'});period=a._get('authority',{'PK':pk,'SK':'PERIOD#'+access['periodId']})
 assert period['usedChecks']==1 and period['reservedChecks']==0 and period['limit']==200
 # Ordinary callbacks after explicit restoration reuse known ownership despite
 # Google's original obfuscated account binding remaining unchanged.
 assert h.handle(other,{**p,'requestId':'c3b9c62a-4e6f-4b38-9d1a-233341aec322'})['status']=='committed'
 assert a._get('authority',key)['usedChecks']==1
 clock[0]=1790899200;other['requestContext']['authorizer']['jwt']['claims']['exp']=str(clock[0]+90)
 h.client.head['lineItems'][0].update(expiryTime='2026-11-01T00:00:00Z',latestSuccessfulOrderId='GPA.1234-1234-1234-12345..3')
 h.client.ordered['orderId']='GPA.1234-1234-1234-12345..3'
 h.client.ordered['lineItems'][0]['subscriptionDetails'].update(servicePeriodStartTime='2026-10-01T00:00:00Z',servicePeriodEndTime='2026-11-01T00:00:00Z')
 assert h.handle(other,{**p,'requestId':'d3b9c62a-4e6f-4b38-9d1a-233341aec322'})['status']=='committed'
 new_access=a._get('authority',{'PK':pk,'SK':'ACCESS'});new_period=a._get('authority',{'PK':pk,'SK':'PERIOD#'+new_access['periodId']})
 assert new_period['usedChecks']==0 and new_period['limit']==200 and new_period['purchaseUsageKey']!=key
 assert a._get('authority',key)['usedChecks']==1

def test_unknown_deleted_account_usage_never_creates_fresh_allowance(handoff):
 h,w,p=handoff;a,e,_,row,_,_=w;other,account=new_account(a,e)
 with pytest.raises(AuthorityError,match='PURCHASE_USAGE_UNAVAILABLE'):h.handle(other,{**p,'intent':'RESTORE'})
 assert a._get('authority',{'PK':a._partition(account,'k1'),'SK':'ACCESS'}) is None and h.client.acks==0


def advance_renewal(h,w):
 _,e,_,_,_,clock=w;clock[0]=1790899200;e['requestContext']['authorizer']['jwt']['claims']['exp']=str(clock[0]+90)
 h.client.head['lineItems'][0].update(expiryTime='2026-11-01T00:00:00Z',latestSuccessfulOrderId='GPA.1234-1234-1234-12345..3')
 h.client.ordered['orderId']='GPA.1234-1234-1234-12345..3'
 h.client.ordered['lineItems'][0]['subscriptionDetails'].update(servicePeriodStartTime='2026-10-01T00:00:00Z',servicePeriodEndTime='2026-11-01T00:00:00Z')


def test_lost_ack_response_across_renewal_finishes_history_without_new_grant(handoff):
 h,w,p=handoff;a,e,_,row,_,_=w;h.handle(e,p)
 check=a.prepare(e,PAYLOAD,'spent');admit=a.admit(e,PAYLOAD,check);a.settle(WORKER,check,admit['executionToken'],'complete')
 old=row('ACCESS');key=row('PERIOD#'+old['periodId'])['purchaseUsageKey'];advance_renewal(h,w)
 out=h.handle(e,p)
 assert out['idempotencyReplay'] and out['acknowledgment']=='acknowledged' and h.client.acks==1
 assert row('ACCESS')==old and a._get('authority',key)['usedChecks']==1
 # Only a new explicitly correlated verification can write the new funded period.
 fresh=h.handle(e,{**p,'requestId':'e3b9c62a-4e6f-4b38-9d1a-233341aec322'})
 assert not fresh['idempotencyReplay'] and row('ACCESS')['periodId']!=old['periodId']
 assert row('PERIOD#'+row('ACCESS')['periodId'])['usedChecks']==0
 assert a._get('authority',key)['usedChecks']==1


@pytest.mark.parametrize('fence',['deletion','owner'])
def test_historical_ack_replay_still_checks_current_owner_and_deletion(handoff,fence):
 h,w,p=handoff;a,e,put,row,_,_=w;h.handle(e,p);advance_renewal(h,w)
 if fence=='deletion':put('deletion','ACCOUNT_DELETION',status='REQUESTED')
 else:
  owner=h.ownership._get(owner_key(token_hash(TOKEN)));owner['accountId']='another-owner';h.ownership.table.put_item(Item=owner)
 with pytest.raises((AuthorityError,OwnershipError)):h.handle(e,p)
 assert h.client.acks==1


def test_same_account_refresh_preserves_pending_reservation(handoff):
 h,w,p=handoff;a,e,_,row,_,_=w;h.handle(e,p)
 check=a.prepare(e,PAYLOAD,'reserved');a.admit(e,PAYLOAD,check)
 before=row('PERIOD#'+row('ACCESS')['periodId'])
 h.handle(e,{**p,'requestId':'e3b9c62a-4e6f-4b38-9d1a-233341aec322'})
 assert row(before['SK'])==before and a._get('authority',before['purchaseUsageKey'])['reservedChecks']==1


def test_new_owner_restore_waits_for_original_pending_reservation_cleanup(handoff):
 from test_deletion import setup
 import boto3
 h,w,p=handoff;a,e,_,row,_,_=w;h.handle(e,p)
 check=a.prepare(e,PAYLOAD,'pending');a.admit(e,PAYLOAD,check)
 key=row('PERIOD#'+row('ACCESS')['periodId'])['purchaseUsageKey'];_,command,_=setup(w)
 h.ownership.client=boto3.client('dynamodb',region_name='us-east-1')
 for _ in range(5):
  if h.ownership.delete_owned_batch(command)['complete']:break
 else:pytest.fail('ownership cleanup did not finish')
 other,account=new_account(a,e)
 with pytest.raises(AuthorityError,match='PURCHASE_USAGE_RECONCILIATION_REQUIRED'):h.handle(other,{**p,'intent':'RESTORE'})
 assert a._get('authority',key)['reservedChecks']==1
 assert a._get('authority',{'PK':a._partition(account,'k1'),'SK':'ACCESS'}) is None


@pytest.mark.parametrize('revision',[0,-1])
def test_invalid_existing_period_revision_cannot_commit_new_handoff(handoff,revision):
 h,w,p=handoff;a,e,_,row,change,_=w;h.handle(e,p)
 old=row('ACCESS');change('authority','PERIOD#'+old['periodId'],grantRevision=revision)
 before=a.ddb.Table('authority').scan()['Items'];owners=h.ownership.table.scan()['Items']
 with pytest.raises(AuthorityError,match='PURCHASE_USAGE_INVALID'):h.handle(e,{**p,'requestId':'e3b9c62a-4e6f-4b38-9d1a-233341aec322'})
 assert a.ddb.Table('authority').scan()['Items']==before and h.ownership.table.scan()['Items']==owners and h.client.acks==1
