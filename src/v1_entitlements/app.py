"""API Gateway JWT routes only; no request or exception logging."""
import json
import os

from shared_check_authority.core import AuthorityError
from shared_check_authority.entitlements import EntitlementWriter
from v1_entitlements.service import access_snapshot


def _writer():
    # Shared runtime owns exact secret ARN and all explicit policy configuration.
    from shared_check_authority.runtime import load_authority
    return EntitlementWriter(load_authority(), approved_products=frozenset(),
                             operator_principals=frozenset(), verification_max_age_seconds=1,
                             trial_retention_approved=os.environ.get('TRIAL_AUTHORITY_RETENTION_APPROVED') == 'true')


def _response(status, body):
    return {'statusCode': status, 'headers': {'Content-Type': 'application/json', 'Cache-Control': 'no-store'},
            'body': json.dumps(body, separators=(',', ':'))}


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate member')
        result[key] = value
    return result


def lambda_handler(event, _context):
    try:
        if os.environ.get('V1_ENTITLEMENTS_ENABLED') != 'true':
            raise AuthorityError('ACCESS_SERVICE_UNAVAILABLE')
        from shared_check_authority.engineering import require_engineering_subject
        require_engineering_subject(event)
        if not isinstance(event, dict) or event.get('version') != '2.0' or event.get('isBase64Encoded') is True:
            raise AuthorityError('INPUT_REJECTED')
        route = event.get('routeKey')
        request = event.get('requestContext')
        method = request.get('http', {}).get('method') if isinstance(request, dict) else None
        if event.get('rawQueryString') or event.get('queryStringParameters'):
            raise AuthorityError('INPUT_REJECTED')
        if route == 'POST /v1/access/trial' and method == 'POST':
            body = event.get('body')
            if not isinstance(body, str) or len(body.encode('utf-8')) > 256:
                raise AuthorityError('INPUT_REJECTED')
            try:
                payload = json.loads(body, object_pairs_hook=_unique_pairs)
            except (ValueError, TypeError):
                raise AuthorityError('INPUT_REJECTED') from None
            if not isinstance(payload, dict) or set(payload) != {'schemaVersion', 'activate'} or type(payload['schemaVersion']) is not int or payload['schemaVersion'] != 1 or payload['activate'] is not True:
                raise AuthorityError('INPUT_REJECTED')
            writer = _writer()
            writer.activate_trial(event)
        elif route == 'GET /v1/access' and method == 'GET':
            if event.get('body') not in (None, ''):
                raise AuthorityError('INPUT_REJECTED')
            writer = _writer()
        else:
            raise AuthorityError('INPUT_REJECTED')
        return _response(200, access_snapshot(writer, event))
    except AuthorityError as error:
        statuses = {'AUTHENTICATION_REQUIRED': 401, 'ACCOUNT_UNAVAILABLE': 403, 'ACTIVE_DEVICE_REQUIRED': 409,
                    'TRIAL_NOT_ELIGIBLE': 409, 'INPUT_REJECTED': 400, 'TRANSACTION_UNCERTAIN': 503,
                    'AUTHORITY_SNAPSHOT_CHANGED': 409}
        code = error.code if error.code in statuses else 'ACCESS_SERVICE_UNAVAILABLE'
        return _response(statuses.get(error.code, 503), {'schemaVersion': 1,
                         'error': {'code': code, 'retryable': error.code in ('TRANSACTION_UNCERTAIN', 'AUTHORITY_SNAPSHOT_CHANGED')}})
    except Exception:
        return _response(503, {'schemaVersion': 1, 'error': {'code': 'ACCESS_SERVICE_UNAVAILABLE', 'retryable': False}})
