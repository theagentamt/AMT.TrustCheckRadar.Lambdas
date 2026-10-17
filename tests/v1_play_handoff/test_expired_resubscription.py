"""Source-only SDK/Moto resubscription races; provider data is synthetic."""
import os
import sys
from copy import deepcopy
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest

if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Isolated authority integration', allow_module_level=True)
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'tests/v1_play_handoff'))
from test_service import handoff, account_binding, ACCOUNT, TOKEN, PRODUCT, new_account
from test_transactions import PAYLOAD, WORKER, world
from shared_check_authority.core import AuthorityError
from shared_check_authority import purchase_usage
from shared_purchase_ownership.service import token_hash, owner_key, locator_item, OwnershipError
from shared_play_verification.proof import PlayVerificationError

NEW = 'synthetic-independent-new-root-token'
SECOND = 'synthetic-independent-second-root-token'
RID = 'e3b9c62a-4e6f-4b38-9d1a-233341aec322'
OLD_END = 1790812800
NEW_END = 1793491200
OLD_ORDER = 'GPA.1234-1234-1234-12345..2'
NEW_ORDER = 'GPA.1234-1234-1234-12345..3'


class Provider:
    def __init__(self, client):
        self.heads = {TOKEN: deepcopy(client.head)}
        self.orders = {OLD_ORDER: deepcopy(client.ordered)}
        self.acks = []

    def subscription(self, token):
        return deepcopy(self.heads[token])

    def order(self, identifier):
        return deepcopy(self.orders[identifier])

    def acknowledge(self, token):
        self.acks.append(token)
        self.heads[token]['acknowledgementState'] = 'ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED'

    def add(self, token=NEW, order=NEW_ORDER, start='2026-10-01T00:00:00Z'):
        head = deepcopy(self.heads[TOKEN])
        head['acknowledgementState'] = 'ACKNOWLEDGEMENT_STATE_PENDING'
        head['lineItems'][0].update(expiryTime='2026-11-01T00:00:00Z', latestSuccessfulOrderId=order)
        self.heads[token] = head
        ordered = deepcopy(self.orders[OLD_ORDER])
        ordered.update(orderId=order, purchaseToken=token)
        ordered['lineItems'][0]['subscriptionDetails'].update(servicePeriodStartTime=start, servicePeriodEndTime='2026-11-01T00:00:00Z')
        self.orders[order] = ordered


@pytest.fixture
def migration(handoff):
    h,w,p = handoff
    a,e,_,row,_,clock = w
    provider = Provider(h.client)
    h.client = provider
    h.handle(e,p)
    old = row('ACCESS')
    clock[0] = OLD_END
    e['requestContext']['authorizer']['jwt']['claims']['exp'] = str(clock[0]+90)
    provider.add()
    request = {**p,'requestId':RID,'purchaseToken':NEW}
    return h,w,request,old


def rows(h):
    a=h.a
    return sorted(a.ddb.Table('authority').scan()['Items'],key=lambda x:(x['PK'],x['SK'])), sorted(h.ownership.table.scan()['Items'],key=lambda x:(x['PK'],x['SK']))


def assert_no_new_root(h, before):
    assert rows(h)==before
    assert h.client.acks==[TOKEN]


