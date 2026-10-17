"""Dev-only fixed verifier failure enums; never inspect exception or request data."""
import json
import logging
import os

_STATUSES = {
    'INPUT_REJECTED': 400, 'AUTHENTICATION_REQUIRED': 401, 'ACCOUNT_UNAVAILABLE': 403,
    'ACTIVE_DEVICE_REQUIRED': 409, 'REQUEST_CONFLICT': 409,
    'PURCHASE_ACCOUNT_MISMATCH': 409, 'PURCHASE_OWNERSHIP_CONFLICT': 409,
    'PURCHASE_PLAN_UNSUPPORTED': 409, 'PURCHASE_TRANSITION_UNSUPPORTED': 409,
    'PURCHASE_TEST_REQUIRED': 409, 'PURCHASE_REJECTED': 409,
    'PURCHASE_VERIFICATION_UNAVAILABLE': 503, 'PURCHASE_RECONCILIATION_REQUIRED': 503,
    'PURCHASE_SERVICE_UNAVAILABLE': 503,
}
_STAGES = frozenset({'ADMISSION', 'REQUEST_VALIDATION', 'RUNTIME_INITIALIZATION', 'VERIFICATION'})


def record_failure(event, code, status, stage):
    """Best effort only. No arbitrary values, traceback, IDs, bodies or exception text."""
    try:
        if (os.environ.get('STAGE') != 'dev' or os.environ.get('PLAY_HANDOFF_ENABLED') != 'true'
                or type(event) is not dict or event.get('routeKey') != 'POST /v1/purchases/google-play/verify'
                or type(event.get('requestContext')) is not dict
                or type(event['requestContext'].get('http')) is not dict
                or event['requestContext']['http'].get('method') != 'POST'):
            return False
        known = type(code) is str and code in _STATUSES
        value = {'event': 'dev_play_verification_failure', 'schemaVersion': 1,
                 'errorCode': code if known else 'UNKNOWN',
                 'httpStatus': status if known and type(status) is int and status == _STATUSES[code] else 0,
                 'stage': stage if type(stage) is str and stage in _STAGES else 'UNKNOWN'}
        logging.getLogger(__name__).warning('%s', json.dumps(value, separators=(',', ':')))
        return True
    except Exception:
        return False
