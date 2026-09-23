"""Unwired reconciliation for an already-owned, exact-current Play head.

The runtime must supply an authenticated delivery and a trusted worker identity.
Unknown initial purchases and ownership transfers require the foreground handoff.
No notification timestamp or notification type authorizes a grant or revocation.
"""
import re
from shared_check_authority.core import AuthorityError,integral
from shared_check_authority.entitlements import VerifiedMonthlyDecision,TrustedLifecycleWorker
from shared_check_authority.purchase_usage import exact_condition
from shared_purchase_ownership.service import owner_key, token_hash
from shared_play_verification.proof import PRODUCT, discover_lineage, verify
from .tokens import key as token_key, prepare_put, validate as validate_token


class Reconciler:
    def __init__(self,writer,ownership,client,worker,*,token_table,token_cipher,require_test=True):
        if type(worker) is not TrustedLifecycleWorker or worker.principal_arn not in writer.lifecycle_principals or not isinstance(token_table,str) or not token_table or token_table==writer.a.s.authority_table:
            raise AuthorityError('LIFECYCLE_CONFIGURATION_REQUIRED')
        self.w,self.a,self.ownership,self.client,self.worker=writer,writer.a,ownership,client,worker
        self.token_table,self.cipher,self.require_test=token_table,token_cipher,require_test

    def refresh(self,purchase_token,operation_id):
        if not isinstance(operation_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',operation_id):raise AuthorityError('LIFECYCLE_INPUT_INVALID')
        digest=token_hash(purchase_token)
        owner=self.ownership._get(owner_key(digest))
        if not owner:return {'state':'unresolved','reason':'OWNERSHIP_NOT_ESTABLISHED'}
        account=owner.get('accountId')
        head_observation=self.ownership.observe_claim(account,(digest,),product_id=PRODUCT)
        if any(x is None for x in head_observation.owners+head_observation.locators):
            return {'state':'unresolved','reason':'OWNERSHIP_NOT_ESTABLISHED'}
        # _read enforces active profile and durable deletion fence. A background
        # update deliberately has no requirement for an online device or user JWT.
        pk,access,sources=self.w._read(account)
        paid=sources.get('paid')
        if not paid or paid.get('headTokenDigest')!=digest:
            return {'state':'unresolved','reason':'CURRENT_HEAD_NOT_ESTABLISHED'}
        request_digest=self.a._mac(self.a.s.active_key_id,'play-lifecycle-request',digest+'\0'+operation_id)
        prior=self.a._get(self.a.s.authority_table,{'PK':pk,'SK':'AUTHORITY_OP#'+self.a._mac(self.a.s.active_key_id,'authority-operation',operation_id)})
        if prior is not None:
            from v1_play_handoff.service import Handoff
            Handoff._receipt(prior,request_digest)
            # Prior commitment is delivery evidence, not a fresh access promise.
            # The scheduled token reconciler separately owns interrupted ack.
            return {'state':'committed','idempotencyReplay':True}
        _,hashes=discover_lineage(purchase_token,self.client.subscription)
        observed=self.ownership.observe_claim(account,hashes,product_id=PRODUCT)
        if any(x is None for x in observed.owners+observed.locators):
            return {'state':'unresolved','reason':'OWNERSHIP_NOT_ESTABLISHED'}
        proof=verify(purchase_token,fetch_subscription=self.client.subscription,fetch_order=self.client.order,
            now_epoch=self.a.now(),expected_hashes=hashes,allow_test=self.require_test,allow_access_extension=True)
        if self.require_test and not proof.test_purchase:raise AuthorityError('LIFECYCLE_TEST_PURCHASE_REQUIRED')
        if proof.state in ('pending','pending_canceled'):
            return {'state':'unresolved','reason':'PENDING_PURCHASE_NOT_AUTHORITY'}
        # The root of an owned alias is insufficient: exact current head and root
        # must both match the pre-provider authority observation.
        subscription=self.a._mac(self.a.s.active_key_id,'store-subscription','google_play\0'+proof.subscription_identity)
        if proof.token_hashes[0]!=digest or paid['subscriptionDigest']!=subscription:
            raise AuthorityError('LIFECYCLE_OBSERVATION_REQUIRED')
        conditions=[]
        for row in (observed.inventory,)+observed.owners+observed.locators:
            conditions.append({'ConditionCheck':{'TableName':self.ownership.table_name,
                'Key':{k:row[k] for k in ('PK','SK')},**exact_condition(row)}})
        end=proof.access_until_epoch if proof.access_until_epoch is not None else integral(paid.get('validUntilEpoch'))
        if end is None:raise AuthorityError('LIFECYCLE_OBSERVATION_REQUIRED')
        retained_key=token_key(pk,digest)
        retained=self.a._get(self.token_table,retained_key)
        token_actions=[]
        if self.a.now()<end+604800:
            token_actions.append(prepare_put(self.token_table,pk,purchase_token,access_until_epoch=end,
                next_attempt_epoch=min(self.a.now()+3600,end+604800),now=self.a.now(),cipher=self.cipher,observed=retained))
        elif retained is not None:
            validate_token(retained,retained_key,self.a.now(),allow_expired=True)
            token_actions.append({'Delete':{'TableName':self.token_table,'Key':retained_key,**exact_condition(retained)}})
        decision=VerifiedMonthlyDecision(account,'google_play',PRODUCT,proof.subscription_identity,
            int(paid['sourceRevision'])+1,proof.verified_at_epoch,proof.active,
            proof.order_digest,proof.period_start_epoch,proof.period_end_epoch,
            access_until_epoch=end,head_token_digest=digest)
        result=self.w.commit_background_paid(self.worker,decision,operation_id,expected_access=access,
            ownership_actions=conditions,token_actions=token_actions,request_digest=request_digest)
        # Delivery completion is not purchase acknowledgment. An actual worker
        # must qualify its separate idempotent server acknowledgment recovery.
        return {'state':'committed','revision':result['revision'],'acknowledgment':proof.acknowledgment}