def test_boundary_expiry_migrates_atomically_without_changing_old_usage_or_schema(migration):
    h,w,p,old=migration;a,e,_,row,_,_=w
    old_period=row('PERIOD#'+old['periodId']);old_usage=a._get('authority',old_period['purchaseUsageKey'])
    old_owner=h.ownership._get(owner_key(token_hash(TOKEN)))
    observed=[];original=a._transact
    def capture(actions):
        keys=[]
        for action in actions:
            kind,op=next(iter(action.items()));key=op.get('Key',op.get('Item'))
            keys.append((op['TableName'],key['PK'],key['SK']))
            if kind=='Put' and key['SK']=='ACCESS':
                assert 'sources' in op['ExpressionAttributeNames'].values()
                assert old['sources'] in op['ExpressionAttributeValues'].values()
        assert len(keys)==len(set(keys))
        observed.append(actions);original(actions)
    a._transact=capture
    out=h.handle(e,p)
    assert out['status']=='committed' and out['acknowledgment']=='acknowledged'
    current=row('ACCESS');paid=current['sources']['paid'];new_period=row('PERIOD#'+current['periodId'])
    assert paid['headTokenDigest']==token_hash(NEW) and paid['sourceRevision']==old['sources']['paid']['sourceRevision']+1
    assert current['revision']==old['revision']+1
    assert new_period['limit']==200 and new_period['usedChecks']==new_period['reservedChecks']==0
    assert row(old_period['SK'])==old_period and a._get('authority',old_period['purchaseUsageKey'])==old_usage
    assert h.ownership._get(owner_key(token_hash(TOKEN)))==old_owner
    assert set(paid)==set(old['sources']['paid']) and len(observed)==1


@pytest.mark.parametrize('change',['not_expired','overlap','restore','binding','already_owned','other_owner','missing_old_owner','missing_old_locator','missing_head','wrong_product'])
def test_ineligible_migration_fails_without_writes_or_ack(migration,change):
    h,w,p,old=migration;a,e,_,row,update,clock=w
    if change=='not_expired':
        clock[0]=OLD_END-1;e['requestContext']['authorizer']['jwt']['claims']['exp']=str(clock[0]+90)
        h.client.orders[NEW_ORDER]['lineItems'][0]['subscriptionDetails']['servicePeriodStartTime']='2026-09-30T00:00:00Z'
    elif change=='overlap':h.client.orders[NEW_ORDER]['lineItems'][0]['subscriptionDetails']['servicePeriodStartTime']='2026-09-30T23:59:59Z'
    elif change=='restore':p['intent']='RESTORE'
    elif change=='binding':h.client.heads[NEW]['externalAccountIdentifiers']['obfuscatedExternalAccountId']=account_binding('synthetic-other-account')
    elif change in {'already_owned','other_owner'}:
        owner=deepcopy(h.ownership._get(owner_key(token_hash(TOKEN))));digest=token_hash(NEW)
        owner.update(**owner_key(digest),purchaseTokenHash=digest)
        if change=='other_owner':owner['accountId']='synthetic-other-account'
        h.ownership.table.put_item(Item=owner)
        h.ownership.table.put_item(Item=locator_item(owner['accountId'],digest))
    elif change=='missing_old_owner':h.ownership.table.delete_item(Key=owner_key(token_hash(TOKEN)))
    elif change=='missing_old_locator':h.ownership.table.delete_item(Key={k:v for k,v in locator_item(ACCOUNT,token_hash(TOKEN)).items()if k in {'PK','SK'}})
    elif change in {'missing_head','wrong_product'}:
        value=row('ACCESS');paid=value['sources']['paid']
        if change=='missing_head':paid.pop('headTokenDigest')
        else:paid['productId']='another-product'
        a.ddb.Table('authority').put_item(Item=value)
    before=rows(h)
    with pytest.raises((AuthorityError,OwnershipError,PlayVerificationError)):h.handle(e,p)
    assert_no_new_root(h,before)


