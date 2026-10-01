"""New wire uses actual authority transactions; original usage survives display ageing."""
import importlib.util,json,sys
from pathlib import Path
from copy import deepcopy
import pytest
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests/shared_check_authority')]
from test_transactions import world,ACCOUNT
from shared_check_authority.core import AuthorityError
from message_consumer.service import Consumer
from message_evaluator.policy_v2 import result
from shared_message_contract import validation_v3 as v3
spec=importlib.util.spec_from_file_location('message_integration_helpers',Path(__file__).with_name('test_integration.py'));helpers=importlib.util.module_from_spec(spec);spec.loader.exec_module(helpers)

def request():return helpers.request()|{'transportVersion':v3.VERSION}

def summary(check):
    value=result(check,evidence=[{'source':'google_web_risk_lookup','outcome':'match','targetScope':'observed_http_chain'}],ai_status='warning',ai_reasons=['AI_PRETEXT'])
    value['schemaVersion']=3
    value['evidence'][0].update(observedAt='2027-01-15T08:00:00Z',validUntil='2027-01-15T08:00:20Z',threatTypes=['MALWARE'],freshness='current')
    return v3.validate_summary(value)

def test_new_message_real_settle_expired_replay_preserves_charge_ai_and_export(world):
    a,e,_,row,_,clock=world;calls=[]
    def provider(body):calls.append(body);return summary(body['checkId'])
    c=Consumer(a,provider,lambda _:None,helpers.Budget(),allow_ai=True)
    req=request();status,p=c.handle(helpers.event(e,'POST /v1/message-checks/prepare',req));assert status==200,p
    proof=p['operationProof'];status,b=c.handle(helpers.event(e,'POST /v1/message-checks',req|{'operationProof':proof}));assert status==200,b
    assert b['accounting']['chargedChecks']==0
    original=deepcopy(row('CHECK#'+proof));clock[0]+=20
    status,aged=c.handle(helpers.event(e,'POST /v1/message-checks/reconcile',{'transportVersion':v3.VERSION,'checkId':req['checkId'],'operationProof':proof}))
    assert status==200 and aged['outcome']['verdict']=='suspicious'
    assert aged['outcome']['assessmentBasis']==['ai_assessment'] and aged['outcome']['processingOutcome']=='partial'
    assert aged['accounting']==b['accounting'] and row('CHECK#'+proof)==original and len(calls)==1
    from account_export_api.projection import receipt
    exported=receipt(original,clock[0]);assert exported['resultSummary']['schemaVersion']==3
    assert exported['resultSummary']['evidence'][0]['freshness']=='current' and exported['chargedChecks']==0
    wrong=c.handle(helpers.event(e,'POST /v1/message-checks/reconcile',{'transportVersion':'1.0.0-message-candidate.2','checkId':req['checkId'],'operationProof':proof}))
    assert wrong[0]==409

@pytest.mark.parametrize('legacy',['1.0.0-message-candidate.1','1.0.0-message-candidate.2'])
def test_legacy_replay_never_returns_unverified_current_google(world,legacy):
    from shared_check_authority.core import TrustedWorkerContext
    from message_evaluator.policy import result as old_result
    a,e,_,row,_,_=world;req=request();intent={k:req[k] for k in ('entryPoint','language','target')}
    payload=intent|{'messageTransportVersion':legacy} if legacy.endswith('.2') else intent
    proof=a.prepare(e,payload,req['checkId'],client_check_id=req['checkId']);admitted=a.admit(e,payload,proof,client_check_id=req['checkId'])
    value=result(req['checkId'],evidence=[{'source':'google_web_risk_lookup','outcome':'match','targetScope':'observed_http_chain'}]) if legacy.endswith('.2') else old_result(req['checkId'],evidence=[{'source':'google_web_risk_lookup','outcome':'match','targetScope':'observed_http_chain'}])
    a.settle(TrustedWorkerContext(ACCOUNT),proof,admitted['executionToken'],value['processingOutcome'],result_summary=value)
    original=deepcopy(row('CHECK#'+proof));c=Consumer(a,lambda _:pytest.fail('provider replay'),lambda _:None,helpers.Budget(),allow_ai=True)
    status,b=c.handle(helpers.event(e,'POST /v1/message-checks/reconcile',{'transportVersion':legacy,'checkId':req['checkId'],'operationProof':proof}))
    assert status==200 and b['outcome']['verdict']=='unknown' and b['outcome']['evidence']==[] and b['accounting']['chargedChecks']==0
    assert row('CHECK#'+proof)==original
