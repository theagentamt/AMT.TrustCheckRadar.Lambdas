"""Real in-memory Moto transactions, profile binding, settlement and History."""
import copy
from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests/shared_check_authority')]
from test_transactions import world
from message_consumer.service import Consumer
from message_evaluator.policy_v3 import evaluate
from shared_message_contract import validation_v3 as v3
from shared_governed_history import RETENTION_SECONDS,present_projection,validate_public_projection
spec=importlib.util.spec_from_file_location('primary_integration_helpers',Path(__file__).with_name('test_integration.py'))
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)


def request(text='Send me your account password and the login code.'):
    return h.request(text=text)|{'transportVersion':v3.VERSION}


def ai_output(kind):
    if kind=='timeout':raise TimeoutError()
    if kind=='malformed':return {}
    if kind=='abstain':return {'assessment':'abstain','context':'insufficient','reasons':[]}
    return {'assessment':kind,'context':'clear','reasons':[{'code':'AI_PAYMENT_PRESSURE','spans':[{'start':0,'end':8}]}] if kind=='warning' else []}


@pytest.mark.parametrize('kind,charge',[('warning',1),('no_warning',1),('timeout',0),('malformed',0),('abstain',0)])
def test_single_evaluation_settlement_and_history_with_real_authority(world,kind,charge):
    a,event,_,row,_,clock=world
    a.s=replace(a.s,receipt_retention_seconds=RETENTION_SECONDS,counter_retention_seconds=RETENTION_SECONDS,
                governed_history_settlement_enabled=True)
    calls=[]
    def provider(body):
        calls.append(body)
        assert body['runtimeMode']=='ai_primary_v1'
        return evaluate(body['checkId'],body['intent'],ai=lambda *_:ai_output(kind),ai_primary=True)
    c=Consumer(a,provider,lambda _:None,h.Budget(),allow_ai=True,allow_ai_primary=True)
    req=request()
    status,p=c.handle(h.event(event,'POST /v1/message-checks/prepare',req));assert status==200
    proof=p['operationProof'];submit=req|{'operationProof':proof}
    status,result=c.handle(h.event(event,'POST /v1/message-checks',submit))
    assert status==200 and result['state']=='settled' and result['accounting']['chargedChecks']==charge
    stored=copy.deepcopy(row('CHECK#'+proof))
    for route,body in [('POST /v1/message-checks',submit),('POST /v1/message-checks/reconcile',{
        'transportVersion':v3.VERSION,'checkId':req['checkId'],'operationProof':proof})]:
        replay=c.handle(h.event(event,route,body))
        assert replay[0]==200 and replay[1]['accounting']==result['accounting']
    assert len(calls)==1 and row('CHECK#'+proof)==stored
    assert row('PERIOD#p1')['usedChecks']==charge and row('PERIOD#p1')['reservedChecks']==0
    history=present_projection(stored['governedHistory'],clock[0])
    validate_public_projection(history)
    assert history['accounting']['chargedChecks']==charge
    assert history['outcome']['aiAssessmentStatus']==result['outcome']['aiAssessmentStatus']


def test_profile_change_cannot_reuse_prepared_proof(world):
    a,event,*_=world;req=request();budget=h.Budget()
    legacy=Consumer(a,lambda *_:pytest.fail('provider'),lambda _:None,budget,allow_candidate3_rules_only=True)
    status,p=legacy.handle(h.event(event,'POST /v1/message-checks/prepare',req));assert status==200
    primary=Consumer(a,lambda *_:pytest.fail('provider'),lambda _:None,budget,allow_ai=True,allow_ai_primary=True)
    status,result=primary.handle(h.event(event,'POST /v1/message-checks',req|{'operationProof':p['operationProof']}))
    assert status==409 and result['errorCode']=='CHECK_ID_CONFLICT' and budget.attempts==0


def test_comp_success_zero_and_paid_trial_records_preserved(world):
    a,event,_,row,change,_=world;change('authority','ACCESS',basis='complimentary')
    before=copy.deepcopy(row('PERIOD#p1'))
    world[2]('authority','TRIAL_HISTORY',recordType='V1_TRIAL_ELIGIBILITY',activatedAtEpoch=1)
    trial_before=copy.deepcopy(row('TRIAL_HISTORY'))
    def provider(body):return evaluate(body['checkId'],body['intent'],ai=lambda *_:ai_output('warning'),ai_primary=True)
    c=Consumer(a,provider,lambda _:None,h.Budget(),allow_ai=True,allow_ai_primary=True)
    req=request();_,p=c.handle(h.event(event,'POST /v1/message-checks/prepare',req))
    status,result=c.handle(h.event(event,'POST /v1/message-checks',req|{'operationProof':p['operationProof']}))
    assert status==200 and result['outcome']['processingOutcome']=='complete' and result['accounting']['chargedChecks']==0
    assert row('PERIOD#p1')==before and row('TRIAL_HISTORY')==trial_before


def test_wire_cannot_select_private_profile(world):
    a,event,*_=world;c=Consumer(a,lambda *_:pytest.fail('provider'),lambda _:None,h.Budget(),allow_ai=True,allow_ai_primary=True)
    status,result=c.handle(h.event(event,'POST /v1/message-checks/prepare',request()|{'messageExecutionProfile':'ai_primary_v1'}))
    assert status==422 and result['errorCode']=='INPUT_REJECTED'