@pytest.mark.parametrize('race',['access_no_revision','period','usage','owner','locator','inventory','deletion','device'])
def test_atomic_races_never_partially_claim_or_grant(migration,race):
    h,w,p,old=migration;a,e,_,row,_,_=w;original=a._transact
    def mutate(actions):
        if race in {'access_no_revision','period','usage'}:
            item=row('ACCESS') if race=='access_no_revision' else row('PERIOD#'+old['periodId'])
            if race=='access_no_revision':item['sources']['paid']['state']='INACTIVE'
            elif race=='period':item['usedChecks']=1
            else:item=a._get('authority',item['purchaseUsageKey']);item['usedChecks']=1
            a.ddb.Table('authority').put_item(Item=item)
        elif race=='owner':
            item=h.ownership._get(owner_key(token_hash(TOKEN)));item['revision']+=1;h.ownership.table.put_item(Item=item)
        elif race=='locator':h.ownership.table.delete_item(Key={k:v for k,v in locator_item(ACCOUNT,token_hash(TOKEN)).items()if k in {'PK','SK'}})
        elif race=='inventory':
            item=h.ownership.inventory();item['revision']+=1;h.ownership.table.put_item(Item=item)
        elif race=='deletion':a.ddb.Table('deletion').put_item(Item={'PK':'ACCOUNT#'+ACCOUNT,'SK':'ACCOUNT_DELETION','status':'REQUESTED'})
        else:
            key={'PK':'USER#'+ACCOUNT,'SK':'ACTIVE_BINDING'};item=a._get('devices',key);item['stateVersion']+=1;a.ddb.Table('devices').put_item(Item=item)
        original(actions)
    a._transact=mutate
    with pytest.raises((AuthorityError,OwnershipError,PlayVerificationError)):h.handle(e,p)
    assert h.ownership._get(owner_key(token_hash(NEW))) is None and h.client.acks==[TOKEN]
    assert row('ACCESS')['sources']['paid'].get('headTokenDigest')==token_hash(TOKEN)
    assert not any(item['recordType']=='V1_ALLOWANCE_PERIOD' and item['SK']!=('PERIOD#'+old['periodId']) for item in a.ddb.Table('authority').scan()['Items'])


def test_lost_commit_ack_and_exact_replay_never_fund_twice(migration):
    h,w,p,old=migration;a,e,_,row,_,_=w;original=a._transact
    def lost(actions):original(actions);raise AuthorityError('TRANSACTION_UNCERTAIN')
    a._transact=lost
    assert h.handle(e,p)['acknowledgment']=='acknowledged'
    a._transact=original
    before=rows(h);assert h.handle(e,p)['idempotencyReplay']
    assert rows(h)==before and h.client.acks==[TOKEN,NEW]
    with pytest.raises(PlayVerificationError,match='REQUEST_CONFLICT'):h.handle(e,{**p,'intent':'RESTORE'})
    assert rows(h)==before


def test_late_old_period_settlement_survives_new_root_without_carryover(migration):
    h,w,p,old=migration;a,e,_,row,_,clock=w
    clock[0]=OLD_END-10;e['requestContext']['authorizer']['jwt']['claims']['exp']=str(clock[0]+90)
    check=a.prepare(e,PAYLOAD,'old-root-inflight');operation=a.admit(e,PAYLOAD,check)
    old_period=row('PERIOD#'+old['periodId']);assert old_period['reservedChecks']==1
    clock[0]=OLD_END
    assert h.handle(e,p)['status']=='committed'
    new_period=row('PERIOD#'+row('ACCESS')['periodId']);assert new_period['usedChecks']==new_period['reservedChecks']==0
    assert row(old_period['SK'])==old_period
    assert a.settle(WORKER,check,operation['executionToken'],'complete')['chargedChecks']==1
    assert row(old_period['SK'])['usedChecks']==1 and row(old_period['SK'])['reservedChecks']==0
    assert row(new_period['SK'])==new_period


@pytest.mark.parametrize('reserved',[0,1])
def test_existing_new_global_usage_is_preserved_or_refused_never_reset(migration,reserved):
    h,w,p,_=migration;a,e,_,row,_,_=w
    import hashlib
    order_digest=hashlib.sha256(NEW_ORDER.encode()).hexdigest()
    pointer=purchase_usage.key(hashlib.sha256(('google_play\0'+token_hash(NEW)).encode()).hexdigest(),hashlib.sha256(order_digest.encode()).hexdigest())
    usage=purchase_usage.new_period_usage(pointer,OLD_END,NEW_END);usage.update(usedChecks=7,reservedChecks=reserved)
    a.ddb.Table('authority').put_item(Item=usage)
    if reserved:
        before=rows(h)
        with pytest.raises(AuthorityError,match='PURCHASE_USAGE_RECONCILIATION_REQUIRED'):h.handle(e,p)
        assert_no_new_root(h,before)
    else:
        assert h.handle(e,p)['status']=='committed'
        period=row('PERIOD#'+row('ACCESS')['periodId'])
        assert period['usedChecks']==7 and period['reservedChecks']==0 and period['limit']==200
        assert a._get('authority',pointer)==usage


