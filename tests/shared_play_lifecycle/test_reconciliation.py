"""Inactive lifecycle core with real Moto transactions and synthetic store/KMS."""
import os,sys,importlib.util,base64,hashlib
from pathlib import Path
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('isolated SDK integration',allow_module_level=True)
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests/shared_check_authority'),str(ROOT/'tests/shared_play_verification')]
spec=importlib.util.spec_from_file_location('play_handoff_test_fixture',ROOT/'tests/v1_play_handoff/test_service.py');fixture_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture_module)
from test_transactions import world,ACCOUNT,PAYLOAD,WORKER
handoff=fixture_module.handoff
from shared_play_lifecycle.reconciliation import Reconciler
from shared_play_lifecycle.tokens import key as token_key
from shared_check_authority.entitlements import TrustedLifecycleWorker
from shared_check_authority.core import AuthorityError
from shared_purchase_ownership.service import owner_key,token_hash
from v1_entitlements.service import access_snapshot

ROLE='arn:aws:iam::107827791950:role/synthetic-play-lifecycle'
ARN='arn:aws:kms:us-east-1:107827791950:key/12345678-1234-1234-1234-123456789abc'
class Cipher:
 def encrypt(self,token,partition):return ARN,base64.b64encode(hashlib.sha256((partition+token).encode()).digest()).decode(),base64.b64encode(b'synthetic-wrapped-key').decode()

@pytest.fixture
def lifecycle(handoff):
 h,w,p=handoff;a,e,*_=w
 a.ddb.create_table(TableName='play-tokens',BillingMode='PAY_PER_REQUEST',KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}])
 h.writer.lifecycle_principals=frozenset({ROLE});h.lifecycle_enabled=True;h.token_cipher=Cipher();h.token_table='play-tokens'
 h.handle(e,p)
 reconciler=Reconciler(h.writer,h.ownership,h.client,TrustedLifecycleWorker(ROLE),token_table='play-tokens',token_cipher=h.token_cipher)
 return h,w,p,reconciler


def test_grace_preserves_funded_period_usage_and_real_snapshot(lifecycle):
 h,w,p,r=lifecycle;a,e,_,row,_,clock=w
 proof=a.prepare(e,PAYLOAD,'one');admission=a.admit(e,PAYLOAD,proof);a.settle(WORKER,proof,admission['executionToken'],'complete')
 old=row('PERIOD#'+row('ACCESS')['periodId']);clock[0]=1790899200
 h.client.head['subscriptionState']='SUBSCRIPTION_STATE_IN_GRACE_PERIOD';h.client.head['lineItems'][0]['expiryTime']='2026-10-08T00:00:00Z'
 result=r.refresh(p['purchaseToken'],'notification-grace');assert result['state']=='committed'
 period=row(old['SK']);usage=a._get('authority',period['purchaseUsageKey'])
 assert period['endEpoch']==old['endEpoch'] and period['usedChecks']==1 and period['limit']==200
 assert period['accessUntilEpoch']==1791417600 and usage['expiresAt']==1791417600+604800
 # User JWT expired, but no user/session fabrication was needed for background work.
 e['requestContext']['authorizer']['jwt']['claims']['exp']=str(clock[0]+90)
 snapshot=access_snapshot(h.writer,e);assert snapshot['allowance']['periodEndsAtEpoch']==1791417600 and snapshot['allowance']['remaining']==199
 next_check=a.prepare(e,PAYLOAD,'two');next_admission=a.admit(e,PAYLOAD,next_check);a.settle(WORKER,next_check,next_admission['executionToken'],'complete')
 assert a._get('authority',period['purchaseUsageKey'])['usedChecks']==2
 stored=a._get('play-tokens',token_key(a._partition(ACCOUNT,'k1'),token_hash(p['purchaseToken'])))
 assert stored['expiresAt']==usage['expiresAt'] and p['purchaseToken'] not in str(stored)


