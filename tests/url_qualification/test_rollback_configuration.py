"""Real keyring/inventory/Settings/Authority loading with read-only fake SDKs."""
import base64
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import boto3
from botocore.config import Config

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from shared_check_authority.core import Authority, OWNER_POLICY
from shared_check_authority.runtime import load_authority
from shared_check_authority.inventory import INVENTORY_KEY


def test_real_authority_loader_accepts_rollback_with_mode_off_and_authority_on(monkeypatch):
    settings={'STAGE':'dev','AUTHORITY_ENABLED':'true',
        'URL_RECONCILIATION_ENABLED':'true','URL_QUALIFICATION_ENABLED':'false',
        'URL_NEW_ADMISSION_ENABLED':'false','URL_PROVIDER_ENABLED':'false','URL_RETENTION_APPROVED':'false',
        'CONSUMER_ENABLED':'false','USERS_TABLE_NAME':'synthetic-users','DEVICE_BINDINGS_TABLE_NAME':'synthetic-devices',
        'DELETION_LEDGER_TABLE_NAME':'synthetic-deletion','AUTHORITY_TABLE_NAME':'synthetic-authority',
        'COGNITO_ISSUER':'https://cognito-idp.us-east-1.amazonaws.com/synthetic',
        'COGNITO_APP_CLIENT_ID':'syntheticclient','COGNITO_REQUIRED_SCOPE':'aws.cognito.signin.user.admin',
        'AUTHORITY_POLICY_VERSION':OWNER_POLICY,'OPERATION_VALIDITY_SECONDS':'60','WORKER_SETTLEMENT_SECONDS':'120',
        'RECONCILIATION_SECONDS':'300','RECEIPT_RETENTION_SECONDS':'604800','COUNTER_RETENTION_SECONDS':'604800',
        'ATTEMPT_WINDOW_SECONDS':'60','ATTEMPTS_PER_WINDOW':'100','MAX_INFLIGHT':'3',
        'GOVERNED_HISTORY_SETTLEMENT_ENABLED':'false',
        'AUTHORITY_HMAC_SECRET_ARN':'arn:aws:secretsmanager:us-east-1:107827791950:secret:trustcheckradar/dev/v1-authority-hmac-ABC123'}
    # Fixed synthetic ARN shape only; SDK factories below are local fakes.
    for key,value in settings.items():monkeypatch.setenv(key,value)
    key=b'synthetic-local-qualification-key-0000'
    secret_client=Mock()
    secret_client.get_secret_value.return_value={'SecretString':json.dumps({'activeKeyId':'k1','keys':{'k1':base64.b64encode(key).decode()}})}
    secret_factory=Mock(return_value=secret_client)
    monkeypatch.setattr(boto3,'client',secret_factory)
    table=Mock()
    table.get_item.return_value={'Item':INVENTORY_KEY|{'recordType':'V1_HMAC_KEY_INVENTORY','schemaVersion':1,
        'revision':1,'coverage':'VERIFIED_COMPLETE','issuedKeys':{'k1':hashlib.sha256(key).hexdigest()}}}
    resource=Mock()
    resource.meta.client.meta.config=Config(retries={'total_max_attempts':1})
    resource.Table.return_value=table
    resource_factory=Mock(return_value=resource)
    monkeypatch.setattr(boto3,'resource',resource_factory)
    authority=load_authority()
    assert isinstance(authority,Authority) and authority.s.enabled is True
    authority.s.validate()
    assert authority.s.receipt_retention_seconds==604800 and authority.s.hmac_keys=={'k1':key}
    assert authority.s.governed_history_settlement_enabled is False
    resource.Table.assert_called_once_with('synthetic-authority')
    table.get_item.assert_called_once_with(Key=INVENTORY_KEY,ConsistentRead=True)
    secret_factory.assert_called_once()
    assert secret_factory.call_args.args==('secretsmanager',)
    secret_client.close.assert_called_once()
    # Real loading performs only the synthetic key read and inventory GetItem.
    for operation in ('put_item','update_item','delete_item'):
        getattr(table,operation).assert_not_called()
    resource.meta.client.transact_write_items.assert_not_called()