def test_two_concurrent_roots_have_one_atomic_winner(migration):
    h,w,p,old=migration;a,e,_,row,_,_=w
    h.client.add(SECOND,'GPA.1234-1234-1234-12345..4')
    barrier=Barrier(2);lock=Lock();original=a._transact
    def atomic(actions):
        barrier.wait(timeout=10)
        with lock:original(actions)
    a._transact=atomic
    other={**p,'requestId':'d3b9c62a-4e6f-4b38-9d1a-233341aec322','purchaseToken':SECOND}
    def call(request):
        try:return request['purchaseToken'],h.handle(deepcopy(e),request)['status']
        except (AuthorityError,OwnershipError,PlayVerificationError):return request['purchaseToken'],'rejected'
    with ThreadPoolExecutor(max_workers=2)as pool:results=list(pool.map(call,[p,other]))
    assert sorted(status for _,status in results)==['committed','rejected']
    winner=next(token for token,status in results if status=='committed');loser=next(token for token,status in results if status=='rejected')
    assert row('ACCESS')['revision']==old['revision']+1
    assert h.ownership._get(owner_key(token_hash(winner))) is not None and h.ownership._get(owner_key(token_hash(loser))) is None
    assert row('ACCESS')['sources']['paid']['headTokenDigest']==token_hash(winner)
    assert sorted(h.client.acks)==sorted([TOKEN,winner])


@pytest.mark.parametrize('when,reserved,present,accepted',[(0,0,False,False),(604801,0,False,True),(604801,1,False,False),(604801,1,True,False)])
def test_old_usage_retention_is_never_reconstructed_or_extended(migration,when,reserved,present,accepted):
    h,w,p,old=migration;a,e,_,row,_,clock=w
    period=row('PERIOD#'+old['periodId']);period['reservedChecks']=reserved;a.ddb.Table('authority').put_item(Item=period)
    usage=a._get('authority',period['purchaseUsageKey']);usage['reservedChecks']=reserved
    if present:a.ddb.Table('authority').put_item(Item=usage)
    else:a.ddb.Table('authority').delete_item(Key=period['purchaseUsageKey'])
    clock[0]=OLD_END+when;e['requestContext']['authorizer']['jwt']['claims']['exp']=str(clock[0]+90)
    if accepted:
        assert h.handle(e,p)['status']=='committed'
        assert a._get('authority',period['purchaseUsageKey']) is None and row(period['SK'])==period
    else:
        before=rows(h)
        with pytest.raises(AuthorityError,match='PURCHASE_USAGE_RECONCILIATION_REQUIRED'):h.handle(e,p)
        assert_no_new_root(h,before)


def test_shortened_access_does_not_hide_old_funded_overlap(migration):
    h,w,p,old=migration;a,e,_,row,_,_=w
    access=row('ACCESS');access['validUntilEpoch']=OLD_END-100;access['sources']['paid']['validUntilEpoch']=OLD_END-100
    a.ddb.Table('authority').put_item(Item=access)
    period=row('PERIOD#'+old['periodId']);period['accessUntilEpoch']=OLD_END-100;a.ddb.Table('authority').put_item(Item=period)
    usage=purchase_usage.new_period_usage(period['purchaseUsageKey'],period['startEpoch'],period['endEpoch'],access_until_epoch=OLD_END-100)
    a.ddb.Table('authority').put_item(Item=usage)
    h.client.orders[NEW_ORDER]['lineItems'][0]['subscriptionDetails']['servicePeriodStartTime']='2026-09-30T23:59:59Z'
    before=rows(h)
    with pytest.raises(AuthorityError,match='OVERLAPPING_STORE_PERIOD'):h.handle(e,p)
    assert_no_new_root(h,before)


