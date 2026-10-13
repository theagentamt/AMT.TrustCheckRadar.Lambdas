"""Fixed failure classifications for Dev GET access; no exception text or input data."""
import json
import logging
from shared_check_authority.core import AuthorityError

_AUTHORITY_CODES = frozenset({
    'ACCESS_SERVICE_UNAVAILABLE', 'AUTHENTICATION_REQUIRED', 'ENGINEERING_ACCESS_UNAVAILABLE',
    'ACCOUNT_UNAVAILABLE', 'ACTIVE_DEVICE_REQUIRED', 'AUTHORITY_STATE_INVALID',
    'AUTHORITY_SNAPSHOT_CHANGED', 'AUTHORITY_MIGRATION_REQUIRED', 'POLICY_CONFIGURATION_UNAVAILABLE',
    'KEY_INVENTORY_UNAVAILABLE', 'KEY_VERSION_UNAVAILABLE', 'WRITER_CONFIGURATION_UNAVAILABLE',
    'SDK_RETRY_CONFIGURATION_UNAVAILABLE', 'TRANSACTION_UNCERTAIN', 'INPUT_REJECTED',
    'INVALID_ACCOUNT', 'EXTERNAL_ACCESS_UNAVAILABLE',
})
_PROVIDER_CODES = frozenset({
    'AccessDeniedException', 'AccessDenied', 'ValidationException', 'ResourceNotFoundException',
    'DecryptionFailure', 'InvalidRequestException', 'InvalidParameterException',
    'ThrottlingException', 'InternalServiceError', 'InternalServerError',
    'ProvisionedThroughputExceededException', 'RequestLimitExceeded',
    'ConditionalCheckFailedException', 'TransactionCanceledException',
})
_STAGES = {
    ('v1_entitlements.snapshot_gate', 'trial_access_for_snapshot'): 'GET_ADMISSION',
    ('shared_check_authority.runtime', 'load_authority'): 'AUTHORITY_CONFIGURATION',
    ('shared_check_authority.inventory', 'load_keyring'): 'HMAC_KEYRING',
    ('shared_check_authority.inventory', 'verified_inventory'): 'HMAC_INVENTORY',
    ('shared_check_authority.core', 'validate'): 'AUTHORITY_CONFIGURATION',
    ('shared_check_authority.core', '_assert_account'): 'ACCOUNT_FENCE',
    ('shared_check_authority.core', '_account'): 'ACCOUNT_FENCE',
    ('shared_check_authority.core', '_device'): 'DEVICE_FENCE',
    ('shared_check_authority.core', '_grant'): 'GRANT_READ',
    ('shared_check_authority.entitlements', '_read'): 'AUTHORITY_READ',
    ('shared_check_authority.entitlements', 'trial_history'): 'TRIAL_HISTORY_READ',
    ('shared_check_authority.entitlements', 'refresh_for_account'): 'AUTHORITY_REFRESH',
    ('v1_entitlements.service', 'access_snapshot'): 'SNAPSHOT_SERVICE',
}


def classify(error):
    result = {'event': 'dev_access_snapshot_failure', 'schemaVersion': 1,
              'stage': 'GET_HANDLER', 'authorityCode': 'UNKNOWN', 'providerCode': 'NONE',
              'causeCategory': 'UNEXPECTED'}
    seen = set()
    for _ in range(8):
        if not isinstance(error, BaseException) or id(error) in seen:
            break
        seen.add(id(error))
        if isinstance(error, AuthorityError) and error.code in _AUTHORITY_CODES and result['authorityCode'] == 'UNKNOWN':
            result['authorityCode'] = error.code
        # Only the known SDK exception's fixed provider code is consulted.
        from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError
        if isinstance(error, (ValueError, TypeError)):
            result['causeCategory'] = 'VALIDATION'
        elif isinstance(error, (ConnectTimeoutError, ReadTimeoutError)):
            result['causeCategory'] = 'TRANSPORT'
        elif isinstance(error, AuthorityError) and result['causeCategory'] == 'UNEXPECTED':
            result['causeCategory'] = 'TYPED_AUTHORITY'
        if isinstance(error, ClientError):
            result['causeCategory'] = 'AWS_CLIENT'
            code = error.response.get('Error', {}).get('Code')
            if isinstance(code, str) and code in _PROVIDER_CODES:
                result['providerCode'] = code
        trace = error.__traceback__
        for _ in range(64):
            if trace is None:
                break
            pair = (trace.tb_frame.f_globals.get('__name__'), trace.tb_frame.f_code.co_name)
            if pair in _STAGES:
                result['stage'] = _STAGES[pair]
            trace = trace.tb_next
        error = error.__cause__ if error.__cause__ is not None else error.__context__
    return result


def record_get_failure(event, error):
    """GET failures only. Logging cannot change handler response or retry behavior."""
    try:
        if (type(event) is not dict or event.get('routeKey') != 'GET /v1/access'
                or type(event.get('requestContext')) is not dict
                or type(event['requestContext'].get('http')) is not dict
                or event['requestContext']['http'].get('method') != 'GET'):
            return False
        logging.getLogger(__name__).warning('%s', json.dumps(classify(error), separators=(',', ':')))
        return True
    except Exception:
        return False
