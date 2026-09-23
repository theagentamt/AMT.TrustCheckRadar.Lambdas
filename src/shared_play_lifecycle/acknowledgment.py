"""Server acknowledgement after durable current funded authority; no grant here."""
from shared_check_authority.core import AuthorityError
from shared_check_authority.purchase_usage import exact_condition,global_for_period
from shared_play_verification.proof import PRODUCT,PlayVerificationError
from .tokens import key,validate


def acknowledge_current(reconciler,account,proof,purchase_token):
    a,w=reconciler.a,reconciler.w
    pk,access,sources=w._read(account);paid=sources.get('paid',{})
    if not proof.active:return 'not_applicable'
    digest=proof.token_hashes[0]
    subscription=a._mac(a.s.active_key_id,'store-subscription','google_play\0'+proof.subscription_identity)
    period_id='paid-'+a._mac(a.s.active_key_id,'store-period',subscription+'\0'+proof.order_digest)[:40]
    if (not access or paid.get('state')!='ACTIVE' or paid.get('headTokenDigest')!=digest
        or paid.get('subscriptionDigest')!=subscription or paid.get('periodId')!=period_id
        or paid.get('validFromEpoch')!=proof.period_start_epoch or paid.get('validUntilEpoch')!=proof.access_until_epoch):
        return 'pending'  # A historical event cannot acknowledge an ungranted renewal.
    observed=reconciler.ownership.observe_claim(account,proof.token_hashes,product_id=PRODUCT)
    if any(row is None for row in observed.owners+observed.locators):raise AuthorityError('PLAY_OWNERSHIP_UNCONFIRMED')
    period=a._get(a.s.authority_table,{'PK':pk,'SK':'PERIOD#'+period_id})
    global_for_period(a.ddb,a.s.authority_table,period,now=a.now())
    retained=a._get(reconciler.token_table,key(pk,digest));validate(retained,key(pk,digest),a.now())
    a._assert_account(account)
    if proof.acknowledgment=='pending':
        try:reconciler.client.acknowledge(purchase_token)
        except PlayVerificationError:return 'pending'
    elif proof.acknowledgment!='acknowledged':return 'not_applicable'
    # AWS deletion can race Google's external acknowledgment. Never recreate or
    # update account data from that acknowledgment after the durable fence.
    a._assert_account(account)
    due=min(a.now()+3600,int(retained['expiresAt']))
    updated=dict(retained,acknowledgment='acknowledged',attemptCount=0,lastOutcome='verified',
                 nextAttemptAtEpoch=due,revision=int(retained['revision'])+1,
                 GSI1SK=f"{due:012d}#{pk}#{retained['SK']}")
    actions=a._account_conditions(account)
    actions += [{'ConditionCheck':{'TableName':reconciler.ownership.table_name,'Key':{k:row[k] for k in ('PK','SK')},**exact_condition(row)}} for row in (observed.inventory,)+observed.owners+observed.locators]
    actions += [{'ConditionCheck':{'TableName':a.s.authority_table,'Key':{'PK':pk,'SK':'ACCESS'},**exact_condition(access)}},
                {'Put':{'TableName':reconciler.token_table,'Item':updated,**exact_condition(retained)}}]
    a._transact(actions)
    return 'acknowledged'
