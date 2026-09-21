"""Default-disabled Dev wiring. Export roles need read-only access only."""
from dataclasses import dataclass, field
import base64
import json
import os
import re
import time
from .cursor import Cursor, ExportError, require, unique_pairs
from .reader import Reader
from .service import Export
from shared_check_authority.core import Authority

POLICY = 'account-export-observed-v1'

@dataclass(frozen=True)
class Settings:
    users_table: str
    devices_table: str
    deletion_table: str
    authority_table: str
    cognito_issuer: str
    cognito_app_client_id: str
    cognito_required_scope: str
    active_key_id: str
    hmac_keys: dict = field(repr=False)

    def validate(self):
        require(all(type(v) is str and v for v in (self.users_table,self.devices_table,
                self.deletion_table,self.authority_table,self.cognito_issuer,self.cognito_app_client_id))
                and self.cognito_required_scope == 'aws.cognito.signin.user.admin'
                and self.active_key_id in self.hmac_keys, 'SERVICE_UNAVAILABLE', 503)


def load():
    require(os.environ.get('STAGE') == 'dev' and os.environ.get('ACCOUNT_EXPORT_ENABLED') == 'true', 'SERVICE_NOT_ENABLED', 503)
    require(os.environ.get('ACCOUNT_EXPORT_POLICY_VERSION') == POLICY
            and os.environ.get('ACCOUNT_EXPORT_INVENTORY_STATUS') == 'verified_complete'
            and os.environ.get('COGNITO_USERNAME_IS_SUB') == 'true', 'SERVICE_UNAVAILABLE', 503)
    try:
        import boto3
        from botocore.config import Config
        from shared_check_authority.inventory import load_keyring
        config = Config(connect_timeout=.3,read_timeout=.5,retries={'total_max_attempts':1})
        resource = boto3.resource('dynamodb',region_name='us-east-1',config=config)
        active, keys = load_keyring()
        settings = Settings(*(os.environ[name] for name in ('USERS_TABLE_NAME','DEVICE_BINDINGS_TABLE_NAME',
             'DELETION_LEDGER_TABLE_NAME','AUTHORITY_TABLE_NAME','COGNITO_ISSUER','COGNITO_APP_CLIENT_ID','COGNITO_REQUIRED_SCOPE')), active, keys)
        authority = Authority(settings,resource,now=lambda:int(time.time()))
        tables = {name:os.environ[env] for name,env in {
            'users':'USERS_TABLE_NAME','devices':'DEVICE_BINDINGS_TABLE_NAME','deletion':'DELETION_LEDGER_TABLE_NAME',
            'authority':'AUTHORITY_TABLE_NAME','entitlements':'ENTITLEMENTS_TABLE_NAME','recovery':'DEVICE_RECOVERY_CONTROL_TABLE_NAME',
            'history_content':'HISTORY_CONTENT_TABLE_NAME','history_control':'HISTORY_CONTROL_TABLE_NAME',
            'abuse':'ANALYSIS_ABUSE_TABLE_NAME','outbox':'CAMPAIGN_OUTBOX_TABLE_NAME','pipeline':'CAMPAIGN_PIPELINE_TABLE_NAME'}.items()}
        arn = os.environ['ACCOUNT_EXPORT_CURSOR_SECRET_ARN']
        require(re.fullmatch(r'arn:aws:secretsmanager:us-east-1:107827791950:secret:trustcheckradar/dev/account-export-cursor-[A-Za-z0-9]{6}',arn) is not None, 'SERVICE_UNAVAILABLE',503)
        secrets = boto3.client('secretsmanager',region_name='us-east-1',config=config)
        try:
            raw = secrets.get_secret_value(SecretId=arn,VersionStage='AWSCURRENT')['SecretString']
        finally:
            secrets.close()
        require(type(raw) is str and len(raw) <= 4096,'SERVICE_UNAVAILABLE',503)
        ring = json.loads(raw,object_pairs_hook=unique_pairs)
        require(type(ring) is dict and set(ring) == {'activeKeyId','keys'} and type(ring['keys']) is dict,'SERVICE_UNAVAILABLE',503)
        cursor = Cursor('dev',ring['activeKeyId'],{k:base64.b64decode(v,validate=True) for k,v in ring['keys'].items()})
        cognito = boto3.client('cognito-idp',region_name='us-east-1',config=config)
        from shared_purchase_ownership import OwnershipStore
        store = OwnershipStore(table=resource.Table(tables['entitlements']),
            ledger=resource.Table(tables['deletion']),users_table_name=tables['users'],
            table_name=tables['entitlements'],ledger_table_name=tables['deletion'],
            client=boto3.client('dynamodb',region_name='us-east-1',config=config),
            environment='dev',now=authority.now)
        reader = Reader(authority,tables,cognito,os.environ['COGNITO_USER_POOL_ID'],
                        purchase_reader=store,
                        kms=boto3.client('kms',region_name='us-east-1',config=config))
        return Export(reader,cursor)
    except ExportError:
        raise
    except Exception:
        raise ExportError('SERVICE_UNAVAILABLE',503) from None
