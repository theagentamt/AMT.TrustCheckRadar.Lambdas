"""Actual pre-AWS engineering allowlist validation; no runtime activation."""
import os
import sys
from pathlib import Path
import json
import time
from copy import deepcopy
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':
    pytest.skip('Isolated environment required',allow_module_level=True)
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from shared_check_authority.engineering import require_engineering_subject
from shared_check_authority.core import AuthorityError
from v1_entitlements import app
SUB='31902877-bcce-4d05-a18b-8e3f58410101'

@pytest.fixture
def gate(monkeypatch):
    for k,v in {'STAGE':'dev','DEV_SUBJECT_ALLOWLIST_JSON':json.dumps([SUB]),'COGNITO_ISSUER':'https://cognito-idp.us-east-1.amazonaws.com/synthetic','COGNITO_APP_CLIENT_ID':'syntheticclient','COGNITO_REQUIRED_SCOPE':'aws.cognito.signin.user.admin'}.items():monkeypatch.setenv(k,v)
    return {'requestContext':{'authorizer':{'jwt':{'claims':{'sub':SUB,'iss':os.environ['COGNITO_ISSUER'],'client_id':'syntheticclient','token_use':'access','scope':'aws.cognito.signin.user.admin','exp':str(int(time.time())+300)}}}}}


def test_exact_verified_subject_allowed(gate):
    assert require_engineering_subject(gate)==SUB


@pytest.mark.parametrize('value',['','[]','null','{}','["*"]','["synthetic-account"]',json.dumps([SUB,SUB]),json.dumps([SUB.upper()]),'x'*513])
def test_missing_invalid_or_wildcard_allowlist_never_falls_back(gate,monkeypatch,value):
    monkeypatch.setenv('DEV_SUBJECT_ALLOWLIST_JSON',value)
    with pytest.raises(AuthorityError,match='^ENGINEERING_ACCESS_UNAVAILABLE$'):require_engineering_subject(gate)


@pytest.mark.parametrize('field,value',[('sub','0bc48d13-2715-46a4-8278-72d977dd9900'),('exp','1'),('iss','https://attacker.invalid'),('client_id','other'),('token_use','id'),('scope','other')])
def test_invalid_claims_or_unlisted_subject_rejected(gate,field,value):
    gate['requestContext']['authorizer']['jwt']['claims'][field]=value
    with pytest.raises(AuthorityError,match='^ENGINEERING_ACCESS_UNAVAILABLE$'):require_engineering_subject(gate)


def test_request_body_header_identity_never_authorizes(gate):
    incoming={'body':json.dumps({'sub':SUB}),'headers':{'x-account-id':SUB}}
    with pytest.raises(AuthorityError):require_engineering_subject(incoming)


def test_access_trial_gate_before_writer_or_secret(gate,monkeypatch):
    monkeypatch.setenv('V1_ENTITLEMENTS_ENABLED','true')
    monkeypatch.setenv('DEV_SUBJECT_ALLOWLIST_JSON','[]')
    monkeypatch.setattr(app,'_writer',lambda:pytest.fail('AWS authority work before allowlist'))
    for route in ('GET /v1/access','POST /v1/access/trial'):
        response=app.lambda_handler(gate|{'routeKey':route},None)
        assert response['statusCode']==503 and json.loads(response['body'])['error']['code']=='ACCESS_SERVICE_UNAVAILABLE'


def test_consumer_gate_precedes_authority_and_preserves_unknown_accounting(gate,monkeypatch):
    import importlib.util
    import url_consumer.service as service
    monkeypatch.setitem(sys.modules,'service',service)
    path=Path(__file__).resolve().parents[2]/'src/url_consumer/app.py'
    spec=importlib.util.spec_from_file_location('isolated_consumer_gate',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    import shared_check_authority.runtime as runtime
    monkeypatch.setattr(runtime,'load_authority',lambda:pytest.fail('AWS authority work before allowlist'))
    monkeypatch.setenv('CONSUMER_ENABLED','true');monkeypatch.setenv('DEV_SUBJECT_ALLOWLIST_JSON','[]')
    response=module.lambda_handler(gate,None)
    assert response['statusCode']==503
    assert json.loads(response['body'])['accounting']['chargedChecks'] is None


def test_not_dev_never_authorized(gate,monkeypatch):
    monkeypatch.setenv('STAGE','prod')
    with pytest.raises(AuthorityError):require_engineering_subject(gate)
