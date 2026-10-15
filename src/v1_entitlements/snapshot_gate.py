"""Dev-only snapshot admission; never extends trial or other engineering gates."""
import json
import os
import time
from types import SimpleNamespace
from uuid import UUID

from shared_check_authority import engineering
from shared_check_authority.core import AuthorityError
from shared_history.security import jwt_subject


def trial_access_for_snapshot(event):
    """Validate the exact GET route; return whether original trial admission applies."""
    try:
        if (not isinstance(event, dict) or event.get('routeKey') != 'GET /v1/access'
                or event.get('requestContext', {}).get('http', {}).get('method') != 'GET'):
            raise ValueError()
        try:
            engineering.require_engineering_subject(event)
            return True
        except AuthorityError:
            # An invalid original gate must not acquire a fallback. Original subjects
            # retain their behavior even when the additional selection is invalid.
            engineering.allowed_subjects()
        raw = os.environ.get('DEV_ACCESS_SNAPSHOT_EXTRA_SUBJECTS_JSON', '[]')
        if len(raw) > 128:
            raise ValueError()
        extra = json.loads(raw)
        if (type(extra) is not list or len(extra) != 1 or not isinstance(extra[0], str)
                or str(UUID(extra[0])) != extra[0]):
            raise ValueError()
        settings = SimpleNamespace(
            cognito_issuer=os.environ['COGNITO_ISSUER'],
            cognito_app_client_id=os.environ['COGNITO_APP_CLIENT_ID'],
            cognito_required_scope=os.environ['COGNITO_REQUIRED_SCOPE'])
        if (not settings.cognito_issuer.startswith('https://cognito-idp.')
                or not settings.cognito_app_client_id
                or settings.cognito_required_scope != 'aws.cognito.signin.user.admin'):
            raise ValueError()
        subject = jwt_subject(event, settings, now=lambda: int(time.time()))
        if subject != extra[0]:
            raise ValueError()
        return False
    except Exception:
        raise AuthorityError('ENGINEERING_ACCESS_UNAVAILABLE') from None