def inputs(h,e):
    from shared_play_verification.proof import verify
    _,access,_=h.writer._read(ACCOUNT)
    previous=h.ownership.observe_claim(ACCOUNT,(token_hash(TOKEN),),product_id=PRODUCT)
    current=h.ownership.observe_claim(ACCOUNT,(token_hash(NEW),),product_id=PRODUCT)
    proof=verify(NEW,fetch_subscription=h.client.subscription,fetch_order=h.client.order,
        now_epoch=h.a.now(),expected_hashes=current.hashes,allow_test=True)
    return proof,{'access':access,'device':h.a._device(e,ACCOUNT)},previous,current


@pytest.mark.parametrize('bad',['restore','dict_proof','wrong_binding','extended_access','wrong_old_observation','wrong_new_observation','wrong_device'])
def test_explicit_proof_api_has_no_boolean_or_untyped_migration_bypass(migration,bad):
    from dataclasses import replace
    h,w,p,_=migration;_,e,*_=w;proof,observation,previous,current=inputs(h,e)
    intent='PURCHASE'
    if bad=='restore':intent='RESTORE'
    elif bad=='dict_proof':proof={}
    elif bad=='wrong_binding':proof=replace(proof,obfuscated_account_id=account_binding('other-account'))
    elif bad=='extended_access':proof=replace(proof,access_until_epoch=proof.period_end_epoch+1)
    elif bad=='wrong_old_observation':previous=current
    elif bad=='wrong_new_observation':current=previous
    else:observation['device']=('wrong-device',2)
    before=rows(h)
    with pytest.raises((AuthorityError,OwnershipError)):
        h.writer.commit_verified_resubscription(e,proof,RID,intent=intent,observation=observation,
            ownership=h.ownership,previous_ownership=previous,new_ownership=current)
    assert_no_new_root(h,before)


@pytest.mark.parametrize('entry',['generic','background','initial_background'])
def test_generic_and_background_writers_cannot_mint_root_migration(migration,entry):
    from shared_check_authority.entitlements import VerifiedMonthlyDecision,TrustedLifecycleWorker
    h,w,p,old=migration;_,e,*_=w;proof,observation,previous,current=inputs(h,e)
    decision=VerifiedMonthlyDecision(account=ACCOUNT,store='google_play',product_id=PRODUCT,
        subscription_id=proof.subscription_identity,source_revision=int(old['sources']['paid']['sourceRevision'])+1,
        verified_at_epoch=proof.verified_at_epoch,active=True,period_id=proof.order_digest,
        period_start_epoch=proof.period_start_epoch,period_end_epoch=proof.period_end_epoch,
        access_until_epoch=proof.access_until_epoch,head_token_digest=proof.token_hashes[0])
    before=rows(h)
    with pytest.raises(AuthorityError):
        if entry=='generic':h.writer.commit_verified_paid(e,decision,RID)
        else:
            principal='arn:aws:iam::123456789012:role/synthetic-lifecycle';h.writer.lifecycle_principals=frozenset({principal})
            fn=h.writer.commit_background_paid if entry=='background' else h.writer.commit_background_initial_paid
            fn(TrustedLifecycleWorker(principal),decision,RID,expected_access=observation['access'],
                ownership_actions=[h.a._check('ownership',owner_key(token_hash(TOKEN)),'attribute_exists(PK)')])
    assert_no_new_root(h,before)


def test_retired_owned_root_cannot_migrate_back_even_with_new_funded_period(migration):
    h,w,p,_=migration;a,e,_,row,_,clock=w;h.handle(e,p)
    h.client.add(TOKEN,'GPA.1234-1234-1234-12345..5',start='2026-11-01T00:00:00Z')
    h.client.heads[TOKEN]['lineItems'][0]['expiryTime']='2026-12-01T00:00:00Z'
    h.client.orders['GPA.1234-1234-1234-12345..5']['lineItems'][0]['subscriptionDetails']['servicePeriodEndTime']='2026-12-01T00:00:00Z'
    clock[0]=NEW_END;e['requestContext']['authorizer']['jwt']['claims']['exp']=str(clock[0]+90)
    before=rows(h)
    with pytest.raises(PlayVerificationError,match='PLAY_TRANSITION_UNSUPPORTED'):
        h.handle(e,{**p,'requestId':'d3b9c62a-4e6f-4b38-9d1a-233341aec322','purchaseToken':TOKEN})
    assert rows(h)==before and h.client.acks==[TOKEN,NEW]


