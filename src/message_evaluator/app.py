"""IAM-only evaluator, no message logging, model credentials or ledger writes."""
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
    if not policy_enabled('MESSAGE_EVALUATOR_ENABLED'):return {'enabled':False}
    try:
        require(type(event) is dict and set(event)=={'schemaVersion','checkId','policyVersion','intent','executionBudgetMs'})
        require(type(event['schemaVersion']) is int and event['schemaVersion']==1 and event['policyVersion']==POLICY)
        require(type(event['checkId']) is str and re.fullmatch('[A-Za-z0-9_-]{1,64}',event['checkId']))
        require(type(event['executionBudgetMs']) is int and 1<=event['executionBudgetMs']<=18000)
        require(context is not None and callable(getattr(context,'get_remaining_time_in_millis',None)))
        budget=min(event['executionBudgetMs'],context.get_remaining_time_in_millis()-1000)
        require(budget>0)
        return evaluate(event['checkId'],event['intent'],lookup=lookup,budget_ms=budget)
    except Exception:
        # Only fixed failure output, never an exception payload or raw request.
        return {'enabled':True,'errorCode':'EVALUATOR_UNAVAILABLE'}
