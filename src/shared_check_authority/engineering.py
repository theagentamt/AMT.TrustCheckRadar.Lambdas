"""Temporary Dev subject gate; validated JWT context only, before AWS work."""
import json
import os
import time
import uuid
from types import SimpleNamespace
from .core import AuthorityError
from shared_history.security import jwt_subject


def allowed_subjects():
    try:
        if os.environ.get('STAGE')!='dev':raise ValueError()
        raw=os.environ['DEV_SUBJECT_ALLOWLIST_JSON']
        if len(raw)>512:raise ValueError()
        allowed=json.loads(raw)
        if not isinstance(allowed,list) or not 1<=len(allowed)<=10 or len(set(allowed))!=len(allowed):raise ValueError()
        if any(not isinstance(s,str) or str(uuid.UUID(s))!=s for s in allowed):raise ValueError()
        return frozenset(allowed)
    except Exception:raise AuthorityError('ENGINEERING_ACCESS_UNAVAILABLE') from None


def require_engineering_subject(event):
    try:
        allowed=allowed_subjects()
        settings=SimpleNamespace(cognito_issuer=os.environ['COGNITO_ISSUER'],cognito_app_client_id=os.environ['COGNITO_APP_CLIENT_ID'],cognito_required_scope=os.environ['COGNITO_REQUIRED_SCOPE'])
        if not settings.cognito_issuer.startswith('https://cognito-idp.') or not settings.cognito_app_client_id or settings.cognito_required_scope!='aws.cognito.signin.user.admin':raise ValueError()
        subject=jwt_subject(event,settings,now=lambda:int(time.time()))
        if subject not in allowed:raise ValueError()
        return subject
    except Exception:raise AuthorityError('ENGINEERING_ACCESS_UNAVAILABLE') from None
