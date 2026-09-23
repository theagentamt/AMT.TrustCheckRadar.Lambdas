"""Separately gated billing-account preparation; no purchase, trial or provider call."""
import json
import os
from shared_check_authority.core import AuthorityError
CONTRACT='v1-play-preparation-1.0.0-candidate.1'


def handle(event):
    status=503;code='SERVICE_NOT_ENABLED'
    try:
        if os.environ.get('STAGE')!='dev' or os.environ.get('PLAY_PREPARATION_ENABLED')!='true':raise AuthorityError(code)
        from shared_check_authority.engineering import require_engineering_subject
        require_engineering_subject(event)
        from shared_play_verification.client import unique
        if (event.get('version')!='2.0' or event.get('routeKey')!='POST /v1/purchases/google-play/prepare'
                or event.get('requestContext',{}).get('http',{}).get('method')!='POST'
                or event.get('rawQueryString') or event.get('queryStringParameters') or event.get('isBase64Encoded') is True
                or not isinstance(event.get('body'),str) or len(event['body'].encode())>128):raise ValueError()
        body=json.loads(event['body'],object_pairs_hook=unique)
        if type(body) is not dict or set(body)!={'schemaVersion'} or type(body['schemaVersion']) is not int or body['schemaVersion']!=1:raise ValueError()
        from shared_check_authority.runtime import load_authority
        from shared_check_authority.entitlements import EntitlementWriter
        from .runtime import token_table
        from .bindings import Bindings
        result=Bindings(EntitlementWriter(load_authority(),approved_products=frozenset(),operator_principals=frozenset(),verification_max_age_seconds=20),token_table()).prepare(event)
        return _response(200,result)
    except (ValueError,TypeError):status,code=400,'INVALID_REQUEST'
    except AuthorityError as error:
        if str(error)=='AUTHENTICATION_REQUIRED':status,code=401,'AUTHENTICATION_REQUIRED'
        elif str(error) in ('ACTIVE_DEVICE_REQUIRED','ACCOUNT_UNAVAILABLE','ENGINEERING_ACCESS_UNAVAILABLE'):status,code=403,'ACCESS_UNAVAILABLE'
        elif str(error)!='SERVICE_NOT_ENABLED':code='SERVICE_UNAVAILABLE'
    except Exception:code='SERVICE_UNAVAILABLE'
    return _response(status,{'schemaVersion':1,'contractVersion':CONTRACT,'error':{'code':code,'retryable':status==503 and code!='SERVICE_NOT_ENABLED'}})


def _response(status,body):
    return {'statusCode':status,'headers':{'Content-Type':'application/json','Cache-Control':'no-store'},'body':json.dumps(body,separators=(',',':'))}
