"""Fresh-store reconciliation of an owned head or explicitly prepared first purchase.

The runtime must supply an authenticated delivery and a trusted worker identity.
Unknown initial purchases require an exact prepared mapping; ownership transfers still require the foreground handoff.
No notification timestamp or notification type authorizes a grant or revocation.
"""
import re
from shared_check_authority.core import AuthorityError,integral
from shared_check_authority.entitlements import VerifiedMonthlyDecision,TrustedLifecycleWorker
from shared_check_authority.purchase_usage import exact_condition
from shared_purchase_ownership.service import owner_key, token_hash
from shared_play_verification.proof import PRODUCT, discover_lineage, verify
from .tokens import key as token_key, prepare_put, validate as validate_token
from .bindings import Bindings,conditions as binding_conditions


class Reconciler:
    def __init__(self,writer,ownership,client,worker,*,token_table,token_cipher,require_test=True,allowed_subjects=None):
        if type(worker) is not TrustedLifecycleWorker or worker.principal_arn not in writer.lifecycle_principals or not isinstance(token_table,str) or not token_table or token_table==writer.a.s.authority_table:
            raise AuthorityError('LIFECYCLE_CONFIGURATION_REQUIRED')
        self.w,self.a,self.ownership,self.client,self.worker=writer,writer.a,ownership,client,worker
        self.token_table,self.cipher,self.require_test=token_table,token_cipher,require_test
        self.allowed_subjects=allowed_subjects
        self.bindings=Bindings(writer,token_table)

    def refresh(self,purchase_token,operation_id):
        if not isinstance(operation_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',operation_id):raise AuthorityError('LIFECYCLE_INPUT_INVALID')
        digest=token_hash(purchase_token)
        owner=self.ownership._get(owner_key(digest));initial=owner is None;mapping=None
        discovered=None
        if initial:
            head={}
            def discover_head(token):
                row=self.client.subscription(token)
                if token==purchase_token:head.update(row)
                return row
            _,discovered=discover_lineage(purchase_token,discover_head)
            binding=head.get('externalAccountIdentifiers',{}).get('obfuscatedExternalAccountId')
            if not isinstance(binding,str):return {'state':'unresolved','reason':'OWNERSHIP_NOT_ESTABLISHED'}
            mapping=self.bindings.resolve(binding)
            if mapping is None:return {'state':'unresolved','reason':'OWNERSHIP_NOT_ESTABLISHED'}
            account=mapping[0]
        else:account=owner.get('accountId')
        if self.allowed_subjects is not None and account not in self.allowed_subjects:
            raise AuthorityError('ENGINEERING_ACCESS_UNAVAILABLE')
        head_observation=self.ownership.observe_claim(account,(digest,),product_id=PRODUCT)
        if not initial and any(x is None for x in head_observation.owners+head_observation.locators):
            return {'state':'unresolved','reason':'OWNERSHIP_NOT_ESTABLISHED'}
        pk,access,sources=self.w._read(account)
        paid=sources.get('paid')
        if initial and paid is not None:
            return {'state':'unresolved','reason':'CURRENT_HEAD_NOT_ESTABLISHED'}
        if not initial and (not paid or paid.get('headTokenDigest')!=digest):
            return {'state':'unresolved','reason':'CURRENT_HEAD_NOT_ESTABLISHED'}
        request_digest=self.a._mac(self.a.s.active_key_id,'play-lifecycle-request',digest+'\0'+operation_id)
        prior=self.a._get(self.a.s.authority_table,{'PK':pk,'SK':'AUTHORITY_OP#'+self.a._mac(self.a.s.active_key_id,'authority-operation',operation_id)})
        if prior is not None:
            from v1_play_handoff.service import Handoff
            Handoff._receipt(prior,request_digest)
        if discovered is None:_,hashes=discover_lineage(purchase_token,self.client.subscription)
        else:hashes=discovered
        observed=self.ownership.observe_claim(account,hashes,product_id=PRODUCT)
        if not initial and any(x is None for x in observed.owners+observed.locators):
            return {'state':'unresolved','reason':'OWNERSHIP_NOT_ESTABLISHED'}
        proof=verify(purchase_token,fetch_subscription=self.client.subscription,fetch_order=self.client.order,
            now_epoch=self.a.now(),expected_hashes=hashes,allow_test=self.require_test,allow_access_extension=True)
        if self.require_test and not proof.test_purchase:raise AuthorityError('LIFECYCLE_TEST_PURCHASE_REQUIRED')
        if proof.state in ('pending','pending_canceled'):
            return {'state':'unresolved','reason':'PENDING_PURCHASE_NOT_AUTHORITY'}
        # The root of an owned alias is insufficient: exact current head and root
        # must both match the pre-provider authority observation.
        subscription=self.a._mac(self.a.s.active_key_id,'store-subscription','google_play\0'+proof.subscription_identity)
        if proof.token_hashes[0]!=digest or paid is not None and paid['subscriptionDigest']!=subscription:
            raise AuthorityError('LIFECYCLE_OBSERVATION_REQUIRED')
        if initial:
            if proof.obfuscated_account_id != mapping[1]['bindingHash'] or not proof.active:
                return {'state':'unresolved','reason':'INITIAL_PROOF_NOT_AUTHORITY'}
            conditions=self.ownership.prepare_claim_actions(observed,proof.token_hashes,product_id=PRODUCT)
            conditions+=binding_conditions(self.token_table,mapping[1],mapping[2])
        else:
            conditions=[]
            for row in (observed.inventory,)+observed.owners+observed.locators:
                conditions.append({'ConditionCheck':{'TableName':self.ownership.table_name,
                    'Key':{k:row[k] for k in ('PK','SK')},**exact_condition(row)}})
        end=proof.access_until_epoch if proof.access_until_epoch is not None else integral((paid or {}).get('validUntilEpoch'))
        if end is None:raise AuthorityError('LIFECYCLE_OBSERVATION_REQUIRED')
        retained_key=token_key(pk,digest)
        retained=self.a._get(self.token_table,retained_key)
        token_actions=[]
        if self.a.now()<end+604800:
            token_actions.append(prepare_put(self.token_table,pk,purchase_token,access_until_epoch=end,
                next_attempt_epoch=min(self.a.now()+(60 if proof.acknowledgment=='pending' else 3600),end+604800),now=self.a.now(),cipher=self.cipher,observed=retained,acknowledgment=proof.acknowledgment))
        elif retained is not None:
            validate_token(retained,retained_key,self.a.now(),allow_expired=True)
            token_actions.append({'Delete':{'TableName':self.token_table,'Key':retained_key,**exact_condition(retained)}})
        decision=VerifiedMonthlyDecision(account,'google_play',PRODUCT,proof.subscription_identity,
            1 if paid is None else int(paid['sourceRevision'])+1,proof.verified_at_epoch,proof.active,
            proof.order_digest,proof.period_start_epoch,proof.period_end_epoch,
            access_until_epoch=end,head_token_digest=digest)
        if prior is None:
            commit=self.w.commit_background_initial_paid if initial else self.w.commit_background_paid
            result=commit(self.worker,decision,operation_id,expected_access=access,
                ownership_actions=conditions,token_actions=token_actions,request_digest=request_digest)
        else:result={'revision':int(prior['revision'])}
        from .acknowledgment import acknowledge_current
        ack=acknowledge_current(self,account,proof,purchase_token)
        return {'state':'committed','revision':result['revision'],'acknowledgment':ack,'idempotencyReplay':prior is not None}
