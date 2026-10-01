import json,sys
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
from v1_play_handoff.app import ERRORS,lambda_handler
from v1_play_handoff.service import request_payload
from shared_play_verification.proof import PlayVerificationError

CONTRACT=ROOT/'contracts/v1-play-handoff/candidate.1'

def test_published_fixtures_and_fixed_errors():
 f=json.loads((CONTRACT/'fixtures.json').read_text())
 for kind,items in [('request',[f['request']]),('response',f['responses']),('error',f['errors'])]:
  validator=Draft202012Validator(json.loads((CONTRACT/(kind+'.schema.json')).read_text()))
  for item in items:validator.validate(item)
 for code,(status,retryable) in ERRORS.items():
  assert status in (400,401,403,409,503)
  value={'schemaVersion':1,'contractVersion':'v1-play-handoff-1.0.0-candidate.1','requestId':f['request']['requestId'],'error':{'code':code,'retryable':retryable}}
  Draft202012Validator(json.loads((CONTRACT/'error.schema.json').read_text())).validate(value)

def test_closed_handler_before_runtime_or_provider(monkeypatch):
 monkeypatch.setenv('STAGE','dev');monkeypatch.setenv('PLAY_HANDOFF_ENABLED','false')
 monkeypatch.setattr('v1_play_handoff.app._runtime',lambda:pytest.fail('closed gate called runtime'))
 r=lambda_handler({},None);b=json.loads(r['body'])
 assert r['statusCode']==503 and b['requestId'] is None and b['error']=={'code':'PURCHASE_SERVICE_UNAVAILABLE','retryable':False}

@pytest.mark.parametrize('field,value',[('schemaVersion',True),('requestId','notuuid'),('intent','restore'),('purchaseToken','short')])
def test_strict_request(field,value):
 p=json.loads((CONTRACT/'fixtures.json').read_text())['request'];p[field]=value
 with pytest.raises(PlayVerificationError):request_payload(p)

def test_request_refuses_client_grant_claims():
 p=json.loads((CONTRACT/'fixtures.json').read_text())['request']
 for field in ('accountId','periodStart','productId','packageName','orderId','isPaid','acknowledged'):
  with pytest.raises(PlayVerificationError):request_payload({**p,field:'untrusted'})
