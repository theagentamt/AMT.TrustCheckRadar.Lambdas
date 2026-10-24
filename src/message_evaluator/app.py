"""IAM-only evaluator; optional gated proposer, no message logs or ledger writes."""
import json
import os
import re
from shared_message_contract import POLICY, APPROVAL_SHA
from shared_message_contract.validation import MessageError, require
from .policy import evaluate


def policy_enabled(flag):
    return (os.environ.get('STAGE')=='dev' and os.environ.get(flag)=='true' and
            os.environ.get('MESSAGE_POLICY_VERSION')==POLICY and os.environ.get('MESSAGE_POLICY_APPROVAL_SHA256')==APPROVAL_SHA)


def lookup(event):
    import boto3
    from botocore.config import Config
    arn=os.environ.get('URL_ASSESSMENT_FUNCTION_ARN','')
    require(arn=='arn:aws:lambda:us-east-1:107827791950:function:trustcheckradar-dev-url-assessment:live')
    client=boto3.client('lambda',region_name='us-east-1',config=Config(connect_timeout=.3,read_timeout=event['executionBudgetMs']/1000+.5,retries={'total_max_attempts':1}))
    try:
        response=client.invoke(FunctionName=arn,InvocationType='RequestResponse',Payload=json.dumps(event).encode())
        stream=response.get('Payload')
        try:raw=stream.read(16385) if stream else b''
        finally:
            if stream:stream.close()
        require(response.get('StatusCode')==200 and not response.get('FunctionError') and len(raw)<=16384)
        from shared_message_contract.runtime import unique_pairs
        return json.loads(raw,object_pairs_hook=unique_pairs)
    finally:client.close()


def lambda_handler(event,context):
    # Candidate.1 keeps its original gate; candidate.2 requires its own approved
    # policy selector. Neither client content nor a model can select that policy.
    from shared_message_contract import validation_v2 as v2
    is_v3=type(event) is dict and event.get('schemaVersion')==3 and event.get('policyVersion')==v2.POLICY
    is_v2=type(event) is dict and event.get('schemaVersion')==2 and event.get('policyVersion')==v2.POLICY
    rules_only=(is_v3 and event.get('runtimeMode')=='rules_only')
    primary=(is_v3 and event.get('runtimeMode')=='ai_primary_v1')
    if rules_only:
        enabled=(os.environ.get('STAGE')=='dev' and os.environ.get('MESSAGE_EVALUATOR_ENABLED')=='true'
                 and os.environ.get('MESSAGE_CANDIDATE3_RULES_ONLY_ENABLED')=='true'
                 and os.environ.get('MESSAGE_AI_ENABLED')!='true'
                 and os.environ.get('MESSAGE_AI_POLICY_VERSION')==v2.POLICY
                 and os.environ.get('MESSAGE_AI_POLICY_APPROVAL_SHA256')==v2.APPROVAL_SHA)
    elif is_v2 or is_v3:
        enabled=(os.environ.get('STAGE')=='dev' and os.environ.get('MESSAGE_EVALUATOR_ENABLED')=='true'
                 and os.environ.get('MESSAGE_AI_ENABLED')=='true' and os.environ.get('MESSAGE_AI_POLICY_VERSION')==v2.POLICY
                 and os.environ.get('MESSAGE_AI_POLICY_APPROVAL_SHA256')==v2.APPROVAL_SHA)
    else:enabled=policy_enabled('MESSAGE_EVALUATOR_ENABLED')
    if primary and (os.environ.get('MESSAGE_AI_PRIMARY_ENABLED')!='true' or os.environ.get('MESSAGE_AI_QUALIFIED')!='true'):enabled=False
    if not enabled:return {'enabled':False}
    try:
        expected={'schemaVersion','checkId','policyVersion','intent','executionBudgetMs'}|({'runtimeMode'} if rules_only or primary else set())|({'providerCost'} if (is_v2 or is_v3) and not rules_only else set())
        require(type(event) is dict and set(event)==expected)
        if rules_only:require(event['runtimeMode']=='rules_only')
        require(type(event['schemaVersion']) is int and event['schemaVersion']==(3 if is_v3 else (2 if is_v2 else 1)) and event['policyVersion']==(v2.POLICY if is_v2 or is_v3 else POLICY))
        require(type(event['checkId']) is str and re.fullmatch('[A-Za-z0-9_-]{1,64}',event['checkId']))
        require(type(event['executionBudgetMs']) is int and 1<=event['executionBudgetMs']<=18000)
        require(context is not None and callable(getattr(context,'get_remaining_time_in_millis',None)))
        budget=min(event['executionBudgetMs'],context.get_remaining_time_in_millis()-1000)
        require(budget>0)
        if is_v2 or is_v3:
            from .policy_v2 import evaluate as evaluate_ai
            if is_v3:
                from .policy_v3 import evaluate as evaluate_ai
            if rules_only:
                return evaluate_ai(event['checkId'],event['intent'],lookup=None,budget_ms=budget,ai=None,rules_only=True)
            from .ai_provider import assess
            from shared_message_contract.provider_cost import CostPolicy
            cost=CostPolicy.from_env()
            require(CostPolicy.from_dict(event['providerCost'])==cost)
            return evaluate_ai(event['checkId'],event['intent'],lookup=lookup,budget_ms=budget,
                ai=lambda intent,remaining:assess(intent,remaining,cost_policy=cost),
                **({'ai_primary':True} if primary else {}))
        proposer = None
        if os.environ.get('MESSAGE_PROPOSER_ENABLED') == 'true':
            # The old optional candidate.1 paid prompt has no approved monetary
            # policy. Keep deterministic/link behavior, but never bypass the
            # normal qualified AI budget through this legacy switch.
            def proposer(intent, remaining_ms):
                raise ValueError('LEGACY_PAID_PROPOSER_NOT_QUALIFIED')
        return evaluate(event['checkId'],event['intent'],lookup=lookup,budget_ms=budget,proposer=proposer)
    except Exception:
        # Only fixed failure output, never an exception payload or raw request.
        return {'enabled':True,'errorCode':'EVALUATOR_UNAVAILABLE'}
