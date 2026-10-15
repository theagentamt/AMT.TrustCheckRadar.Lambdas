"""Server-owned synthetic URL qualification, independent of customer admission."""
import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit


class Closed(Exception):
    """Never include request, identity, proof, SDK or URL details."""


@dataclass(frozen=True)
class Policy:
    run_id: str
    start: int
    end: int
    targets: dict
    table: str
    admission: bool
    provider: bool
    retention: bool
    now: object = time.time

    @property
    def manifest_digest(self):
        value = [self.run_id, self.start, self.end, sorted(self.targets.items())]
        return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()

    def active(self):
        if not self.retention or not self.start <= self.now() < self.end:
            raise Closed('QUALIFICATION_CLOSED')

    def target(self, url):
        self.active()
        for target_id, value in self.targets.items():
            if value == url:
                return target_id
        raise Closed('TARGET_NOT_APPROVED')

    def require_admission(self, url):
        if not self.admission:
            raise Closed('ADMISSION_CLOSED')
        return self.target(url)

    def require_provider(self, url):
        if not self.provider:
            raise Closed('PROVIDER_CLOSED')
        return self.target(url)


def configured():
    # Absent opt-in preserves the retained legacy artifact behavior. Once any
    # qualification configuration is supplied, malformed/OFF mode cannot fall
    # back to historical unrestricted private-provider behavior.
    names = ('URL_QUALIFICATION_ENABLED', 'URL_NEW_ADMISSION_ENABLED',
             'URL_PROVIDER_ENABLED', 'URL_RECONCILIATION_ENABLED', 'URL_RETENTION_APPROVED',
             'URL_QUALIFICATION_MANIFEST_JSON', 'URL_QUALIFICATION_TABLE_NAME', 'URL_SUBJECT_ALLOWLIST_JSON')
    return any(name in os.environ for name in names)


def mode():
    return os.environ.get('URL_QUALIFICATION_ENABLED') == 'true'


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:raise ValueError()
        result[key] = value
    return result


def load_policy(*, now=time.time):
    try:
        if os.environ.get('STAGE') != 'dev' or not mode():
            raise ValueError()
        raw = os.environ['URL_QUALIFICATION_MANIFEST_JSON']
        if len(raw.encode()) > 12000:
            raise ValueError()
        value = json.loads(raw, object_pairs_hook=_unique)
        if (set(value) != {'version', 'runId', 'startEpoch', 'endEpoch', 'syntheticOnly', 'targets'}
                or type(value['version']) is not int or value['version'] != 1 or value['syntheticOnly'] is not True
                or not isinstance(value['runId'], str) or not re.fullmatch(r'[0-9]{10}_[A-Za-z0-9_-]{1,32}', value['runId'])
                or type(value['startEpoch']) is not int or type(value['endEpoch']) is not int
                or not value['runId'].startswith(str(value['startEpoch']) + '_')
                or not 0 < value['endEpoch'] - value['startEpoch'] <= 3600
                or not isinstance(value['targets'], list) or not 1 <= len(value['targets']) <= 16):
            raise ValueError()
        targets = {}
        for target in value['targets']:
            if set(target) != {'id', 'url'} or not re.fullmatch('[A-Za-z0-9_-]{1,48}', target['id']):
                raise ValueError()
            url = target['url']
            parsed = urlsplit(url)
            if (not isinstance(url, str) or not 1 <= len(url.encode()) <= 2048
                    or parsed.scheme not in ('http', 'https') or not parsed.hostname
                    or parsed.username or parsed.password or '#' in url or '\\' in url
                    or any(ord(c) <= 32 or ord(c) >= 127 for c in url)
                    or target['id'] in targets or url in targets.values()):
                raise ValueError()
            targets[target['id']] = url
        table = os.environ['URL_QUALIFICATION_TABLE_NAME']
        if not re.fullmatch('[A-Za-z0-9_.-]{3,255}', table):
            raise ValueError()
        return Policy(value['runId'], value['startEpoch'], value['endEpoch'], targets, table,
                      os.environ.get('URL_NEW_ADMISSION_ENABLED') == 'true',
                      os.environ.get('URL_PROVIDER_ENABLED') == 'true',
                      os.environ.get('URL_RETENTION_APPROVED') == 'true', now)
    except Exception:
        raise Closed('CONFIGURATION_CLOSED') from None


def require_subject(event):
    """Separate allowlist; never broadens engineering/message/history gates."""
    from shared_history.security import jwt_subject
    from types import SimpleNamespace
    try:
        if os.environ.get('STAGE') != 'dev':
            raise ValueError()
        raw = os.environ['URL_SUBJECT_ALLOWLIST_JSON']
        if len(raw) > 512:
            raise ValueError()
        subjects = json.loads(raw)
        if (not isinstance(subjects, list) or len(subjects) != 1
                or len(set(subjects)) != len(subjects)
                or any(not isinstance(s, str) or str(uuid.UUID(s)) != s for s in subjects)):
            raise ValueError()
        settings = SimpleNamespace(cognito_issuer=os.environ['COGNITO_ISSUER'],
                                   cognito_app_client_id=os.environ['COGNITO_APP_CLIENT_ID'],
                                   cognito_required_scope=os.environ['COGNITO_REQUIRED_SCOPE'])
        if (not settings.cognito_issuer.startswith('https://cognito-idp.')
                or not settings.cognito_app_client_id or settings.cognito_required_scope != 'aws.cognito.signin.user.admin'):
            raise ValueError()
        subject = jwt_subject(event, settings, now=lambda: int(time.time()))
        if subject not in subjects:
            raise ValueError()
        return subject
    except Exception:
        raise Closed('SUBJECT_CLOSED') from None
