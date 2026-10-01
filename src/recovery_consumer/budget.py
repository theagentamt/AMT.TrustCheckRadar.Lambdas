"""Aggregate provider attempts/circuit state; no account or submitted content."""
from shared_check_authority.core import integral
from shared_recovery_contract.constants import RecoveryError as MessageError

KEY={'PK':'V1#CONTROL','SK':'RECOVERY_PROVIDER_BUDGET'}


class ProviderBudget:
    def __init__(self,authority,*,window_seconds,max_attempts,max_failures,circuit_open):
        if (any(type(x) is not int or x<=0 for x in (window_seconds,max_attempts,max_failures)) or
                window_seconds>86400 or max_attempts>100000 or max_failures>max_attempts or type(circuit_open) is not bool):
            raise MessageError('PROVIDER_BUDGET_UNAVAILABLE')
        self.a=authority;self.window=window_seconds;self.cap=max_attempts;self.fail_cap=max_failures;self.open=circuit_open

    def reserve(self):
        if self.open:return None
        window=self.a.now()//self.window*self.window
        prior=self.a._get(self.a.s.authority_table,KEY)
        if prior:
            if (prior.get('recordType')!='V1_RECOVERY_PROVIDER_BUDGET' or any(integral(prior.get(k)) is None or prior[k]<0 for k in ('revision','windowStart','attempts','failures'))):
                raise MessageError('PROVIDER_BUDGET_UNAVAILABLE')
            if prior['windowStart']==window and (prior['attempts']>=self.cap or prior['failures']>=self.fail_cap):return None
        old_revision=int(prior['revision']) if prior else 0
        attempts=int(prior['attempts'])+1 if prior and prior['windowStart']==window else 1
        failures=int(prior['failures']) if prior and prior['windowStart']==window else 0
        values={':type':'V1_RECOVERY_PROVIDER_BUDGET',':next':old_revision+1,':window':window,':attempts':attempts,':failures':failures}
        condition='attribute_not_exists(PK)'
        if prior:condition='revision = :old';values[':old']=old_revision
        self.a._transact([{'Update':{'TableName':self.a.s.authority_table,'Key':KEY,
            'UpdateExpression':'SET recordType = :type, revision = :next, windowStart = :window, attempts = :attempts, failures = :failures',
            'ConditionExpression':condition,'ExpressionAttributeValues':values}}])
        return window

    def failed(self,window):
        # Failure accounting is never permission to repeat provider work. On a
        # concurrent window rollover this bounded attempt may simply not apply.
        self.a._transact([{'Update':{'TableName':self.a.s.authority_table,'Key':KEY,
            'UpdateExpression':'ADD failures :one, revision :one',
            'ConditionExpression':'recordType = :type AND windowStart = :window',
            'ExpressionAttributeValues':{':one':1,':type':'V1_RECOVERY_PROVIDER_BUDGET',':window':window}}}])
