"""Actual local three-consumer attempt wiring; not live/provider-cost evidence."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0,str(Path(__file__).parent))
from test_transactions import world, WORKER, ACCOUNT
from test_operations_callers import bounded_world, consumer, api_event, pending
from shared_check_authority.operations_ingestion import ReceiptIngestion
from shared_check_authority.operations_callers import begin_authenticated_attempt, enter_internal_attempt, complete_internal_attempt, capture_authenticated
from shared_operations.projection import validate_capture

FAMILIES=['url_check','message_check','recovery_clarification']


def attempts(w,proof):
    projected=validate_capture(w[3]('CHECK#'+proof)['operationsProjection'])
    return [e for e in projected['events']if e['eventType'].startswith('attempt_')]


def setup_flow(w,family,*,clock=lambda:0.0):
    service,base,calls,path,req=consumer(w,family);service.clock=clock
    status,prepared=service.handle(api_event(base,'POST '+path+'/prepare',req))
    assert status==200 and prepared['state']=='prepared'
    return service,base,calls,path,req,prepared['operationProof']


@pytest.mark.parametrize('family',FAMILIES)
@pytest.mark.parametrize('provider_failure',[False,True])
def test_real_start_precedes_single_dispatch_finish_and_replay_never_charges_twice(bounded_world,family,provider_failure):
    w=bounded_world;service,base,calls,path,req,proof=setup_flow(w,family)
    original=service.provider;observed=[]
    def provider(body):
        begun=attempts(w,proof)
        assert len(begun)==1 and begun[0]['eventType']=='attempt_started'
        assert w[3]('CHECK#'+proof)['state']=='ADMITTED'
        assert 'operationProof'not in body and 'operations'not in json.dumps(body)
        assert body['executionBudgetMs']==18000
        observed.append(1)
        if provider_failure:raise TimeoutError('PRIVATE_INVOKE_EXCEPTION')
        return original(body)
    service.provider=provider
    status,response=service.handle(api_event(base,'POST '+path,req|{'operationProof':proof}))
    assert status==200 and response['state']=='settled' and observed==[1]
    recorded=attempts(w,proof);assert len(recorded)==2
    assert recorded[1]['attemptOutcome']==('failed'if provider_failure else'succeeded')
    assert recorded[1]['reasonCode']==('SERVICE_UNAVAILABLE'if provider_failure else'NONE')
    assert recorded[1]['durationBucket']=='lt_250ms'
    assert all(e['providerFamily'] is e['providerRequestUnits'] is e['estimatedCostMicrousd'] is None for e in recorded)
    assert 'PRIVATE_INVOKE'not in json.dumps(response) and 'attempt'not in json.dumps(response)
    before=deepcopy(w[3]('PERIOD#p1'))
    for _ in range(2):
        status,replay=service.handle(api_event(base,'POST '+path+'/reconcile',{'transportVersion':req['transportVersion'],'checkId':req['checkId'],'operationProof':proof}))
        assert status==200 and replay['accounting']==response['accounting']
    assert observed==[1] and attempts(w,proof)==recorded and w[3]('PERIOD#p1')==before
    assert before['reservedChecks']==0 and before['usedChecks']==response['accounting']['chargedChecks']


@pytest.mark.parametrize('family',['message_check','recovery_clarification'])
def test_valid_unavailable_is_internally_successful_not_logical_completion(bounded_world,family):
    w=bounded_world;service,base,_,path,req,proof=setup_flow(w,family)
    def unavailable(body):
        if family=='message_check':
            from message_evaluator.policy import result
            return result(body['checkId'],limits=['PROVIDER_UNAVAILABLE'])
        from shared_recovery_contract.validation import outcome
        return outcome(body['checkId'],'unavailable','PROVIDER_UNAVAILABLE')
    service.provider=unavailable
    status,response=service.handle(api_event(base,'POST '+path,req|{'operationProof':proof}))
    assert status==200 and response['accounting']['chargedChecks']==0
    assert w[3]('CHECK#'+proof)['processingOutcome']=='unavailable'
    assert attempts(w,proof)[1]['attemptOutcome']=='succeeded'


@pytest.mark.parametrize('family',['message_check','recovery_clarification'])
@pytest.mark.parametrize('kind',['none','raise'])
def test_business_budget_refusal_is_not_an_attempt(bounded_world,family,kind):
    w=bounded_world;service,base,calls,path,req,proof=setup_flow(w,family)
    def unavailable():
        if kind=='raise':raise RuntimeError('PRIVATE_BUDGET')
        return None
    service.budget.reserve=unavailable
    status,response=service.handle(api_event(base,'POST '+path,req|{'operationProof':proof}))
    assert status==200 and response['accounting']['chargedChecks']==0 and not calls
    assert attempts(w,proof)==[]


@pytest.mark.parametrize('family',FAMILIES)
def test_default_off_never_calls_new_helpers_or_changes_legacy_clock_and_budget(world,monkeypatch,family):
    import shared_check_authority.operations_callers as module
    def forbidden(*a,**kw):raise AssertionError('NEW_OPTIONAL_WORK')
    monkeypatch.setattr(module,'begin_authenticated_attempt',forbidden)
    monkeypatch.setattr(module,'complete_internal_attempt',forbidden)
    monkeypatch.setattr(module,'enter_internal_attempt',forbidden)
    ticks=[]
    def clock():ticks.append(1);return 0.0
    service,base,calls,path,req,proof=setup_flow(world,family,clock=clock);ticks.clear()
    budgets=[];original=service.provider
    def provider(body):budgets.append(body['executionBudgetMs']);return original(body)
    service.provider=provider
    status,response=service.handle(api_event(base,'POST '+path,req|{'operationProof':proof}))
    assert status==200 and response['state']=='settled' and budgets==[18000] and len(calls)==1
    assert len(ticks)==2 and 'operationsProjection'not in world[3]('CHECK#'+proof)


@pytest.mark.parametrize('family',FAMILIES)
def test_unknown_start_ack_keeps_incomplete_start_and_one_independent_business_dispatch(bounded_world,monkeypatch,family):
    w=bounded_world;service,base,calls,path,req,proof=setup_flow(w,family)
    original=w[0].client.transact_write_items;fired=[]
    def lost(**kw):
        response=original(**kw)
        values=[a['Update'].get('ExpressionAttributeValues',{}).get(':projection')for a in kw['TransactItems']if'Update'in a]
        if not fired and any(v and any(e['eventType']=='attempt_started'for e in v['events'])for v in values):
            fired.append(1);raise TimeoutError('PRIVATE_START_ACK')
        return response
    monkeypatch.setattr(w[0].client,'transact_write_items',lost)
    status,response=service.handle(api_event(base,'POST '+path,req|{'operationProof':proof}))
    assert status==200 and len(calls)==1 and fired==[1]
    assert len(attempts(w,proof))==1 and w[3]('CHECK#'+proof)['state']=='SETTLED'
    assert 'PRIVATE_START'not in json.dumps(response)


@pytest.mark.parametrize('family',FAMILIES)
def test_late_optional_start_can_skip_invocation_without_false_failed_finish(bounded_world,monkeypatch,family):
    w=bounded_world;ticks=[0.0]
    service,base,calls,path,req,proof=setup_flow(w,family,clock=lambda:ticks[0])
    original=ReceiptIngestion.start_attempt
    def late(self,*args,**kwargs):
        result=original(self,*args,**kwargs);ticks[0]=25.0;return result
    monkeypatch.setattr(ReceiptIngestion,'start_attempt',late)
    status,response=service.handle(api_event(base,'POST '+path,req|{'operationProof':proof}))
    assert status==200 and response['accounting']['chargedChecks']==0 and not calls
    assert len(attempts(w,proof))==1
    assert attempts(w,proof)[0]['eventType']=='attempt_started'


@pytest.mark.parametrize('family',FAMILIES)
def test_finish_ack_unknown_never_repeats_finish_or_evaluator(bounded_world,monkeypatch,family):
    w=bounded_world;service,base,calls,path,req,proof=setup_flow(w,family)
    real=w[0].client.transact_write_items;finished=[]
    def lost(**kw):
        response=real(**kw)
        projections=[a['Update'].get('ExpressionAttributeValues',{}).get(':projection')for a in kw['TransactItems']if'Update'in a]
        if any(p and any(e['eventType']=='attempt_finished'for e in p['events'])for p in projections):
            finished.append(1);raise TimeoutError('PRIVATE_FINISH_ACK')
        return response
    monkeypatch.setattr(w[0].client,'transact_write_items',lost)
    status,response=service.handle(api_event(base,'POST '+path,req|{'operationProof':proof}))
    assert status==200 and len(calls)==1 and finished==[1] and len(attempts(w,proof))==2
    before=deepcopy(w[3]('PERIOD#p1'))
    service.handle(api_event(base,'POST '+path+'/reconcile',{'transportVersion':req['transportVersion'],'checkId':req['checkId'],'operationProof':proof}))
    assert finished==[1] and len(calls)==1 and w[3]('PERIOD#p1')==before


def ticket_case(w):
    proof,op,event=pending(w);event['routeKey']='POST /v1/url-checks';scope=object()
    ticket=begin_authenticated_attempt(w[0],event,proof,op['executionToken'],family='url_check',client_check_id='bound-client',
        transport_version='1.0.0-candidate.2',scope=scope,request_started=0.0,provider_budget_ms=18000,remaining_ms=lambda:29000,clock=lambda:0.0)
    assert ticket is not None
    ticket=enter_internal_attempt(ticket,clock=lambda:0.0);assert ticket is not None
    completed=complete_internal_attempt(ticket,succeeded=True,clock=lambda:.1);assert completed is not None
    return proof,op,event,scope,completed


@pytest.mark.parametrize('drift',['owner','proof','execution','outcome','duration','binding','scope'])
def test_tampered_or_cross_request_ticket_falls_back_without_finish(bounded_world,drift):
    w=bounded_world;proof,op,event,scope,ticket=ticket_case(w)
    if drift=='owner':ticket=replace(ticket,owner='different-owner')
    elif drift=='proof':ticket=replace(ticket,proof='different-proof')
    elif drift=='execution':ticket=replace(ticket,execution_token='f'*32)
    elif drift=='outcome':ticket=replace(ticket,outcome='failed')
    elif drift=='duration':ticket=replace(ticket,duration_ms=10000)
    elif drift=='binding':ticket=replace(ticket,binding=replace(ticket.binding,client_check_id='other'))
    else:scope=object()
    before=deepcopy(attempts(w,proof))
    decision=capture_authenticated(w[0],event,proof,family='url_check',client_check_id='bound-client',transport_version='1.0.0-candidate.2',remaining_ms=lambda:29000,clock=lambda:0.0,attempt=ticket,attempt_scope=scope)
    assert decision in('REVALIDATED','CAPTURED') and attempts(w,proof)==before
    assert ACCOUNT not in repr(ticket) and op['executionToken']not in repr(ticket)


def test_ticket_can_finish_only_once_even_when_reused(bounded_world,monkeypatch):
    w=bounded_world;proof,op,event,scope,ticket=ticket_case(w);calls=[];real=ReceiptIngestion.finish_attempt
    def recorded(self,*args,**kw):calls.append(1);return real(self,*args,**kw)
    monkeypatch.setattr(ReceiptIngestion,'finish_attempt',recorded)
    def capture():return capture_authenticated(w[0],event,proof,family='url_check',client_check_id='bound-client',transport_version='1.0.0-candidate.2',remaining_ms=lambda:29000,clock=lambda:0.0,attempt=ticket,attempt_scope=scope)
    assert capture()=='CAPTURED' and capture()=='REVALIDATED' and calls==[1]
    assert len(attempts(w,proof))==2


@pytest.mark.parametrize('family',FAMILIES)
def test_duration_excludes_post_start_request_preparation(bounded_world,monkeypatch,family):
    import url_consumer.service as url
    import message_consumer.service as message
    import recovery_consumer.service as recovery
    module={'url_check':url,'message_check':message,'recovery_clarification':recovery}[family]
    w=bounded_world;ticks=[0.0]
    service,base,_,path,req,proof=setup_flow(w,family,clock=lambda:ticks[0])
    real_entry=module._enter_attempt;real_provider=service.provider
    def enter(*args):
        ticks[0]=.7
        return real_entry(*args)
    def provider(body):
        ticks[0]=.8
        return real_provider(body)
    monkeypatch.setattr(module,'_enter_attempt',enter);service.provider=provider
    status,response=service.handle(api_event(base,'POST '+path,req|{'operationProof':proof}))
    assert status==200 and response['state']=='settled'
    assert attempts(w,proof)[1]['durationBucket']=='lt_250ms'


def test_rules_only_cannot_enter_after_enabled_optional_work_exhausts_deadline(bounded_world,monkeypatch):
    from shared_message_contract import validation_v3 as v3
    from message_evaluator.policy_v3 import evaluate
    w=bounded_world;service,base,calls,path,req=consumer(w,'message_check');ticks=[0.0]
    service.allow_candidate3_rules_only=True;service.clock=lambda:ticks[0]
    req['transportVersion']=v3.VERSION
    service.provider=lambda body:calls.append(body) or evaluate(body['checkId'],body['intent'],rules_only=True)
    _,prepared=service.handle(api_event(base,'POST '+path+'/prepare',req));proof=prepared['operationProof']
    original=ReceiptIngestion.start_attempt
    def late(self,*args,**kwargs):
        result=original(self,*args,**kwargs);ticks[0]=25.0;return result
    monkeypatch.setattr(ReceiptIngestion,'start_attempt',late)
    status,response=service.handle(api_event(base,'POST '+path,req|{'operationProof':proof}))
    assert status==200 and response['accounting']['chargedChecks']==0 and not calls
    assert len(attempts(w,proof))==1


def test_complete_requires_original_entered_ticket_and_enter_is_once(bounded_world):
    w=bounded_world;proof,op,event=pending(w);event['routeKey']='POST /v1/url-checks';scope=object()
    ticket=begin_authenticated_attempt(w[0],event,proof,op['executionToken'],family='url_check',client_check_id='bound-client',
        transport_version='1.0.0-candidate.2',scope=scope,request_started=0.0,provider_budget_ms=18000,remaining_ms=lambda:29000,clock=lambda:0.0)
    assert complete_internal_attempt(ticket,succeeded=True,clock=lambda:.1) is None
    assert enter_internal_attempt(replace(ticket,started=0.0),clock=lambda:.1) is None
    entered=enter_internal_attempt(ticket,clock=lambda:.2);assert entered is not None
    assert enter_internal_attempt(ticket,clock=lambda:.2) is None
    assert complete_internal_attempt(ticket,succeeded=True,clock=lambda:.3) is None
    completed=complete_internal_attempt(entered,succeeded=True,clock=lambda:.3)
    assert completed is not None and complete_internal_attempt(entered,succeeded=True,clock=lambda:.3) is None
