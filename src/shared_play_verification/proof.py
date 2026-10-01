"""Bounded current-access and immutable funded-order proof.

Raw store responses/tokens remain call-local. This module performs no grant,
ownership mutation, acknowledgment or diagnostic logging.
"""
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import re

from shared_purchase_ownership.service import token_hash, OwnershipError

PACKAGE = 'com.andmorethings.trustcheckradar'
PRODUCT = 'trustcheck_radar_pro_monthly'
BASE_PLAN = 'pro-monthly'
STATES = {
 'SUBSCRIPTION_STATE_PENDING':'pending', 'SUBSCRIPTION_STATE_ACTIVE':'active',
 'SUBSCRIPTION_STATE_IN_GRACE_PERIOD':'grace', 'SUBSCRIPTION_STATE_ON_HOLD':'hold',
 'SUBSCRIPTION_STATE_PAUSED':'paused', 'SUBSCRIPTION_STATE_CANCELED':'canceled',
 'SUBSCRIPTION_STATE_EXPIRED':'expired', 'SUBSCRIPTION_STATE_PENDING_PURCHASE_CANCELED':'pending_canceled',
}
ACKS={'ACKNOWLEDGEMENT_STATE_PENDING':'pending','ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED':'acknowledged'}
ORDER=re.compile(r'GPA\.[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{5}(?:\.\.[0-9]+)?')

class PlayVerificationError(RuntimeError):
    """Fixed code only; never raw provider errors, identifiers or response content."""

@dataclass(frozen=True)
class PlayProof:
    state: str
    acknowledgment: str
    token_hashes: tuple[str,...] = field(repr=False)
    subscription_identity: str = field(repr=False)
    verified_at_epoch: int
    obfuscated_account_id: str | None = field(repr=False)
    active: bool
    order_digest: str | None = field(default=None, repr=False)
    period_start_epoch: int | None = None
    period_end_epoch: int | None = None
    access_until_epoch: int | None = None
    test_purchase: bool = False


def require(condition, code='PLAY_RESPONSE_INVALID'):
    if not condition: raise PlayVerificationError(code)


def timestamp(value):
    require(isinstance(value,str) and len(value)<=40 and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})',value) is not None)
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'));number=int(parsed.timestamp())
    except (ValueError,OverflowError):raise PlayVerificationError('PLAY_RESPONSE_INVALID') from None
    require(number>0)
    return number


def _token(value):
    try:return token_hash(value)
    except OwnershipError:raise PlayVerificationError('PLAY_RESPONSE_INVALID') from None


def _read(fetch,token):
    try:row=fetch(token)
    except PlayVerificationError:raise
    except Exception:raise PlayVerificationError('PLAY_PROVIDER_UNAVAILABLE') from None
    require(type(row) is dict)
    return row


def _item(row,*,head=True):
    items=row.get('lineItems');require(type(items) is list and len(items)==1 and type(items[0]) is dict,'PLAY_PLAN_UNSUPPORTED')
    item=items[0]
    require(item.get('productId')==PRODUCT and type(item.get('offerDetails')) is dict and item['offerDetails'].get('basePlanId')==BASE_PLAN,'PLAY_PLAN_UNSUPPORTED')
    require(type(item.get('autoRenewingPlan')) is dict and 'prepaidPlan' not in item,'PLAY_PLAN_UNSUPPORTED')
    require(not any(k in item for k in ('deferredItemReplacement','deferredItemRemoval','itemReplacement')) and 'outOfAppPurchaseContext' not in row,'PLAY_TRANSITION_UNSUPPORTED')
    require(not item['offerDetails'].get('offerId'),'PLAY_PLAN_UNSUPPORTED')
    return item


def discover_lineage(purchase_token,fetch):
    """Discovery does not authorize a write; callers must observe then reverify."""
    tokens=[];hashes=[];current=purchase_token
    for _ in range(8):
        digest=_token(current);require(digest not in hashes,'PLAY_LINEAGE_INVALID')
        row=_read(fetch,current);_item(row)
        tokens.append(current);hashes.append(digest)
        linked=row.get('linkedPurchaseToken')
        if linked is None:return tuple(tokens),tuple(hashes)
        _token(linked);current=linked
    raise PlayVerificationError('PLAY_LINEAGE_LIMIT')


