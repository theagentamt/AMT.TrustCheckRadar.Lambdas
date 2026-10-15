"""API Gateway JWT account routes and one AWS_IAM operator route; no request logging."""
import json
import os
import re

from shared_check_authority.core import AuthorityError
from shared_check_authority.entitlements import (COMPLIMENTARY_AUDIT_RETENTION_SECONDS,
                                                  EntitlementWriter, TrustedOperator)
from v1_entitlements.service import access_snapshot


def _operator_principals():
    try:
        values = json.loads(os.environ['COMPLIMENTARY_OPERATOR_PRINCIPAL_ARNS_JSON'])
    except (KeyError, TypeError, ValueError):
        raise AuthorityError('OPERATOR_CONFIGURATION_UNAVAILABLE') from None
    if (not isinstance(values, list) or not values or len(values) != len(set(values))
            or any(not isinstance(value, str) for value in values)):
        raise AuthorityError('OPERATOR_CONFIGURATION_UNAVAILABLE')
    return frozenset(values)


def _writer(*, operator=False):
    # Shared runtime owns exact secret ARN and all explicit policy configuration.
    from shared_check_authority.runtime import load_authority
    retention = None
    principals = frozenset()
    if operator:
        if os.environ.get('COMPLIMENTARY_OPERATOR_ENABLED') != 'true':
            raise AuthorityError('OPERATOR_SERVICE_UNAVAILABLE')
        if os.environ.get('COMPLIMENTARY_AUDIT_RETENTION_SECONDS') != str(COMPLIMENTARY_AUDIT_RETENTION_SECONDS):
            raise AuthorityError('OPERATOR_CONFIGURATION_UNAVAILABLE')
        retention = COMPLIMENTARY_AUDIT_RETENTION_SECONDS
        principals = _operator_principals()
    return EntitlementWriter(load_authority(), approved_products=frozenset(),
                             operator_principals=principals, verification_max_age_seconds=1,
                             trial_retention_approved=os.environ.get('TRIAL_AUTHORITY_RETENTION_APPROVED') == 'true',
                             complimentary_audit_retention_seconds=retention)


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


def _trusted_operator(event):
    request = event.get('requestContext') if isinstance(event, dict) else None
    authorizer = request.get('authorizer') if isinstance(request, dict) else None
    iam = authorizer.get('iam') if isinstance(authorizer, dict) else None
    user_arn = iam.get('userArn') if isinstance(iam, dict) else None
    caller_id = iam.get('callerId') if isinstance(iam, dict) else None
    account_id = iam.get('accountId') if isinstance(iam, dict) else None
    matched = re.fullmatch(
        r'arn:aws:sts::([0-9]{12}):assumed-role/([A-Za-z0-9_+=,.@-]{1,64})/([A-Za-z0-9_+=,.@-]{1,64})',
        user_arn or '')
    if (not matched or account_id != matched.group(1) or not isinstance(caller_id, str)
            or not 3 <= len(caller_id) <= 256):
        raise AuthorityError('OPERATOR_AUTHORIZATION_REQUIRED')
    principal = f'arn:aws:iam::{matched.group(1)}:role/{matched.group(2)}'
    if principal not in _operator_principals():
        raise AuthorityError('OPERATOR_AUTHORIZATION_REQUIRED')
    return TrustedOperator(principal, caller_id + '\0' + user_arn)


def _payload(event, *, maximum_bytes):
    body = event.get('body')
    if not isinstance(body, str) or len(body.encode('utf-8')) > maximum_bytes:
        raise AuthorityError('INPUT_REJECTED')
    try:
        value = json.loads(body, object_pairs_hook=_unique_pairs)
    except (ValueError, TypeError):
        raise AuthorityError('INPUT_REJECTED') from None
    if not isinstance(value, dict):
        raise AuthorityError('INPUT_REJECTED')
    return value


def lambda_handler(event, _context):
    try:
        if os.environ.get('V1_ENTITLEMENTS_ENABLED') != 'true':
            raise AuthorityError('ACCESS_SERVICE_UNAVAILABLE')
        route = event.get('routeKey') if isinstance(event, dict) else None
        trial_activation_permitted = True
        if route != 'POST /v1/operator/complimentary-access':
            if (route == 'GET /v1/access'
                    and event.get('requestContext', {}).get('http', {}).get('method') == 'GET'):
                from v1_entitlements.snapshot_gate import trial_access_for_snapshot
                trial_activation_permitted = trial_access_for_snapshot(event)
            else:
                from shared_check_authority.engineering import require_engineering_subject
                require_engineering_subject(event)
        if not isinstance(event, dict) or event.get('version') != '2.0' or event.get('isBase64Encoded') is True:
            raise AuthorityError('INPUT_REJECTED')
        request = event.get('requestContext')
        method = request.get('http', {}).get('method') if isinstance(request, dict) else None
        if event.get('rawQueryString') or event.get('queryStringParameters'):
            raise AuthorityError('INPUT_REJECTED')
        if route == 'POST /v1/operator/complimentary-access' and method == 'POST':
            operator = _trusted_operator(event)
            payload = _payload(event, maximum_bytes=1024)
            required = {'schemaVersion', 'operationId', 'accountId', 'action', 'reasonCode', 'expiresAtEpoch'}
            if (set(payload) != required or type(payload['schemaVersion']) is not int or payload['schemaVersion'] != 1
                    or payload['action'] not in ('grant', 'revoke')):
                raise AuthorityError('INPUT_REJECTED')
            writer = _writer(operator=True)
            result = writer.set_complimentary(
                operator, payload['accountId'], payload['operationId'],
                enabled=payload['action'] == 'grant', reason_code=payload['reasonCode'],
                expires_at=payload['expiresAtEpoch'])
            return _response(200, {'schemaVersion': 1, 'access': result})
        if route == 'POST /v1/access/trial' and method == 'POST':
            payload = _payload(event, maximum_bytes=256)
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
        return _response(200, access_snapshot(writer, event,
                                             trial_activation_permitted=trial_activation_permitted))
    except AuthorityError as error:
        statuses = {'AUTHENTICATION_REQUIRED': 401, 'ACCOUNT_UNAVAILABLE': 403, 'ACTIVE_DEVICE_REQUIRED': 409,
                    'TRIAL_NOT_ELIGIBLE': 409, 'INPUT_REJECTED': 400, 'INVALID_ACCOUNT': 400,
                    'TRANSACTION_UNCERTAIN': 503,
                    'AUTHORITY_SNAPSHOT_CHANGED': 409, 'OPERATOR_AUTHORIZATION_REQUIRED': 403,
                    'WRITER_INPUT_REJECTED': 400}
        code = error.code if error.code in statuses else 'ACCESS_SERVICE_UNAVAILABLE'
        return _response(statuses.get(error.code, 503), {'schemaVersion': 1,
                         'error': {'code': code, 'retryable': error.code in ('TRANSACTION_UNCERTAIN', 'AUTHORITY_SNAPSHOT_CHANGED')}})
    except Exception:
        return _response(503, {'schemaVersion': 1, 'error': {'code': 'ACCESS_SERVICE_UNAVAILABLE', 'retryable': False}})
