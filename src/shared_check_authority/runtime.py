"""Explicit disabled-by-default Dev configuration shared by V1 handlers."""
import os
import time
from .core import Authority, AuthorityError, Settings


def load_authority():
    if os.environ.get('STAGE') != 'dev' or os.environ.get('AUTHORITY_ENABLED') != 'true':
        raise AuthorityError('POLICY_CONFIGURATION_UNAVAILABLE')
    try:
        import boto3
        from botocore.config import Config
        config = Config(connect_timeout=0.2, read_timeout=0.3, retries={'total_max_attempts': 1})
        from .inventory import load_keyring, verified_inventory
        active_key_id, keys = load_keyring()
        resource = boto3.resource('dynamodb',region_name='us-east-1',config=config)
        verified_inventory(resource, os.environ['AUTHORITY_TABLE_NAME'], keys)
        text = lambda name: os.environ[name]
        positive = lambda name: int(os.environ[name])
        settings = Settings(
            users_table=text('USERS_TABLE_NAME'), devices_table=text('DEVICE_BINDINGS_TABLE_NAME'),
            deletion_table=text('DELETION_LEDGER_TABLE_NAME'), authority_table=text('AUTHORITY_TABLE_NAME'),
            cognito_issuer=text('COGNITO_ISSUER'), cognito_app_client_id=text('COGNITO_APP_CLIENT_ID'),
            cognito_required_scope=text('COGNITO_REQUIRED_SCOPE'), policy_version=text('AUTHORITY_POLICY_VERSION'),
            active_key_id=active_key_id, hmac_keys=keys,
            operation_validity_seconds=positive('OPERATION_VALIDITY_SECONDS'),
            worker_settlement_seconds=positive('WORKER_SETTLEMENT_SECONDS'), reconciliation_seconds=positive('RECONCILIATION_SECONDS'),
            receipt_retention_seconds=positive('RECEIPT_RETENTION_SECONDS'), counter_retention_seconds=positive('COUNTER_RETENTION_SECONDS'),
            attempt_window_seconds=positive('ATTEMPT_WINDOW_SECONDS'), attempts_per_window=positive('ATTEMPTS_PER_WINDOW'),
            max_inflight=positive('MAX_INFLIGHT'), enabled=True)
        settings.validate()
        if settings.receipt_retention_seconds != 7*86400 or settings.counter_retention_seconds != 7*86400:
            raise AuthorityError('POLICY_CONFIGURATION_UNAVAILABLE')
        return Authority(settings,resource,now=lambda:int(time.time()))
    except AuthorityError: raise
    except Exception: raise AuthorityError('POLICY_CONFIGURATION_UNAVAILABLE') from None
