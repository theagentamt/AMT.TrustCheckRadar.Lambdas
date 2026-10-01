"""Dev HTTP API adapter. Disabled until authority/retention/device gates pass."""
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
from service import Consumer, unique_pairs, unavailable_envelope


def _mapper():
    path=Path(__file__).parent/'public_contract'/'reference_mapping.py'
    spec=importlib.util.spec_from_file_location('consumer_public_contract',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def _provider(event):
    import boto3
    from botocore.config import Config
    arn=os.environ.get('URL_ASSESSMENT_FUNCTION_ARN','')
    if not re.fullmatch(r'arn:aws:lambda:us-east-1:107827791950:function:trustcheckradar-dev-url-assessment:live',arn):raise ValueError()
    budget=event['executionBudgetMs']/1000
    client=boto3.client('lambda',region_name='us-east-1',config=Config(connect_timeout=0.5,read_timeout=budget+1,retries={'total_max_attempts':1}))
    try:
        response=client.invoke(FunctionName=arn,InvocationType='RequestResponse',Payload=json.dumps(event).encode())
        stream=response.get('Payload')
        try:raw=stream.read(16385) if stream else b''
        finally:
            if stream:stream.close()
        if response.get('StatusCode')!=200 or response.get('FunctionError') or len(raw)>16384:raise ValueError()
        return json.loads(raw,object_pairs_hook=unique_pairs)
    finally:client.close()


def lambda_handler(event,context):
    if os.environ.get('STAGE')!='dev' or os.environ.get('CONSUMER_ENABLED')!='true':
        return _response(503,unavailable_envelope())
    try:
        from shared_check_authority.engineering import require_engineering_subject
        require_engineering_subject(event)
        from shared_check_authority.runtime import load_authority
        from shared_check_authority.entitlements import EntitlementWriter
        authority=load_authority()
        writer=EntitlementWriter(authority,approved_products=frozenset(),operator_principals=frozenset(),verification_max_age_seconds=1)
        consumer=Consumer(authority,_mapper(),_provider,lambda event:writer.refresh_for_account(event,'consumer-'+secrets.token_hex(8)))
        status,body=consumer.handle(event)
    except Exception:
        # No inference about an uncertain transaction; no raw exception or request.
        return _response(503,unavailable_envelope())
    print(json.dumps({'event':'url_consumer','statusCode':status,'state':body['state'],'reason':body['errorCode'] or 'NONE'}))
    return _response(status,body)


def _response(status,body):
    return {'statusCode':status,'headers':{'Content-Type':'application/json','Cache-Control':'no-store'},'body':json.dumps(body,separators=(',',':'))}
