import os,sys,json
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))

@pytest.mark.parametrize('module,result',[('play_lifecycle_ingress.app',{'statusCode':503,'body':''}),('play_lifecycle_worker.app',{'enabled':False}),('play_token_deletion.app',{'enabled':False})])
def test_default_handlers_deny_before_network(monkeypatch,module,result):
 import importlib
 monkeypatch.setenv('STAGE','dev');monkeypatch.delenv('PLAY_LIFECYCLE_ENABLED',raising=False);monkeypatch.delenv('PLAY_TOKEN_CLEANUP_ENABLED',raising=False)
 assert importlib.import_module(module).lambda_handler({},None)==result

def test_worker_checkpoint_policy_gate_blocks_even_when_cleanup_enabled(monkeypatch):
 from play_lifecycle_worker.app import lambda_handler
 monkeypatch.setenv('STAGE','dev');monkeypatch.setenv('PLAY_TOKEN_CLEANUP_ENABLED','true');monkeypatch.setenv('PLAY_CHECKPOINT_POLICY_APPROVED','false')
 assert lambda_handler({'schemaVersion':1,'operation':'reconcile-play-lifecycle'},None)=={'enabled':False}

def test_preparation_enabled_composition_and_exact_response(monkeypatch):
 from shared_play_lifecycle import preparation
 from shared_check_authority import engineering,runtime,entitlements
 from shared_play_lifecycle import bindings
 monkeypatch.setenv('STAGE','dev');monkeypatch.setenv('PLAY_PREPARATION_ENABLED','true');monkeypatch.setenv('PLAY_TOKEN_TABLE_NAME','tokens');monkeypatch.setenv('AUTHORITY_TABLE_NAME','authority')
 monkeypatch.setattr(engineering,'require_engineering_subject',lambda e:'account')
 monkeypatch.setattr(runtime,'load_authority',lambda:object())
 seen=[]
 class Writer:
  def __init__(self,authority,*,approved_products,operator_principals,verification_max_age_seconds):seen.append(verification_max_age_seconds)
 class Bindings:
  def __init__(self,writer,table):assert table=='tokens'
  def prepare(self,event):return {'schemaVersion':1,'contractVersion':preparation.CONTRACT,'accountBinding':'a'*64,'prepared':True}
 monkeypatch.setattr(entitlements,'EntitlementWriter',Writer);monkeypatch.setattr(bindings,'Bindings',Bindings)
 event={'version':'2.0','routeKey':'POST /v1/purchases/google-play/prepare','requestContext':{'http':{'method':'POST'}},'body':'{"schemaVersion":1}'}
 response=preparation.handle(event)
 assert response['statusCode']==200 and json.loads(response['body'])['prepared'] is True and seen==[20]
 event['body']='{"schemaVersion":true}';assert preparation.handle(event)['statusCode']==400

def test_ingress_logs_only_fixed_counters_and_test_delivery_avoids_provider(monkeypatch,capsys):
 from play_lifecycle_ingress import app
 from types import SimpleNamespace
 monkeypatch.setenv('STAGE','dev');monkeypatch.setenv('PLAY_LIFECYCLE_ENABLED','true')
 for name in ('PLAY_PUBSUB_AUDIENCE','PLAY_PUBSUB_SERVICE_ACCOUNT_EMAIL','PLAY_PUBSUB_SERVICE_ACCOUNT_SUBJECT','PLAY_PUBSUB_SUBSCRIPTION'):monkeypatch.setenv(name,'synthetic-config')
 monkeypatch.setattr(app,'parse_delivery',lambda *a,**k:SimpleNamespace(kind='test'))
 monkeypatch.setattr(app.runtime,'load',lambda:pytest.fail('provider must not load'))
 response=app.lambda_handler({'version':'2.0','requestContext':{'http':{'method':'POST'}},'body':'private body','headers':{'authorization':'private bearer'}},None)
 assert response['statusCode']==204
 line=json.loads(capsys.readouterr().out)
 assert line==dict(event='play_lifecycle_ingress',heartbeat=1,accepted=0,testNotification=1,rejected=0,failed=0,unresolved=0)


def test_new_contract_fixtures_and_immutable_predecessor_versions():
 import jsonschema
 root=Path(__file__).resolve().parents[2]/'contracts'
 p=root/'play-preparation/1.0.0-candidate.1';f=json.loads((p/'fixtures.json').read_text())
 for kind in ('request','response'):jsonschema.validate(f[kind],json.loads((p/(kind+'.schema.json')).read_text()))
 p=root/'account-export/1.0.0-candidate.3';f=json.loads((p/'fixtures.json').read_text());schema=json.loads((p/'response.schema.json').read_text())
 for name in ('firstPage','emptyContinuationPage','finalPage'):jsonschema.validate(f[name],schema)
 assert f['finalPage']['family']=='play_verification' and 'purchase_credentials' in f['finalPage']['scope']['excluded']