def verify(purchase_token,*,fetch_subscription,fetch_order,now_epoch,expected_hashes=None,allow_test=False,allow_access_extension=False):
    """Fetch authoritative state; caller separately pins reviewed P1M catalog config.

    Funded-period bounds come only from the exact successful order. Current expiry
    is independently checked. Unsupported grace/deferral extensions are explicit
    failures until the allowance/access policy can represent both boundaries.
    """
    require(type(now_epoch) is int and now_epoch>0)
    tokens,hashes=discover_lineage(purchase_token,fetch_subscription)
    if expected_hashes is not None:require(hashes==expected_hashes,'PLAY_OBSERVATION_CHANGED')
    head=_read(fetch_subscription,purchase_token);item=_item(head)
    linked=head.get('linkedPurchaseToken');require((linked is None and len(tokens)==1) or (len(tokens)>1 and linked==tokens[1]),'PLAY_OBSERVATION_CHANGED')
    require(head.get('subscriptionState') in STATES and head.get('acknowledgementState') in ACKS)
    state=STATES[head['subscriptionState']];ack=ACKS[head['acknowledgementState']]
    require(not (state in {'pending','pending_canceled'} and len(hashes)>1),'PLAY_TRANSITION_UNSUPPORTED')
    identifiers=head.get('externalAccountIdentifiers',{});require(type(identifiers) is dict)
    obfuscated=identifiers.get('obfuscatedExternalAccountId');require(obfuscated is None or isinstance(obfuscated,str) and 1<=len(obfuscated)<=64)
    test='testPurchase' in head;require(not test or allow_test,'PLAY_TEST_PURCHASE_NOT_ALLOWED')
    root=hashes[-1];active=state in {'active','grace','canceled'}
    base=dict(state=state,acknowledgment=ack,token_hashes=hashes,subscription_identity=root,verified_at_epoch=now_epoch,obfuscated_account_id=obfuscated,active=active,test_purchase=test)
    if not active:
        end=timestamp(item['expiryTime']) if 'expiryTime' in item else None
        return PlayProof(**base,access_until_epoch=end)
    expiry=timestamp(item.get('expiryTime'));require(expiry>now_epoch,'PLAY_ACCESS_EXPIRED')
    order_id=item.get('latestSuccessfulOrderId');require(isinstance(order_id,str) and ORDER.fullmatch(order_id) is not None,'PLAY_FUNDED_PERIOD_UNAVAILABLE')
    try:order=fetch_order(order_id)
    except PlayVerificationError:raise
    except Exception:raise PlayVerificationError('PLAY_PROVIDER_UNAVAILABLE') from None
    require(type(order) is dict and order.get('orderId')==order_id and order.get('purchaseToken')==purchase_token,'PLAY_ORDER_MISMATCH')
    require(order.get('state')=='PROCESSED','PLAY_ORDER_NOT_FUNDED')
    items=order.get('lineItems');require(type(items) is list and len(items)==1 and type(items[0]) is dict,'PLAY_ORDER_MISMATCH')
    ordered=items[0];details=ordered.get('subscriptionDetails')
    require(ordered.get('productId')==PRODUCT and type(details) is dict and details.get('basePlanId')==BASE_PLAN and not details.get('offerId'),'PLAY_ORDER_MISMATCH')
    phase=details.get('offerPhaseDetails');require(type(phase) is dict and set(phase)=={'baseDetails'} and type(phase['baseDetails']) is dict and not phase['baseDetails'],'PLAY_PLAN_UNSUPPORTED')
    start,end=timestamp(details.get('servicePeriodStartTime')),timestamp(details.get('servicePeriodEndTime'))
    require(start<end and start<=now_epoch and (allow_access_extension or now_epoch<end),'PLAY_FUNDED_PERIOD_UNAVAILABLE')
    require(type(allow_access_extension) is bool and (expiry>=end if allow_access_extension else expiry==end),'PLAY_PERIOD_EXTENSION_UNSUPPORTED')
    after=_read(fetch_subscription,purchase_token)
    # Compare only protocol-relevant fields; order/customer/address/pricing never persist.
    keys=('subscriptionState','acknowledgementState','lineItems','linkedPurchaseToken','externalAccountIdentifiers','testPurchase','outOfAppPurchaseContext')
    require(all(head.get(k)==after.get(k) for k in keys),'PLAY_OBSERVATION_CHANGED')
    return PlayProof(**base,order_digest=sha256(order_id.encode()).hexdigest(),period_start_epoch=start,period_end_epoch=end,access_until_epoch=expiry)