def test_duplicate_notification_reverifies_ack_without_granting_again(lifecycle):
 h,w,p,r=lifecycle;a,e,_,row,_,_=w
 r.refresh(p['purchaseToken'],'notification-one');before=row('ACCESS');calls=h.client.calls
 replay=r.refresh(p['purchaseToken'],'notification-one')
 assert replay['state']=='committed' and replay['idempotencyReplay'] and replay['acknowledgment']=='acknowledged'
 assert h.client.calls>calls and row('ACCESS')==before


def test_current_head_hold_stops_new_admission_keeps_pending_settlement(lifecycle):
 h,w,p,r=lifecycle;a,e,_,row,_,_=w
 check=a.prepare(e,PAYLOAD,'pending');admission=a.admit(e,PAYLOAD,check)
 h.client.head['subscriptionState']='SUBSCRIPTION_STATE_ON_HOLD'
 r.refresh(p['purchaseToken'],'notification-hold');assert row('ACCESS')['state']=='INACTIVE'
 with pytest.raises(AuthorityError):a.prepare(e,PAYLOAD,'new')
 a.settle(WORKER,check,admission['executionToken'],'complete')
 period=row('PERIOD#'+row('ACCESS')['sources']['paid']['periodId']);assert period['usedChecks']==1 and period['reservedChecks']==0


def test_old_owned_alias_cannot_revoke_new_head(lifecycle):
 h,w,p,r=lifecycle;a,e,_,row,_,_=w
 access=row('ACCESS');access['sources']['paid']['headTokenDigest']='b'*64;a.ddb.Table('authority').put_item(Item=access)
 h.client.head['subscriptionState']='SUBSCRIPTION_STATE_EXPIRED';calls=h.client.calls
 assert r.refresh(p['purchaseToken'],'stale-notification')['reason']=='CURRENT_HEAD_NOT_ESTABLISHED'
 assert row('ACCESS')==access and h.client.calls==calls


def test_shorter_verified_end_shortens_token_and_usage_deadlines(lifecycle):
 from datetime import datetime,timezone
 h,w,p,r=lifecycle;a,e,_,row,_,clock=w
 h.client.head['subscriptionState']='SUBSCRIPTION_STATE_ON_HOLD';end=clock[0]+3600
 h.client.head['lineItems'][0]['expiryTime']=datetime.fromtimestamp(end,timezone.utc).isoformat().replace('+00:00','Z')
 r.refresh(p['purchaseToken'],'shorter-end')
 access=row('ACCESS');period=row('PERIOD#'+access['sources']['paid']['periodId']);usage=a._get('authority',period['purchaseUsageKey'])
 assert access['state']=='INACTIVE' and usage['expiresAt']==end+604800
 retained=a._get('play-tokens',token_key(a._partition(ACCOUNT,'k1'),token_hash(p['purchaseToken'])))
 assert retained['expiresAt']==end+604800


def test_shortening_with_pending_work_preserves_settlement(lifecycle):
 from datetime import datetime,timezone
 h,w,p,r=lifecycle;a,e,_,row,_,clock=w
 proof=a.prepare(e,PAYLOAD,'pending');admitted=a.admit(e,PAYLOAD,proof)
 h.client.head['subscriptionState']='SUBSCRIPTION_STATE_ON_HOLD';h.client.head['lineItems'][0]['expiryTime']=datetime.fromtimestamp(clock[0]+3600,timezone.utc).isoformat().replace('+00:00','Z')
 r.refresh(p['purchaseToken'],'shorter-pending')
 assert row('ACCESS')['state']=='INACTIVE'
 a.settle(WORKER,proof,admitted['executionToken'],'complete')
 period=row('PERIOD#'+row('ACCESS')['sources']['paid']['periodId'])
 assert period['usedChecks']==1 and period['reservedChecks']==0


