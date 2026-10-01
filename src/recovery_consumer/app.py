"""Disabled-by-default authenticated recovery API; no content in logs."""
import json
import os
import secrets
from .service import Consumer, unavailable_envelope
from .budget import ProviderBudget
from shared_recovery_contract.constants import POLICY, APPROVAL_SHA, PLAYBOOK
from shared_message_contract.runtime import unique_pairs


def provider(event):
    import boto3
    from botocore.config import Config
    arn=os.environ.get('RECOVERY_EVALUATOR_FUNCTION_ARN','')
    if arn!='arn:aws:lambda:us-east-1:107827791950:function:trustcheckradar-dev-recovery-evaluator:live':raise ValueError()
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


def lambda_handler(event,context):
    if (os.environ.get('STAGE')!='dev' or os.environ.get('RECOVERY_CONSUMER_ENABLED')!='true' or
        os.environ.get('AUTHORITY_ENABLED')!='true'):
        return response(503,unavailable_envelope('SERVICE_NOT_ENABLED'))
    try:
        if os.environ.get('RECOVERY_POLICY_VERSION')!=POLICY or os.environ.get('RECOVERY_POLICY_APPROVAL_SHA256')!=APPROVAL_SHA or os.environ.get('RECOVERY_PLAYBOOK_VERSION')!=PLAYBOOK:raise ValueError()
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
        circuit=os.environ.get('RECOVERY_PROVIDER_CIRCUIT_OPEN','true')
        if circuit not in ('true','false'):raise ValueError()
        budget=ProviderBudget(authority,window_seconds=required_number('RECOVERY_PROVIDER_WINDOW_SECONDS'),
            max_attempts=required_number('RECOVERY_PROVIDER_ATTEMPTS_PER_WINDOW'),
            max_failures=required_number('RECOVERY_PROVIDER_FAILURES_PER_WINDOW'),circuit_open=circuit=='true')
        consumer=Consumer(authority,provider,lambda event:writer.refresh_for_account(event,'recovery-'+secrets.token_hex(8)),budget,
            remaining_ms=context.get_remaining_time_in_millis)
        status,body=consumer.handle(event)
    except Exception:
        return response(503,unavailable_envelope())
    print(json.dumps({'event':'recovery_consumer','statusCode':status,'state':body['state'],'reason':body['errorCode'] or 'NONE'}))
    return response(status,body)
