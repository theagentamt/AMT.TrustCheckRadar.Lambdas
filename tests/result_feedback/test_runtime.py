"""Disabled bootstrap, minimal settings, and fixed metadata logging boundaries."""
import sys,json
from pathlib import Path
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
from result_feedback import app,runtime
from result_feedback.validation import POLICY,APPROVAL_SHA
from shared_check_authority.core import AuthorityError


def enabled(monkeypatch):
    for k,v in {'STAGE':'dev','RESULT_FEEDBACK_ENABLED':'true','RESULT_FEEDBACK_POLICY_VERSION':POLICY,'RESULT_FEEDBACK_POLICY_APPROVAL_SHA256':APPROVAL_SHA}.items():monkeypatch.setenv(k,v)

@pytest.mark.parametrize('change',[{'RESULT_FEEDBACK_ENABLED':'false'},{'RESULT_FEEDBACK_POLICY_APPROVAL_SHA256':'wrong'},{'STAGE':'prod'}])
def test_disabled_or_mismatched_policy_never_initializes_authority(monkeypatch,change):
    enabled(monkeypatch)
    for k,v in change.items():monkeypatch.setenv(k,v)
    monkeypatch.setattr(runtime,'load_authority',lambda:pytest.fail('AWS path touched'))
    result=app.lambda_handler({'private':'must not echo'},None)
    assert result['statusCode']==503 and json.loads(result['body'])['reasonCode']=='SERVICE_NOT_ENABLED'


def test_runtime_loads_only_minimal_feedback_config(monkeypatch):
    import boto3
    from shared_check_authority import inventory
    enabled(monkeypatch)
    env={'USERS_TABLE_NAME':'users','DEVICE_BINDINGS_TABLE_NAME':'devices','DELETION_LEDGER_TABLE_NAME':'deletion','AUTHORITY_TABLE_NAME':'authority','COGNITO_ISSUER':'issuer','COGNITO_APP_CLIENT_ID':'client','COGNITO_REQUIRED_SCOPE':'scope','ATTEMPT_WINDOW_SECONDS':'60','ATTEMPTS_PER_WINDOW':'10'}
    for k,v in env.items():monkeypatch.setenv(k,v)
    for k in ('AUTHORITY_ENABLED','AUTHORITY_POLICY_VERSION','RECEIPT_RETENTION_SECONDS','ALLOWANCE_LIMIT','PROVIDER_SECRET_ARN'):monkeypatch.delenv(k,raising=False)
    resource=SimpleNamespace(meta=SimpleNamespace(client=SimpleNamespace(meta=SimpleNamespace(config=SimpleNamespace(retries={'total_max_attempts':1})))))
    # Other legacy suites replace botocore.exceptions at import time. Keep this
    # bootstrap unit test isolated; real SDK behavior has separate Moto coverage.
    config_calls=[]
    def sdk_config(**kwargs):
        config_calls.append(kwargs)
        return SimpleNamespace(**kwargs)
    monkeypatch.setitem(sys.modules,'botocore.config',SimpleNamespace(Config=sdk_config))
    monkeypatch.setattr(boto3,'resource',lambda *a,**k:resource)
    monkeypatch.setattr(inventory,'load_keyring',lambda:('k1',{'k1':b'x'*32}))
    monkeypatch.setattr(inventory,'verified_inventory',lambda *a:None)
    authority=runtime.load_authority();assert authority.s.counter_retention_seconds==604800
    assert config_calls == [{'connect_timeout':.2,'read_timeout':.3,'retries':{'total_max_attempts':1}}]
    assert not hasattr(authority.s,'policy_version') and not hasattr(authority.s,'receipt_retention_seconds')


def test_handler_logs_only_closed_metadata_and_redacts_private_exception(monkeypatch,capsys):
    from shared_check_authority import engineering
    from result_feedback.service import Feedback
    from result_feedback.validation import response
    enabled(monkeypatch);monkeypatch.setattr(engineering,'require_engineering_subject',lambda e:'synthetic')
    monkeypatch.setattr(runtime,'load_authority',lambda:None)
    monkeypatch.setattr(Feedback,'handle',lambda self,event:(200,response('accepted',feedback_id='private-id',check_id='private-check',received=1,expires=2)))
    app.lambda_handler({'body':'raw-private-input'},None)
    log=capsys.readouterr().out;assert 'private' not in log and json.loads(log)['status']=='accepted'
    monkeypatch.setattr(runtime,'load_authority',lambda:(_ for _ in ()).throw(RuntimeError('private credential exception')))
    reply=app.lambda_handler({},None);assert 'private' not in json.dumps(reply) and capsys.readouterr().out==''
