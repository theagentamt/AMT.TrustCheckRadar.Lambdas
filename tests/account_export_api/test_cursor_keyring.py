"""Operator setup validation is pure and never activates/export reads user data."""
import base64
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from account_export_api.runtime import parse_cursor_keyring
from account_export_api.cursor import Cursor,ExportError

KEY=base64.b64encode(b'a'*32).decode()


def raw(keys=None,active='k1'):
    return json.dumps({'activeKeyId':active,'keys':keys if keys is not None else {'k1':KEY}})


def test_setup_parser_roundtrip_and_rotation_without_aws():
    active,keys=parse_cursor_keyring(raw())
    old=Cursor('dev',active,keys).seal({'synthetic':'fixture'})
    active,keys=parse_cursor_keyring(raw({'k1':KEY,'k2':base64.b64encode(b'b'*32).decode()},'k2'))
    current=Cursor('dev',active,keys)
    assert current.open(old)=={'synthetic':'fixture'}
    assert current.seal({'synthetic':'fixture'}).startswith('k2.')
    with pytest.raises(ExportError,match='INVALID_CURSOR'):
        Cursor('dev','k2',{'k2':keys['k2']}).open(old)


@pytest.mark.parametrize('value',[
    None, b'{}', 'not-json', '[]', '{}', 'null', 'x'*4097,
    '{"activeKeyId":"k1","activeKeyId":"k1","keys":{"k1":"'+KEY+'"}}',
    '{"activeKeyId":"k1","keys":{"k1":"'+KEY+'","k1":"'+KEY+'"}}',
    '{"activeKeyId":"k1","keys":{"k1":NaN}}',
    json.dumps({'activeKeyId':'k1','keys':{'k1':KEY},'extra':'private-secret'}),
    raw({},'k1'), raw({'k1':KEY},'absent'), raw({'bad-key':KEY},'bad-key'),
    raw({'abcdefgh9':KEY},'abcdefgh9'), raw({'k1':KEY},True),
    raw({'k1':True}), raw({'k1':KEY+'\n'}), raw({'k1':KEY.rstrip('=')}),
    raw({'k1':base64.b64encode(b'a'*31).decode()}),
    raw({'k1':base64.b64encode(b'a'*33).decode()}),
    raw({f'k{i}':KEY for i in range(5)},'k0'),
])
def test_malformed_secret_is_fixed_server_error_without_secret_detail(value):
    with pytest.raises(ExportError) as caught:
        parse_cursor_keyring(value)
    assert caught.value.status==503 and caught.value.code=='SERVICE_UNAVAILABLE'
    assert str(caught.value)=='SERVICE_UNAVAILABLE'


def test_runtime_duplicate_secret_fields_are_server_unavailable_and_client_closed(monkeypatch):
    from types import SimpleNamespace
    import boto3
    from account_export_api import runtime
    from shared_check_authority import inventory
    env={'STAGE':'dev','ACCOUNT_EXPORT_ENABLED':'true',
         'ACCOUNT_EXPORT_POLICY_VERSION':runtime.POLICY,'ACCOUNT_EXPORT_INVENTORY_STATUS':'verified_complete',
         'COGNITO_USERNAME_IS_SUB':'true','COGNITO_ISSUER':'https://synthetic.example',
         'COGNITO_APP_CLIENT_ID':'synthetic-client','COGNITO_REQUIRED_SCOPE':'aws.cognito.signin.user.admin',
         'ACCOUNT_EXPORT_CURSOR_SECRET_ARN':'arn:aws:secretsmanager:us-east-1:107827791950:secret:trustcheckradar/dev/account-export-cursor-ABC123'}
    for key in ('USERS_TABLE_NAME','DEVICE_BINDINGS_TABLE_NAME','DELETION_LEDGER_TABLE_NAME',
                'AUTHORITY_TABLE_NAME','ENTITLEMENTS_TABLE_NAME','DEVICE_RECOVERY_CONTROL_TABLE_NAME',
                'HISTORY_CONTENT_TABLE_NAME','HISTORY_CONTROL_TABLE_NAME','ANALYSIS_ABUSE_TABLE_NAME',
                'CAMPAIGN_OUTBOX_TABLE_NAME','CAMPAIGN_PIPELINE_TABLE_NAME'):
        env[key]='synthetic-'+key.lower()
    for key,value in env.items():monkeypatch.setenv(key,value)
    calls=[]
    class Secret:
        def get_secret_value(self,**kwargs):
            calls.append(kwargs)
            return {'SecretString':'{"activeKeyId":"k1","activeKeyId":"k1","keys":{"k1":"'+KEY+'"}}'}
        def close(self):calls.append('closed')
    def client(name,**kwargs):
        assert name=='secretsmanager'  # Failure must precede identity/reader setup.
        return Secret()
    monkeypatch.setattr(boto3,'client',client)
    sdk_client=SimpleNamespace(meta=SimpleNamespace(config=SimpleNamespace(retries={'total_max_attempts':1})))
    monkeypatch.setattr(boto3,'resource',lambda *a,**kw:SimpleNamespace(meta=SimpleNamespace(client=sdk_client)))
    monkeypatch.setattr(inventory,'load_keyring',lambda:('h1',{'h1':b'h'*32}))
    with pytest.raises(ExportError) as caught:runtime.load()
    assert caught.value.status==503 and caught.value.code=='SERVICE_UNAVAILABLE'
    assert calls==[{'SecretId':env['ACCOUNT_EXPORT_CURSOR_SECRET_ARN'],'VersionStage':'AWSCURRENT'},'closed']