@pytest.mark.parametrize('kind',['account','ownership','authority'])
def test_provider_race_cannot_commit_lifecycle_update(lifecycle,kind):
 h,w,p,r=lifecycle;a,e,put,row,change,_=w;calls=h.client.calls
 audits=[x for x in a.ddb.Table('authority').scan()['Items'] if x['SK'].startswith('AUTHORITY_OP#')]
 token_rows=a.ddb.Table('play-tokens').scan()['Items']
 def race():
  if h.client.calls!=calls+2:return
  if kind=='account':put('deletion','ACCOUNT_DELETION',status='REQUESTED')
  elif kind=='ownership':
   old=h.ownership._get(owner_key(token_hash(p['purchaseToken'])));old['revision']+=1;h.ownership.table.put_item(Item=old)
  else:
   access=row('ACCESS');access['revision']+=1;a.ddb.Table('authority').put_item(Item=access)
 h.client.hook=race
 with pytest.raises(AuthorityError):r.refresh(p['purchaseToken'],'raced-update')
 assert [x for x in a.ddb.Table('authority').scan()['Items'] if x['SK'].startswith('AUTHORITY_OP#')]==audits
 assert a.ddb.Table('play-tokens').scan()['Items']==token_rows


def test_unknown_token_cannot_guess_initial_account_or_claim_ownership(lifecycle):
 h,w,p,r=lifecycle;calls=h.client.calls
 assert r.refresh('unknown-token-123456','unknown-event')['reason']=='OWNERSHIP_NOT_ESTABLISHED'
 assert h.client.calls>calls


def test_worker_identity_required_before_provider(lifecycle):
 h,w,p,r=lifecycle
 with pytest.raises(AuthorityError):Reconciler(h.writer,h.ownership,h.client,TrustedLifecycleWorker('arn:aws:iam::107827791950:role/other'),token_table='play-tokens',token_cipher=Cipher())


def test_grace_then_funded_renewal_preserves_old_pending_period(lifecycle):
 h,w,p,r=lifecycle;a,e,_,row,_,clock=w
 clock[0]=1790899200;e['requestContext']['authorizer']['jwt']['claims']['exp']=str(clock[0]+90)
 h.client.head['subscriptionState']='SUBSCRIPTION_STATE_IN_GRACE_PERIOD';h.client.head['lineItems'][0]['expiryTime']='2026-10-08T00:00:00Z'
 r.refresh(p['purchaseToken'],'grace-first')
 old=row('PERIOD#'+row('ACCESS')['periodId']);proof=a.prepare(e,PAYLOAD,'old-pending');admission=a.admit(e,PAYLOAD,proof)
 h.client.head['subscriptionState']='SUBSCRIPTION_STATE_ACTIVE'
 h.client.head['lineItems'][0].update(expiryTime='2026-11-01T00:00:00Z',latestSuccessfulOrderId='GPA.1234-1234-1234-12345..3')
 h.client.ordered['orderId']='GPA.1234-1234-1234-12345..3'
 h.client.ordered['lineItems'][0]['subscriptionDetails'].update(servicePeriodStartTime='2026-10-01T00:00:00Z',servicePeriodEndTime='2026-11-01T00:00:00Z')
 r.refresh(p['purchaseToken'],'funded-renewal')
 latest=row('PERIOD#'+row('ACCESS')['periodId']);assert latest['SK']!=old['SK'] and latest['usedChecks']==0 and latest['reservedChecks']==0
 a.settle(WORKER,proof,admission['executionToken'],'complete')
 assert row(old['SK'])['usedChecks']==1 and row(old['SK'])['reservedChecks']==0
 assert a._get('authority',old['purchaseUsageKey'])['usedChecks']==1 and a._get('authority',latest['purchaseUsageKey'])['usedChecks']==0


@pytest.mark.parametrize('state',['SUBSCRIPTION_STATE_ON_HOLD','SUBSCRIPTION_STATE_EXPIRED'])
def test_inactive_without_replacement_end_preserves_verified_clock(lifecycle,state):
 h,w,p,r=lifecycle;a,e,_,row,_,_=w
 old=row('PERIOD#'+row('ACCESS')['periodId']);usage=a._get('authority',old['purchaseUsageKey'])
 h.client.head['subscriptionState']=state;h.client.head['lineItems'][0].pop('expiryTime')
 r.refresh(p['purchaseToken'],'no-replacement-end')
 assert row('ACCESS')['state']=='INACTIVE' and row(old['SK'])['accessUntilEpoch']==old['accessUntilEpoch']
 assert a._get('authority',old['purchaseUsageKey'])['expiresAt']==usage['expiresAt']


