"""Dev prepare's existing account-JWT rejection only; no claim or exception values."""
import json
import logging
import os

PREDICATES = frozenset({
    'CLAIMS_UNAVAILABLE', 'SUBJECT_INVALID', 'ISSUER_MISMATCH', 'CLIENT_MISMATCH',
    'TOKEN_USE_MISMATCH', 'EXPIRY_INVALID', 'EXPIRY_NOT_FUTURE', 'SCOPE_MISSING',
})


def record_failure(event, predicate):
    try:
        if (os.environ.get('STAGE') != 'dev'
                or os.environ.get('PLAY_PREPARATION_ENABLED') != 'true'
                or os.environ.get('AUTHORITY_ENABLED') != 'true'
                or type(event) is not dict or event.get('version') != '2.0'
                or event.get('routeKey') != 'POST /v1/purchases/google-play/prepare'
                or type(event.get('requestContext')) is not dict
                or type(event['requestContext'].get('http')) is not dict
                or event['requestContext']['http'].get('method') != 'POST'):
            return False
        value = {'event': 'dev_play_prepare_auth_failure', 'schemaVersion': 1,
                 'stage': 'AUTHORITY_ACCOUNT_JWT',
                 'predicate': predicate if type(predicate) is str and predicate in PREDICATES else 'UNKNOWN'}
        logging.getLogger(__name__).warning('%s', json.dumps(value, separators=(',', ':')))
        return True
    except Exception:
        return False
