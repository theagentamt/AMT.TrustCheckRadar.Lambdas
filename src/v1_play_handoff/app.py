"""Closed Dev candidate. No secret/provider access before all explicit gates."""
import json
import os
import time
from .service import CONTRACT,Handoff,request_payload
from shared_check_authority.core import AuthorityError
from shared_play_verification.proof import PlayVerificationError,PRODUCT,PACKAGE,BASE_PLAN

ERRORS={
'INPUT_REJECTED':(400,False),'AUTHENTICATION_REQUIRED':(401,False),'ACCOUNT_UNAVAILABLE':(403,False),'ACTIVE_DEVICE_REQUIRED':(409,False),
'REQUEST_CONFLICT':(409,False),'PURCHASE_ACCOUNT_MISMATCH':(409,False),'PURCHASE_OWNERSHIP_CONFLICT':(409,False),
'PURCHASE_VERIFICATION_UNAVAILABLE':(503,True),'PURCHASE_PLAN_UNSUPPORTED':(409,False),'PURCHASE_TRANSITION_UNSUPPORTED':(409,False),
'PURCHASE_TEST_REQUIRED':(409,False),'PURCHASE_REJECTED':(409,False),'PURCHASE_RECONCILIATION_REQUIRED':(503,True),'PURCHASE_SERVICE_UNAVAILABLE':(503,False),
}

def _response(status,body):return {'statusCode':status,'headers':{'Content-Type':'application/json','Cache-Control':'no-store'},'body':json.dumps(body,separators=(',',':'))}

def _code(error):
    code=str(error)
    if code in ERRORS:return code
    if code in {'PLAY_PLAN_UNSUPPORTED','PLAY_ORDER_MISMATCH'}:return 'PURCHASE_PLAN_UNSUPPORTED'
    if code in {'PLAY_TRANSITION_UNSUPPORTED','PLAY_PERIOD_EXTENSION_UNSUPPORTED','RESTORE_POLICY_UNAVAILABLE'}:return 'PURCHASE_TRANSITION_UNSUPPORTED'
    if code in {'PLAY_TOKEN_REJECTED','PLAY_ACCESS_EXPIRED','PLAY_ORDER_NOT_FUNDED'}:return 'PURCHASE_REJECTED'
    if code=='PLAY_TEST_PURCHASE_REQUIRED':return 'PURCHASE_TEST_REQUIRED'
    if code.startswith('PLAY_PROVIDER') or code in {'PLAY_RESPONSE_INVALID','PLAY_FUNDED_PERIOD_UNAVAILABLE'}:return 'PURCHASE_VERIFICATION_UNAVAILABLE'
    if code in {'TRANSACTION_UNCERTAIN','STORE_OBSERVATION_CHANGED','PLAY_OBSERVATION_CHANGED','PURCHASE_OBSERVATION_CHANGED','PURCHASE_TRANSACTION_UNCONFIRMED','PLAY_OWNERSHIP_UNCONFIRMED','STORE_AUTHORITY_MIGRATION_REQUIRED','AUTHORITY_OPERATION_CONFLICT','OVERLAPPING_STORE_PERIOD','IMMUTABLE_PERIOD_CONFLICT','PURCHASE_USAGE_UNAVAILABLE','PURCHASE_USAGE_MISMATCH','PURCHASE_USAGE_RECONCILIATION_REQUIRED','PURCHASE_USAGE_INVALID'}:return 'PURCHASE_RECONCILIATION_REQUIRED'
    return 'PURCHASE_SERVICE_UNAVAILABLE'

def _runtime():
    if (os.environ.get('PLAY_CATALOG_P1M_VERIFIED')!='true' or os.environ.get('GOOGLE_PLAY_PACKAGE_NAME')!=PACKAGE
        or os.environ.get('GOOGLE_PLAY_PRODUCT_ID')!=PRODUCT or os.environ.get('GOOGLE_PLAY_BASE_PLAN_ID')!=BASE_PLAN
        or os.environ.get('GOOGLE_PLAY_BILLING_PERIOD')!='P1M' or os.environ.get('PLAY_REQUIRE_TEST_PURCHASES')!='true'):
        raise PlayVerificationError('PURCHASE_SERVICE_UNAVAILABLE')
    from shared_check_authority.runtime import load_authority
    from shared_check_authority.entitlements import EntitlementWriter
    from shared_purchase_ownership.service import OwnershipStore
    from shared_play_verification.client import authorized_session,PlayClient
    import boto3
    from botocore.config import Config
    a=load_authority();table=os.environ['PURCHASE_OWNERSHIP_TABLE_NAME']
    ownership=OwnershipStore(table=a.ddb.Table(table),ledger=a.ddb.Table(a.s.deletion_table),users_table_name=a.s.users_table,table_name=table,ledger_table_name=a.s.deletion_table,client=a.ddb.meta.client,environment='dev',now=a.now)
    ownership.inventory()
    writer=EntitlementWriter(a,approved_products=frozenset({('google_play',PRODUCT)}),operator_principals=frozenset(),verification_max_age_seconds=20)
    deadline=time.monotonic()+20
    secret=boto3.client('secretsmanager',region_name='us-east-1',config=Config(connect_timeout=.3,read_timeout=.5,retries={'total_max_attempts':1}))
    try:session=authorized_session(secret,os.environ['GOOGLE_PLAY_SERVICE_ACCOUNT_SECRET_ARN'])
    finally:secret.close()
    client=PlayClient(session,deadline_monotonic=deadline)
    return Handoff(writer,ownership,client,allow_test=True,require_test=True)

def lambda_handler(event,_context):
    request_id=None;handoff=None
    try:
        if os.environ.get('STAGE')!='dev' or os.environ.get('PLAY_HANDOFF_ENABLED')!='true':raise PlayVerificationError('PURCHASE_SERVICE_UNAVAILABLE')
        from shared_check_authority.engineering import require_engineering_subject
        require_engineering_subject(event)
        if (type(event) is not dict or event.get('version')!='2.0' or event.get('routeKey')!='POST /v1/purchases/google-play/verify' or event.get('requestContext',{}).get('http',{}).get('method')!='POST' or event.get('isBase64Encoded') is True or event.get('rawQueryString') or event.get('queryStringParameters')):raise PlayVerificationError('INPUT_REJECTED')
        raw=event.get('body')
        if not isinstance(raw,str) or len(raw.encode())>5120:raise PlayVerificationError('INPUT_REJECTED')
        from shared_play_verification.client import unique
        try:payload=request_payload(json.loads(raw,object_pairs_hook=unique))
        except (ValueError,TypeError):raise PlayVerificationError('INPUT_REJECTED') from None
        request_id=payload['requestId'];handoff=_runtime()
        return _response(200,handoff.handle(event,payload))
    except Exception as error:
        code=_code(error);status,retryable=ERRORS[code]
        return _response(status,{'schemaVersion':1,'contractVersion':CONTRACT,'requestId':request_id,'error':{'code':code,'retryable':retryable}})
    finally:
        if handoff is not None:handoff.client.close()