@pytest.mark.parametrize('field',['grantRevision','startEpoch'])
def test_renewal_rejects_mismatched_previous_funded_identity(lifecycle,field):
 h,w,p,r=lifecycle;a,e,_,row,_,clock=w
 old=row('PERIOD#'+row('ACCESS')['periodId']);old[field]+=1;a.ddb.Table('authority').put_item(Item=old)
 before=a.ddb.Table('authority').scan()['Items'];tokens=a.ddb.Table('play-tokens').scan()['Items']
 clock[0]=1790899200
 h.client.head['lineItems'][0].update(expiryTime='2026-11-01T00:00:00Z',latestSuccessfulOrderId='GPA.1234-1234-1234-12345..3')
 h.client.ordered['orderId']='GPA.1234-1234-1234-12345..3'
 h.client.ordered['lineItems'][0]['subscriptionDetails'].update(servicePeriodStartTime='2026-10-01T00:00:00Z',servicePeriodEndTime='2026-11-01T00:00:00Z')
 with pytest.raises(AuthorityError,match='IMMUTABLE_PERIOD_CONFLICT'):r.refresh(p['purchaseToken'],'mismatched-renewal')
 assert a.ddb.Table('authority').scan()['Items']==before and a.ddb.Table('play-tokens').scan()['Items']==tokens


def test_prepared_initial_purchase_recovers_without_device_session(handoff):
 from shared_play_lifecycle.bindings import Bindings
 h,w,p=handoff;a,e,_,row,_,_=w
 a.ddb.create_table(TableName='play-tokens',BillingMode='PAY_PER_REQUEST',KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}])
 Bindings(h.writer,'play-tokens').prepare(e)
 h.writer.lifecycle_principals=frozenset({ROLE})
 r=Reconciler(h.writer,h.ownership,h.client,TrustedLifecycleWorker(ROLE),token_table='play-tokens',token_cipher=Cipher())
 result=r.refresh(p['purchaseToken'],'initial-background')
 assert result['state']=='committed' and result['acknowledgment']=='acknowledged'
 period=row('PERIOD#'+row('ACCESS')['periodId'])
 assert period['usedChecks']==0 and period['reservedChecks']==0 and period['limit']==200
 assert r.refresh(p['purchaseToken'],'initial-background')['idempotencyReplay']
 assert row(period['SK'])==period


def test_ack_failure_is_recovered_by_duplicate_without_grant_reset(lifecycle):
 from shared_play_verification.proof import PlayVerificationError
 h,w,p,r=lifecycle;a,e,_,row,_,_=w
 h.client.head['acknowledgementState']='ACKNOWLEDGEMENT_STATE_PENDING'
 original=h.client.acknowledge
 h.client.acknowledge=lambda _:(_ for _ in ()).throw(PlayVerificationError('PLAY_PROVIDER_UNAVAILABLE'))
 first=r.refresh(p['purchaseToken'],'ack-repair');before=row('ACCESS');period=row('PERIOD#'+before['periodId'])
 assert first['acknowledgment']=='pending'
 h.client.acknowledge=original
 second=r.refresh(p['purchaseToken'],'ack-repair')
 assert second['acknowledgment']=='acknowledged' and second['idempotencyReplay']
 assert row('ACCESS')==before and row(period['SK'])==period


def test_deletion_during_external_ack_does_not_recreate_account_metadata(lifecycle):
 h,w,p,r=lifecycle;a,e,_,row,_,_=w
 h.client.head['acknowledgementState']='ACKNOWLEDGEMENT_STATE_PENDING'
 observed=[]
 def ack(_):
  observed.extend(a.ddb.Table('play-tokens').scan()['Items'])
  a.ddb.Table(a.s.deletion_table).put_item(Item={'PK':'ACCOUNT#'+ACCOUNT,'SK':'ACCOUNT_DELETION'})
 h.client.acknowledge=ack
 with pytest.raises(AuthorityError):r.refresh(p['purchaseToken'],'delete-during-ack')
 assert observed and a.ddb.Table('play-tokens').scan()['Items']==observed
