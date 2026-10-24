"""Aggregate provider attempts/circuit state; no account or submitted content."""
from shared_check_authority.core import integral
from shared_message_contract.validation import MessageError
from shared_message_contract.provider_cost import CostPolicy

KEY={'PK':'V1#CONTROL','SK':'MESSAGE_PROVIDER_BUDGET'}


class ProviderBudget:
    def __init__(self,authority,*,window_seconds,max_attempts,max_failures,circuit_open,
                 cost_policy=None,max_reserved_nano=None):
        if (any(type(x) is not int or x<=0 for x in (window_seconds,max_attempts,max_failures)) or
                window_seconds>86400 or max_attempts>100000 or max_failures>max_attempts or type(circuit_open) is not bool):
            raise MessageError('PROVIDER_BUDGET_UNAVAILABLE')
        self.a=authority;self.window=window_seconds;self.cap=max_attempts;self.fail_cap=max_failures;self.open=circuit_open
        if cost_policy is not None:
            if (not isinstance(cost_policy,CostPolicy) or type(max_reserved_nano) is not int
                    or not cost_policy.reservation_nano<=max_reserved_nano<=10**18):
                raise MessageError('PROVIDER_BUDGET_UNAVAILABLE')
        elif max_reserved_nano is not None:
            raise MessageError('PROVIDER_BUDGET_UNAVAILABLE')
        self.cost_policy=cost_policy;self.money_cap=max_reserved_nano

    def reserve(self):
        if self.open:return None
        now=self.a.now()
        window=now//self.window*self.window
        prior=self.a._get(self.a.s.authority_table,KEY)
        active=False
        if prior:
            if (prior.get('recordType')!='V1_MESSAGE_PROVIDER_BUDGET' or any(integral(prior.get(k)) is None or prior[k]<0 for k in ('revision','windowStart','attempts','failures'))):
                raise MessageError('PROVIDER_BUDGET_UNAVAILABLE')
            if now<prior['windowStart']:raise MessageError('PROVIDER_BUDGET_UNAVAILABLE')
            old_window=integral(prior.get('windowSeconds'))
            if 'windowSeconds' in prior and old_window is None:
                raise MessageError('PROVIDER_BUDGET_UNAVAILABLE')
            if old_window is not None and not 1<=old_window<=86400:
                raise MessageError('PROVIDER_BUDGET_UNAVAILABLE')
            # Old records do not prove their window length. Do not reset a live
            # reservation by changing configuration; wait the maximum old window.
            prior_reserved=integral(prior.get('reservedNanoUsd'))
            if 'reservedNanoUsd' in prior and (prior_reserved is None or prior_reserved<0):
                raise MessageError('PROVIDER_BUDGET_UNAVAILABLE')
            if (self.cost_policy or prior_reserved) and old_window!=self.window and now<prior['windowStart']+(old_window or 86400):
                raise MessageError('PROVIDER_BUDGET_UNAVAILABLE')
            active=prior['windowStart']==window
            if active and (prior['attempts']>=self.cap or prior['failures']>=self.fail_cap):return None
        reserved=0
        unpriced=0
        if active:
            reserved=integral(prior.get('reservedNanoUsd'))
            unpriced=integral(prior.get('unpricedAttempts'))
            if reserved is None or unpriced is None:
                if self.cost_policy or any(k in prior for k in ('windowSeconds','reservedNanoUsd','unpricedAttempts')):
                    raise MessageError('PROVIDER_BUDGET_UNAVAILABLE')
                reserved=0;unpriced=int(prior['attempts'])
            if reserved<0 or unpriced<0 or unpriced>prior['attempts']:
                raise MessageError('PROVIDER_BUDGET_UNAVAILABLE')
        if self.cost_policy:
            if unpriced:raise MessageError('PROVIDER_BUDGET_UNAVAILABLE')
            reserved+=self.cost_policy.reservation_nano
            if reserved>self.money_cap:return None
        else:unpriced+=1
        old_revision=int(prior['revision']) if prior else 0
        attempts=int(prior['attempts'])+1 if active else 1
        failures=int(prior['failures']) if active else 0
        values={':type':'V1_MESSAGE_PROVIDER_BUDGET',':next':old_revision+1,':window':window,':attempts':attempts,':failures':failures,
                ':seconds':self.window,':reserved':reserved,':unpriced':unpriced}
        condition='attribute_not_exists(PK)'
        if prior:condition='revision = :old';values[':old']=old_revision
        self.a._transact([{'Update':{'TableName':self.a.s.authority_table,'Key':KEY,
            'UpdateExpression':'SET recordType = :type, revision = :next, windowStart = :window, attempts = :attempts, failures = :failures, windowSeconds = :seconds, reservedNanoUsd = :reserved, unpricedAttempts = :unpriced',
            'ConditionExpression':condition,'ExpressionAttributeValues':values}}])
        return window

    def failed(self,window):
        # Failure accounting is never permission to repeat provider work. On a
        # concurrent window rollover this bounded attempt may simply not apply.
        self.a._transact([{'Update':{'TableName':self.a.s.authority_table,'Key':KEY,
            'UpdateExpression':'ADD failures :one, revision :one',
            'ConditionExpression':'recordType = :type AND windowStart = :window',
            'ExpressionAttributeValues':{':one':1,':type':'V1_MESSAGE_PROVIDER_BUDGET',':window':window}}}])
