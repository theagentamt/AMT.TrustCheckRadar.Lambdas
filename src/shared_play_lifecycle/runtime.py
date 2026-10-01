"""Closed Dev runtime composition; no secrets/provider before explicit caller gate."""
import json
import os
import time
from shared_check_authority.core import AuthorityError


def token_table():
    value=os.environ['PLAY_TOKEN_TABLE_NAME']
    if not value or value==os.environ['AUTHORITY_TABLE_NAME']:raise AuthorityError('PLAY_TOKEN_CONFIGURATION_REQUIRED')
    return value


def cipher():
    import boto3
    from botocore.config import Config
    from .tokens import KmsTokenCipher
    readable=json.loads(os.environ['PLAY_TOKEN_READABLE_KMS_KEYS_JSON'])
    if not isinstance(readable,list) or len(set(readable))!=len(readable):raise AuthorityError('PLAY_TOKEN_CONFIGURATION_REQUIRED')
    return KmsTokenCipher(boto3.client('kms',region_name='us-east-1',config=Config(connect_timeout=.2,read_timeout=.3,retries={'total_max_attempts':1})),
        active_key_arn=os.environ['PLAY_TOKEN_KMS_KEY_ARN'],readable_key_arns=frozenset(readable))


def load():
    from shared_check_authority.engineering import allowed_subjects
    from shared_check_authority.entitlements import TrustedLifecycleWorker
    from v1_play_handoff.app import _runtime
    from .reconciliation import Reconciler
    if os.environ.get('STAGE')!='dev' or os.environ.get('PLAY_LIFECYCLE_ENABLED')!='true':raise AuthorityError('LIFECYCLE_UNAVAILABLE')
    allowed=allowed_subjects()
    principal=os.environ['PLAY_LIFECYCLE_WORKER_PRINCIPAL_ARN']
    import re
    if not re.fullmatch(r'arn:aws:iam::107827791950:role/[A-Za-z0-9+=,.@_/-]{1,128}',principal):raise AuthorityError('LIFECYCLE_CONFIGURATION_REQUIRED')
    handoff=_runtime()
    handoff.writer.lifecycle_principals=frozenset({principal})
    try:return Reconciler(handoff.writer,handoff.ownership,handoff.client,TrustedLifecycleWorker(principal),token_table=token_table(),token_cipher=handoff.token_cipher or cipher(),require_test=True,allowed_subjects=allowed)
    except Exception:
        handoff.client.close();raise


def close(service):
    if service is not None:
        service.client.close()
        service.cipher.client.close()


def resource():
    import boto3
    from botocore.config import Config
    return boto3.resource('dynamodb',region_name='us-east-1',config=Config(connect_timeout=.2,read_timeout=.3,retries={'total_max_attempts':1}))


def verify_google_jwt(token,audience):
    # google-auth owns signature/issuer/audience verification. Pin its certificate
    # fetch destination and bound network time; never accept a decoded JWT alone.
    from google.oauth2 import id_token
    from google.auth.transport.requests import Request
    import requests
    class Certificates(Request):
        def __call__(self,url,method='GET',body=None,headers=None,timeout=None,**kwargs):
            if url!='https://www.googleapis.com/oauth2/v1/certs' or method!='GET':raise ValueError()
            return super().__call__(url,method=method,body=body,headers=headers,timeout=2,allow_redirects=False)
    with requests.Session() as session:
        return id_token.verify_oauth2_token(token,Certificates(session),audience=audience)
