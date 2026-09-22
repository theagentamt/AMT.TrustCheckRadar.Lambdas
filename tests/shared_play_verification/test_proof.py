from copy import deepcopy
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from shared_play_verification.proof import verify,discover_lineage,PlayVerificationError,PRODUCT,BASE_PLAN

TOKEN='synthetic-token-123456';ORDER='GPA.1234-1234-1234-12345..2';NOW=1789084800

def fixture():
 head={'subscriptionState':'SUBSCRIPTION_STATE_ACTIVE','acknowledgementState':'ACKNOWLEDGEMENT_STATE_PENDING','startTime':'2025-01-01T00:00:00Z','lineItems':[{'productId':PRODUCT,'expiryTime':'2026-10-01T00:00:00Z','latestSuccessfulOrderId':ORDER,'autoRenewingPlan':{'autoRenewEnabled':True},'offerDetails':{'basePlanId':BASE_PLAN}}],'externalAccountIdentifiers':{'obfuscatedExternalAccountId':'a'*64}}
 order={'orderId':ORDER,'purchaseToken':TOKEN,'state':'PROCESSED','buyerAddress':{'buyerPostcode':'DO-NOT-RETAIN'},'lineItems':[{'productId':PRODUCT,'subscriptionDetails':{'basePlanId':BASE_PLAN,'offerPhaseDetails':{'baseDetails':{}},'servicePeriodStartTime':'2026-09-01T00:00:00Z','servicePeriodEndTime':'2026-10-01T00:00:00Z'}}]}
 return head,order

def run(head,order):return verify(TOKEN,fetch_subscription=lambda _:deepcopy(head),fetch_order=lambda _:deepcopy(order),now_epoch=NOW)

def test_funded_order_not_lifetime_start():
 h,o=fixture();p=run(h,o)
 assert p.period_start_epoch==1788220800 and p.period_end_epoch==1790812800 and p.active
 assert 'DO-NOT-RETAIN' not in repr(p) and TOKEN not in repr(p) and ORDER not in repr(p)

@pytest.mark.parametrize('state',['PENDING','ON_HOLD','PAUSED','EXPIRED','PENDING_PURCHASE_CANCELED'])
def test_inactive_no_order_or_ack(state):
 h,o=fixture();h['subscriptionState']='SUBSCRIPTION_STATE_'+state
 p=verify(TOKEN,fetch_subscription=lambda _:h,fetch_order=lambda _:pytest.fail('no order for inactive'),now_epoch=NOW)
 assert not p.active and p.order_digest is None

@pytest.mark.parametrize('mutation',[
 lambda h,o:h.update(subscriptionState='UNKNOWN'),
 lambda h,o:h.update(acknowledgementState='UNKNOWN'),
 lambda h,o:h['lineItems'][0]['offerDetails'].update(basePlanId='annual'),
 lambda h,o:h['lineItems'][0]['offerDetails'].update(offerId='free-trial'),
 lambda h,o:h['lineItems'][0].update(expiryTime='2026-10-02T00:00:00Z'),
 lambda h,o:h.update(outOfAppPurchaseContext={'expiredPurchaseToken':'other-token-12345'}),
 lambda h,o:o.update(purchaseToken='wrong-token-12345'),
 lambda h,o:o.update(orderId='GPA.9999-9999-9999-99999'),
 lambda h,o:o.update(state='REFUNDED'),
 lambda h,o:o['lineItems'][0]['subscriptionDetails'].update(offerPhaseDetails={'freeTrialDetails':{}}),
 lambda h,o:o['lineItems'][0]['subscriptionDetails'].update(servicePeriodStartTime='2026-09-99T00:00:00Z'),
 lambda h,o:h['lineItems'].append(deepcopy(h['lineItems'][0])),
 lambda h,o:h.update(testPurchase={}),
])
def test_unsupported_or_mismatched_proof_never_grants(mutation):
 h,o=fixture();mutation(h,o)
 with pytest.raises(PlayVerificationError):run(h,o)

def test_provider_change_while_fetching_order_is_rejected():
 h,o=fixture();count=0
 def fetch(_):
  nonlocal count
  count+=1;r=deepcopy(h)
  if count==3:r['subscriptionState']='SUBSCRIPTION_STATE_EXPIRED'
  return r
 with pytest.raises(PlayVerificationError,match='PLAY_OBSERVATION_CHANGED'):verify(TOKEN,fetch_subscription=fetch,fetch_order=lambda _:o,now_epoch=NOW)

def test_lineage_cycle_and_changed_observation_rejected():
 h,o=fixture();h['linkedPurchaseToken']=TOKEN
 with pytest.raises(PlayVerificationError,match='PLAY_LINEAGE_INVALID'):discover_lineage(TOKEN,lambda _:h)
 h.pop('linkedPurchaseToken')
 with pytest.raises(PlayVerificationError,match='PLAY_OBSERVATION_CHANGED'):verify(TOKEN,fetch_subscription=lambda _:h,fetch_order=lambda _:o,now_epoch=NOW,expected_hashes=('0'*64,))
