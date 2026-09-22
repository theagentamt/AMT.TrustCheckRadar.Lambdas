"""Atomic normal-period Play handoff; activation and lifecycle qualification separate."""
from hashlib import sha256
import json
from uuid import UUID
from shared_check_authority.core import AuthorityError,OWNER_POLICY,integral
from shared_check_authority.entitlements import VerifiedMonthlyDecision
from shared_play_verification.proof import PRODUCT, PlayVerificationError, discover_lineage, verify

CONTRACT='v1-play-handoff-1.0.0-candidate.1'


def request_payload(value):
    if (type(value) is not dict or set(value)!={'schemaVersion','requestId','intent','purchaseToken'} or type(value['schemaVersion']) is not int or value['schemaVersion']!=1 or value['intent'] not in ('PURCHASE','RESTORE')):raise PlayVerificationError('INPUT_REJECTED')
    try:
        if str(UUID(value['requestId']))!=value['requestId']:raise ValueError()
        from shared_play_verification.proof import _token
        _token(value['purchaseToken'])
    except (ValueError,TypeError,AttributeError,PlayVerificationError):raise PlayVerificationError('INPUT_REJECTED') from None
    return value


def account_binding(account):return sha256(b'TrustCheckRadar/account-namespace/v1\x00'+account.encode('utf-8')).hexdigest()


