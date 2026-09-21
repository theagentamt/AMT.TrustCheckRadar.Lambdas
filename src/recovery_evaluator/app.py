"""Disabled private recovery evaluator. No storage or other Lambda calls."""
import os
import re
from shared_recovery_contract.constants import *
from .policy import evaluate


def lambda_handler(event,context):
    if (os.environ.get('STAGE')!='dev' or os.environ.get('RECOVERY_EVALUATOR_ENABLED')!='true'
        or os.environ.get('RECOVERY_AI_ENABLED')!='true' or os.environ.get('RECOVERY_POLICY_VERSION')!=POLICY
        or os.environ.get('RECOVERY_POLICY_APPROVAL_SHA256')!=APPROVAL_SHA or os.environ.get('RECOVERY_PLAYBOOK_VERSION')!=PLAYBOOK):
        return {'enabled':False}
    try:
        require(type(event) is dict and set(event)=={'schemaVersion','checkId','policyVersion','playbookVersion','intent','executionBudgetMs'})
        require(type(event['schemaVersion']) is int and event['schemaVersion']==1 and event['policyVersion']==POLICY and event['playbookVersion']==PLAYBOOK)
        require(type(event['checkId']) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',event['checkId']))
        require(type(event['executionBudgetMs']) is int and 1<=event['executionBudgetMs']<=18000)
        require(context is not None and callable(getattr(context,'get_remaining_time_in_millis',None)))
        budget=min(event['executionBudgetMs'],context.get_remaining_time_in_millis()-1000);require(budget>0)
        from .ai_provider import assess
        return evaluate(event['checkId'],event['intent'],ai=assess,budget_ms=budget)
    except Exception:return {'enabled':True,'errorCode':'EVALUATOR_UNAVAILABLE'}