@pytest.mark.parametrize('reply,charge',[('warning',1),('no_warning',1),('refused',0),('usage_absent',0),('timeout',0)])
def test_normal_http_adapters_budget_usage_reconcile_and_history(world,monkeypatch,reply,charge):
    import json
    from types import SimpleNamespace
    from message_consumer import app as http
    from message_evaluator import app as evaluator,ai_provider
    from shared_check_authority import engineering,runtime,entitlements
    from shared_message_contract.provider_cost import CostPolicy
    from shared_message_contract import POLICY,APPROVAL_SHA,validation_v2 as v2
    a,event,_,row,_,clock=world
    a.s=replace(a.s,receipt_retention_seconds=RETENTION_SECONDS,counter_retention_seconds=RETENTION_SECONDS,
                governed_history_settlement_enabled=True)
    cost=CostPolicy('fixture-model-2026-01-01',1024,512,2,4)
    env={'STAGE':'dev','MESSAGE_CONSUMER_ENABLED':'true','AUTHORITY_ENABLED':'true',
        'MESSAGE_POLICY_VERSION':POLICY,'MESSAGE_POLICY_APPROVAL_SHA256':APPROVAL_SHA,
        'MESSAGE_PROVIDER_WINDOW_SECONDS':'60','MESSAGE_PROVIDER_ATTEMPTS_PER_WINDOW':'5',
        'MESSAGE_PROVIDER_FAILURES_PER_WINDOW':'5','MESSAGE_PROVIDER_CIRCUIT_OPEN':'false',
        'MESSAGE_PROVIDER_NANO_USD_PER_WINDOW':str(cost.reservation_nano),'MESSAGE_AI_COST_POLICY':json.dumps(cost.as_dict()),
        'MESSAGE_AI_ENABLED':'true','MESSAGE_AI_QUALIFIED':'true','MESSAGE_AI_PRIMARY_ENABLED':'true',
        'MESSAGE_AI_POLICY_VERSION':v2.POLICY,'MESSAGE_AI_POLICY_APPROVAL_SHA256':v2.APPROVAL_SHA,
        'MESSAGE_EVALUATOR_ENABLED':'true','MESSAGE_CANDIDATE3_RULES_ONLY_ENABLED':'false'}
    for key,value in env.items():monkeypatch.setenv(key,value)
    monkeypatch.setattr(engineering,'require_engineering_subject',lambda _: 'synthetic')
    monkeypatch.setattr(runtime,'load_authority',lambda:a)
    refresh=[]
    monkeypatch.setattr(entitlements,'EntitlementWriter',lambda *a,**k:SimpleNamespace(refresh_for_account=lambda *a:refresh.append(1)))
    settings=SimpleNamespace(model=cost.model,max_output_tokens=512)
    monkeypatch.setattr(ai_provider,'qualified_settings',lambda policy:settings)
    calls=[]
    def fake_transport(intent,settings,ms,**kw):
        calls.append(kw['body_builder'](intent,settings))
        if reply=='timeout':raise TimeoutError()
        proposal=ai_output(reply if reply in ('warning','no_warning') else 'warning')
        proposal={**proposal,'reasons':[{'code':r['code'],'quotes':[intent['target']['sanitizedText']]}
            for r in proposal['reasons']]}
        wire={'model':cost.model,'service_tier':'default','status':'completed',
            'usage':{'input_tokens':100,'output_tokens':20,'total_tokens':120,
              'input_tokens_details':{'cached_tokens':0},'output_tokens_details':{'reasoning_tokens':0}},
            'output':[{'type':'message','role':'assistant','status':'completed',
                'content':[{'type':'output_text','text':json.dumps({'result':proposal})}]}]}
        if reply=='refused':wire['output'][0]['content']=[{'type':'refusal','refusal':'not retained'}]
        if reply=='usage_absent':wire.pop('usage')
        return kw['response_parser'](json.dumps(wire).encode(),intent['target']['sanitizedText'])
    monkeypatch.setattr(ai_provider.proposer,'propose',fake_transport)
    context=SimpleNamespace(get_remaining_time_in_millis=lambda:28000)
    monkeypatch.setattr(http,'provider',lambda body:evaluator.lambda_handler(body,context))
    def invoke(route,body):
        result=http.lambda_handler(h.event(event,route,body),context)
        return result['statusCode'],json.loads(result['body'])
    req=request();_,prepared=invoke('POST /v1/message-checks/prepare',req)
    submit=req|{'operationProof':prepared['operationProof']}
    status,result=invoke('POST /v1/message-checks',submit)
    assert status==200 and result['state']=='settled' and result['accounting']['chargedChecks']==charge
    assert len(calls)==1
    from message_consumer.budget import KEY
    reserved=a._get(a.s.authority_table,KEY)
    assert reserved['reservedNanoUsd']==cost.reservation_nano and reserved['attempts']==1
    # An exhausted currency window settles a different logical operation without another provider call.
    next_req=req|{'checkId':'other-check'}
    _,other=invoke('POST /v1/message-checks/prepare',next_req)
    _,limited=invoke('POST /v1/message-checks',next_req|{'operationProof':other['operationProof']})
    assert limited['accounting']['chargedChecks']==0 and len(calls)==1
    assert 'BUDGET_LIMIT' in limited['outcome']['limitationCodes']
    # Missing new dispatch configuration cannot hide a settled existing receipt.
    monkeypatch.delenv('MESSAGE_AI_COST_POLICY');monkeypatch.delenv('MESSAGE_PROVIDER_NANO_USD_PER_WINDOW')
    before_refresh=len(refresh)
    status,replayed=invoke('POST /v1/message-checks/reconcile',{
        'transportVersion':v3.VERSION,'checkId':req['checkId'],'operationProof':prepared['operationProof']})
    assert status==200 and replayed['accounting']==result['accounting']
    assert len(calls)==1 and len(refresh)==before_refresh
    saved=present_projection(row('CHECK#'+prepared['operationProof'])['governedHistory'],clock[0])
    validate_public_projection(saved)
    assert saved['accounting']['chargedChecks']==charge