def test_lifecycle_extension_option_does_not_broaden_root_migration(migration):
    h,w,p,_=migration;_,e,*_=w;h.lifecycle_enabled=True
    h.client.heads[NEW]['lineItems'][0]['expiryTime']='2026-11-02T00:00:00Z'
    before=rows(h)
    with pytest.raises(PlayVerificationError,match='PLAY_TRANSITION_UNSUPPORTED'):h.handle(e,p)
    assert_no_new_root(h,before)


def test_inactive_expired_source_can_migrate_without_reviving_old_period(migration):
    h,w,p,old=migration;a,e,_,row,_,_=w
    access=row('ACCESS');access['sources']['paid']['state']='INACTIVE'
    access=h.writer._effective(access['PK'],access,access['sources']);a.ddb.Table('authority').put_item(Item=access)
    old_period=row('PERIOD#'+old['periodId'])
    assert h.handle(e,p)['status']=='committed'
    assert row(old_period['SK'])==old_period and row('ACCESS')['sources']['paid']['headTokenDigest']==token_hash(NEW)


def test_same_request_concurrent_reconciliation_funds_only_once(migration):
    h,w,p,old=migration;a,e,_,row,_,_=w;barrier=Barrier(2);lock=Lock();original=a._transact
    def atomic(actions):
        barrier.wait(timeout=10)
        with lock:original(actions)
    a._transact=atomic
    with ThreadPoolExecutor(max_workers=2)as pool:
        results=list(pool.map(lambda _:h.handle(deepcopy(e),deepcopy(p)),range(2)))
    assert all(r['status']=='committed'for r in results)
    assert row('ACCESS')['revision']==old['revision']+1
    periods=[v for v in a.ddb.Table('authority').scan()['Items']if v.get('recordType')=='V1_ALLOWANCE_PERIOD']
    assert len(periods)==2 and row('PERIOD#'+row('ACCESS')['periodId'])['usedChecks']==0


def test_duplicate_new_request_uses_same_period_without_another_allowance(migration):
    h,w,p,_=migration;a,e,_,row,_,_=w;h.handle(e,p)
    check=a.prepare(e,PAYLOAD,'new-root-used');op=a.admit(e,PAYLOAD,check);a.settle(WORKER,check,op['executionToken'],'complete')
    before=row('PERIOD#'+row('ACCESS')['periodId'])
    assert h.handle(e,{**p,'requestId':'d3b9c62a-4e6f-4b38-9d1a-233341aec322'})['status']=='committed'
    assert row(before['SK'])==before and before['usedChecks']==1


def test_old_linked_head_ownership_remains_intact_when_new_root_is_independent(handoff):
    h,w,p=handoff;a,e,_,row,_,clock=w;provider=Provider(h.client);h.client=provider
    predecessor='synthetic-original-root-predecessor'
    provider.heads[predecessor]=deepcopy(provider.heads[TOKEN])
    provider.heads[TOKEN]['linkedPurchaseToken']=predecessor
    assert h.handle(e,p)['status']=='committed'
    old=row('ACCESS');old_owners=deepcopy(h.ownership.table.scan()['Items'])
    clock[0]=OLD_END;e['requestContext']['authorizer']['jwt']['claims']['exp']=str(clock[0]+90)
    provider.add();provider.heads[NEW].pop('linkedPurchaseToken')
    assert h.handle(e,{**p,'requestId':RID,'purchaseToken':NEW})['status']=='committed'
    for owner in old_owners:
        assert h.ownership._get({k:owner[k]for k in ('PK','SK')})==owner
    assert row('ACCESS')['sources']['paid']['subscriptionDigest']!=old['sources']['paid']['subscriptionDigest']
