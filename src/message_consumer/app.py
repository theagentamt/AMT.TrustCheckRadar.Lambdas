"""Disabled-by-default authenticated message API; no content in logs."""
import json
import os
import secrets
from .service import Consumer, unavailable_envelope
from .budget import ProviderBudget
from shared_message_contract import POLICY, APPROVAL_SHA, VERSION
from shared_message_contract import validation_v2 as v2
from shared_message_contract.runtime import unique_pairs


def provider(event):
    import boto3
    from botocore.config import Config
    arn=os.environ.get('MESSAGE_EVALUATOR_FUNCTION_ARN','')
    if arn!='arn:aws:lambda:us-east-1:107827791950:function:trustcheckradar-dev-message-evaluator:live':raise ValueError()
    client=boto3.client('lambda',region_name='us-east-1',config=Config(connect_timeout=.3,read_timeout=event['executionBudgetMs']/1000+1,retries={'total_max_attempts':1}))
    try:
        response=client.invoke(FunctionName=arn,InvocationType='RequestResponse',Payload=json.dumps(event).encode())
        stream=response.get('Payload')
        try:raw=stream.read(16385) if stream else b''
        finally:
            if stream:stream.close()
        if response.get('StatusCode')!=200 or response.get('FunctionError') or len(raw)>16384:raise ValueError()
        return json.loads(raw,object_pairs_hook=unique_pairs)
    finally:client.close()


def response(status,body):
    return {'statusCode':status,'headers':{'Content-Type':'application/json','Cache-Control':'no-store'},'body':json.dumps(body,separators=(',',':'))}


def requested_version(event):
    try:
        raw=event.get('body') if type(event) is dict else None
        if type(raw) is not str or len(raw.encode())>32768:return VERSION
        value=json.loads(raw,object_pairs_hook=unique_pairs)
        return v2.VERSION if type(value) is dict and value.get('transportVersion')==v2.VERSION else VERSION
    except Exception:return VERSION


def lambda_handler(event,context):
    version=requested_version(event)
    if (os.environ.get('STAGE')!='dev' or os.environ.get('MESSAGE_CONSUMER_ENABLED')!='true' or
        os.environ.get('AUTHORITY_ENABLED')!='true'):
        return response(503,unavailable_envelope('SERVICE_NOT_ENABLED',version))
    try:
        if os.environ.get('MESSAGE_POLICY_VERSION')!=POLICY or os.environ.get('MESSAGE_POLICY_APPROVAL_SHA256')!=APPROVAL_SHA:raise ValueError()
        if context is None or not callable(getattr(context,'get_remaining_time_in_millis',None)):raise ValueError()
        from shared_check_authority.engineering import require_engineering_subject
        require_engineering_subject(event)
        from shared_check_authority.runtime import load_authority
        from shared_check_authority.entitlements import EntitlementWriter
        authority=load_authority()
        writer=EntitlementWriter(authority,approved_products=frozenset(),operator_principals=frozenset(),verification_max_age_seconds=1)
        def required_number(name):
            raw=os.environ.get(name,'')
            if not raw.isascii() or not raw.isdigit():raise ValueError()
            return int(raw)
        circuit=os.environ.get('MESSAGE_PROVIDER_CIRCUIT_OPEN','true')
        if circuit not in ('true','false'):raise ValueError()
        budget=ProviderBudget(authority,window_seconds=required_number('MESSAGE_PROVIDER_WINDOW_SECONDS'),
            max_attempts=required_number('MESSAGE_PROVIDER_ATTEMPTS_PER_WINDOW'),
            max_failures=required_number('MESSAGE_PROVIDER_FAILURES_PER_WINDOW'),circuit_open=circuit=='true')
        consumer=Consumer(authority,provider,lambda event:writer.refresh_for_account(event,'message-'+secrets.token_hex(8)),budget,
            remaining_ms=context.get_remaining_time_in_millis,allow_ai=(os.environ.get('MESSAGE_AI_ENABLED')=='true'
            and os.environ.get('MESSAGE_AI_POLICY_VERSION')==v2.POLICY and os.environ.get('MESSAGE_AI_POLICY_APPROVAL_SHA256')==v2.APPROVAL_SHA))
        status,body=consumer.handle(event)
    except Exception:
        return response(503,unavailable_envelope(version=version))
    print(json.dumps({'event':'message_consumer','statusCode':status,'state':body['state'],'reason':body['errorCode'] or 'NONE'}))
    return response(status,body)
