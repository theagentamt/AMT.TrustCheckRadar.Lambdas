"""Bounded fixed-host Google client. Construction does not read credentials."""
import json
import time
from urllib.parse import quote
from .proof import PACKAGE, PRODUCT, PlayVerificationError, ORDER

BASE='https://androidpublisher.googleapis.com/androidpublisher/v3/applications/'+PACKAGE
SCOPE='https://www.googleapis.com/auth/androidpublisher'
MAX_BYTES=65536


def unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate')
        result[key]=value
    return result


class PlayClient:
    def __init__(self, session, *, deadline_monotonic, clock=time.monotonic):
        self.session=session;self.deadline=deadline_monotonic;self.clock=clock;self.calls=0

    def _request(self, method, path):
        remaining=self.deadline-self.clock()
        if remaining<=0.25 or self.calls>=28:raise PlayVerificationError('PLAY_PROVIDER_BUDGET_EXHAUSTED')
        self.calls+=1
        try:
            response=self.session.request(method,BASE+path,timeout=(min(1,remaining),min(2,remaining)),allow_redirects=False,stream=True,max_allowed_time=remaining,**({'json':{}} if method=='POST' else {}))
            try:
                if self.clock()>=self.deadline:raise PlayVerificationError('PLAY_PROVIDER_BUDGET_EXHAUSTED')
                if response.status_code not in (200,204):
                    code='PLAY_TOKEN_REJECTED' if response.status_code in (400,404,410) else 'PLAY_PROVIDER_UNAVAILABLE'
                    raise PlayVerificationError(code)
                raw=bytearray()
                for chunk in response.iter_content(chunk_size=4096):
                    if self.clock()>=self.deadline:raise PlayVerificationError('PLAY_PROVIDER_BUDGET_EXHAUSTED')
                    raw.extend(chunk)
                    if len(raw)>MAX_BYTES:raise PlayVerificationError('PLAY_RESPONSE_INVALID')
                if self.clock()>=self.deadline:raise PlayVerificationError('PLAY_PROVIDER_BUDGET_EXHAUSTED')
                if method=='POST':return None
                parsed=json.loads(raw,object_pairs_hook=unique)
                if type(parsed) is not dict:raise PlayVerificationError('PLAY_RESPONSE_INVALID')
                return parsed
            finally:response.close()
        except PlayVerificationError:raise
        except Exception:raise PlayVerificationError('PLAY_PROVIDER_UNAVAILABLE') from None

    def subscription(self, token):
        from .proof import _token
        _token(token)
        return self._request('GET','/purchases/subscriptionsv2/tokens/'+quote(token,safe=''))

    def order(self, order_id):
        if not isinstance(order_id,str) or not ORDER.fullmatch(order_id):raise PlayVerificationError('PLAY_RESPONSE_INVALID')
        return self._request('GET','/orders/'+quote(order_id,safe=''))

    def acknowledge(self, token):
        from .proof import _token
        _token(token)
        return self._request('POST','/purchases/subscriptions/'+PRODUCT+'/tokens/'+quote(token,safe='')+':acknowledge')

    def close(self):self.session.close()


def authorized_session(secret_client,secret_arn):
    """Runtime calls only after its explicit feature/config/inventory gates."""
    import re
    if not isinstance(secret_arn,str) or not re.fullmatch(r'arn:aws:secretsmanager:us-east-1:107827791950:secret:[A-Za-z0-9/_+=.@-]+',secret_arn):raise PlayVerificationError('PLAY_CONFIGURATION_UNAVAILABLE')
    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2.service_account import Credentials
        response=secret_client.get_secret_value(SecretId=secret_arn,VersionStage='AWSCURRENT')
        raw=response.get('SecretString')
        if not isinstance(raw,str) or len(raw.encode())>32768:raise ValueError()
        payload=json.loads(raw,object_pairs_hook=unique)
        credentials=Credentials.from_service_account_info(payload,scopes=[SCOPE])
        return AuthorizedSession(credentials,max_refresh_attempts=0,refresh_timeout=2)
    except Exception:raise PlayVerificationError('PLAY_CONFIGURATION_UNAVAILABLE') from None