class Handoff:
    def __init__(self,writer,ownership,client,*,allow_test=False,require_test=False):
        self.writer=writer;self.a=writer.a;self.ownership=ownership;self.client=client;self.allow_test=allow_test;self.require_test=require_test

    def handle(self,event,payload):
        payload=request_payload(payload);account=self.a._account(event)
        self.a._assert_account(account);device=self.a._device(event,account)
        pk,access,sources=self.writer._read(account)
        request_digest=self.a._mac(self.a.s.active_key_id,'play-handoff-request',json.dumps(payload,sort_keys=True,separators=(',',':')))
        receipt_key={'PK':pk,'SK':'AUTHORITY_OP#'+self.a._mac(self.a.s.active_key_id,'authority-operation',payload['requestId'])}
        prior=self.a._get(self.a.s.authority_table,receipt_key)
        if prior is not None:self._receipt(prior,request_digest)
        # Discovery cannot grant. Capture ownership and ACCESS before a second,
        # fresh full-lineage verification; conflicts require a new provider read.
        _,hashes=discover_lineage(payload['purchaseToken'],self.client.subscription)
        observed=self.ownership.observe_claim(account,hashes,product_id=PRODUCT)
        proof=verify(payload['purchaseToken'],fetch_subscription=self.client.subscription,fetch_order=self.client.order,now_epoch=self.a.now(),expected_hashes=hashes,allow_test=self.allow_test)
        if self.require_test and not proof.test_purchase:raise PlayVerificationError('PLAY_TEST_PURCHASE_REQUIRED')
        cross_account_restore = proof.obfuscated_account_id != account_binding(account)
        known_owned_lineage = any(owner is not None for owner in observed.owners)
        if cross_account_restore and not known_owned_lineage and payload['intent'] != 'RESTORE':
            raise PlayVerificationError('PURCHASE_ACCOUNT_MISMATCH')
        # observe_claim already rejects any alias owned by another account.
        # After explicit restore, future same-owner callbacks can reuse that
        # verified ownership even though Google's original binding stays old.
        if not proof.active:
            # A submitted old or pending token is not proof to revoke a newer
            # linked subscription. Background lifecycle owns authoritative revoke.
            return self._result(payload,'pending' if proof.state=='pending' else 'not_granted','not_applicable',False)
        if prior is None:
            actions=self.ownership.prepare_claim_actions(observed,proof.token_hashes,product_id=PRODUCT)
            previous=sources.get('paid')
            revision=1 if previous is None else int(previous['sourceRevision'])+1
            decision=VerifiedMonthlyDecision(account=account,store='google_play',product_id=PRODUCT,subscription_id=proof.subscription_identity,source_revision=revision,verified_at_epoch=proof.verified_at_epoch,active=True,period_id=proof.order_digest,period_start_epoch=proof.period_start_epoch,period_end_epoch=proof.period_end_epoch,require_existing_usage=cross_account_restore and (not known_owned_lineage or sources.get('paid') is None))
            self.writer.commit_verified_paid(event,decision,payload['requestId'],observation={'access':access,'device':device},transaction_extras=actions,request_digest=request_digest)
        # A lost transaction acknowledgment is reconciled only by the exact owned
        # request receipt. Never acknowledge a purchase from an uncertain grant.
        receipt=self.a._get(self.a.s.authority_table,receipt_key)
        if receipt is None:raise AuthorityError('TRANSACTION_UNCERTAIN')
        self._receipt(receipt,request_digest)
        self.a._assert_account(account)
        if self.a._device(event,account)!=device:raise AuthorityError('STORE_OBSERVATION_CHANGED')
        owned=self.ownership.observe_claim(account,proof.token_hashes,product_id=PRODUCT)
        if any(owner is None for owner in owned.owners):raise PlayVerificationError('PLAY_OWNERSHIP_UNCONFIRMED')
        # An exact historical handoff can finish reconciliation after renewal.
        # Fresh Google acknowledgment evidence is sufficient for this historical
        # result; it neither grants the new period nor invokes acknowledgment.
        # The client must refresh authority and use a new operation for renewal.
        if prior is not None and proof.acknowledgment == 'acknowledged':
            return self._result(payload,'committed','acknowledged',True)
        _,current,current_sources=self.writer._read(account)
        paid=current_sources.get('paid',{})
        subscription=self.a._mac(self.a.s.active_key_id,'store-subscription','google_play\0'+proof.subscription_identity)
        period_id='paid-'+self.a._mac(self.a.s.active_key_id,'store-period',subscription+'\0'+proof.order_digest)[:40]
        # A complimentary overlay or another same-period verification may advance
        # ACCESS revision. Acknowledge only if the verified paid source remains
        # durable; a revoked/different period never qualifies via historical audit.
        if not current or paid.get('state')!='ACTIVE' or paid.get('subscriptionDigest')!=subscription or paid.get('periodId')!=period_id or paid.get('validFromEpoch')!=proof.period_start_epoch or paid.get('validUntilEpoch')!=proof.period_end_epoch:
            return self._result(payload,'committed','pending',prior is not None)
        from shared_check_authority.purchase_usage import global_for_period
        period=self.a._get(self.a.s.authority_table,{'PK':pk,'SK':'PERIOD#'+period_id})
        global_for_period(self.a.ddb,self.a.s.authority_table,period,now=self.a.now())
        ack=proof.acknowledgment
        if ack=='pending':
            try:self.client.acknowledge(payload['purchaseToken']);ack='acknowledged'
            except PlayVerificationError:ack='pending'
        return self._result(payload,'committed',ack,prior is not None)

    @staticmethod
    def _receipt(receipt,request_digest):
        if (receipt.get('recordType')!='V1_AUTHORITY_AUDIT' or receipt.get('policyVersion')!=OWNER_POLICY or receipt.get('action')!='SYNC_PAID' or receipt.get('requestDigest')!=request_digest or integral(receipt.get('revision')) is None or receipt['revision']<1 or integral(receipt.get('recordedAtEpoch')) is None or receipt['recordedAtEpoch']<0):
            raise PlayVerificationError('REQUEST_CONFLICT')

    @staticmethod
    def _result(payload,status,ack,replay):
        return {'schemaVersion':1,'contractVersion':CONTRACT,'requestId':payload['requestId'],'status':status,'acknowledgment':ack,'snapshotRequired':True,'idempotencyReplay':replay}
