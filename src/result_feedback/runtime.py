"""Minimal feedback authority settings: no subscription or provider configuration."""
from dataclasses import dataclass,field
import os,re,time
from shared_check_authority.core import Authority,AuthorityError
from .validation import POLICY,APPROVAL_SHA

@dataclass(frozen=True)
class Settings:
    users_table:str
    devices_table:str
    deletion_table:str
    authority_table:str
    cognito_issuer:str
    cognito_app_client_id:str
    cognito_required_scope:str
    active_key_id:str
    hmac_keys:dict=field(repr=False)
    attempt_window_seconds:int
    attempts_per_window:int
    counter_retention_seconds:int=7*86400

    def validate(self):
        texts=(self.users_table,self.devices_table,self.deletion_table,self.authority_table,self.cognito_issuer,self.cognito_app_client_id,self.cognito_required_scope)
        if (any(type(v) is not str or not v for v in texts) or type(self.attempt_window_seconds) is not int or not 1<=self.attempt_window_seconds<=86400
            or type(self.attempts_per_window) is not int or not 1<=self.attempts_per_window<=10000 or self.counter_retention_seconds!=7*86400
            or type(self.hmac_keys) is not dict or not 1<=len(self.hmac_keys)<=4 or self.active_key_id not in self.hmac_keys
            or any(type(k) is not str or not re.fullmatch('[A-Za-z0-9]{1,8}',k) or type(v) is not bytes or len(v)<32 for k,v in self.hmac_keys.items())):
            raise AuthorityError('POLICY_CONFIGURATION_UNAVAILABLE')

def load_authority():
    if (os.environ.get('STAGE')!='dev' or os.environ.get('RESULT_FEEDBACK_ENABLED')!='true'
        or os.environ.get('RESULT_FEEDBACK_POLICY_VERSION')!=POLICY or os.environ.get('RESULT_FEEDBACK_POLICY_APPROVAL_SHA256')!=APPROVAL_SHA):
        raise AuthorityError('POLICY_CONFIGURATION_UNAVAILABLE')
    try:
        import boto3
        from botocore.config import Config
        from shared_check_authority.inventory import load_keyring,verified_inventory
        active,keys=load_keyring()
        resource=boto3.resource('dynamodb',region_name='us-east-1',config=Config(connect_timeout=.2,read_timeout=.3,retries={'total_max_attempts':1}))
        settings=Settings(*(os.environ[k] for k in ('USERS_TABLE_NAME','DEVICE_BINDINGS_TABLE_NAME','DELETION_LEDGER_TABLE_NAME','AUTHORITY_TABLE_NAME','COGNITO_ISSUER','COGNITO_APP_CLIENT_ID','COGNITO_REQUIRED_SCOPE')),active,keys,int(os.environ['ATTEMPT_WINDOW_SECONDS']),int(os.environ['ATTEMPTS_PER_WINDOW']))
        settings.validate();verified_inventory(resource,settings.authority_table,keys)
        return Authority(settings,resource,now=lambda:int(time.time()))
    except AuthorityError:raise
    except Exception:raise AuthorityError('POLICY_CONFIGURATION_UNAVAILABLE') from None
