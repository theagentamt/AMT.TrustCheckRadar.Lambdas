"""Real local Moto consumer wiring; no JWT signature/AWS/runtime proof claimed."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import sys

import boto3
from botocore.config import Config
import pytest

sys.path.insert(0,str(Path(__file__).parent))
from test_transactions import ACCOUNT, WORKER, world, Authority, AuthorityError, admit
from test_consumer import request as url_request, system as url_system, event as api_event
from test_operations_producer import enable
from test_operations_retirement import history_receipt
from shared_check_authority.operations_callers import capture_authenticated
from shared_check_authority.operations_ingestion import ReceiptIngestion
from shared_check_authority.operations_binding import ReceiptBinding

URL_VERSION='1.0.0-candidate.2'


@pytest.fixture
def bounded_world(world):
    old=world[0]
    # New injected client against the same local Moto tables, with the existing
    # runtime's actual one-attempt timeout configuration; no live credentials.
    ddb=boto3.resource('dynamodb',region_name='us-east-1',aws_access_key_id='synthetic',
        aws_secret_access_key='synthetic',config=Config(connect_timeout=.2,read_timeout=.3,retries={'total_max_attempts':1}))
    a=Authority(replace(old.s,operations_producer_enabled=True),ddb,now=old.now,freshness_now=old.freshness_now)
    return (a,*world[1:])


def pending(w,client='bound-client'):
    a,base,*_=w
    payload={k:v for k,v in url_request(client).items() if k in ('entryPoint','language','target')}|{'urlTransportVersion':URL_VERSION}
    proof=a.prepare(base,payload,client,client_check_id=client)
    operation=a.admit(base,payload,proof,client_check_id=client)
    event=api_event(base,'POST /v1/url-checks/reconcile',{'transportVersion':URL_VERSION,'checkId':client,'operationProof':proof})
    return proof,operation,event


def capture(w,proof,event,**kwargs):
    return capture_authenticated(w[0],event,proof,family=kwargs.pop('family','url_check'),
        client_check_id=kwargs.pop('client_check_id','bound-client'),transport_version=kwargs.pop('transport_version',URL_VERSION),
        remaining_ms=kwargs.pop('remaining_ms',lambda:29000),clock=kwargs.pop('clock',lambda:0.0),**kwargs)


def test_existing_authenticated_context_captures_and_revalidates_actual_owned_receipt(bounded_world):
    w=bounded_world;a,_,_,row,_,_=w
    proof,_,event=pending(w)
    assert capture(w,proof,event)=='CAPTURED'
    first=deepcopy(row('CHECK#'+proof))
    assert capture(w,proof,event)=='REVALIDATED'
    assert row('CHECK#'+proof)==first and row('PERIOD#p1')['reservedChecks']==1
    assert 'operations' not in event['body']


@pytest.mark.parametrize('field,value',[('iss','https://wrong.invalid'),('client_id','wrong'),
    ('token_use','id'),('scope','wrong'),('exp','1'),('sub','not-present-account')])
def test_bad_authorizer_context_cannot_use_body_identity_to_capture(bounded_world,field,value):
    w=bounded_world;proof,_,event=pending(w);before=deepcopy(w[3]('CHECK#'+proof))
    event['requestContext']['authorizer']['jwt']['claims'][field]=value
    event['body']=json.dumps({'accountId':ACCOUNT,'worker':{'account':ACCOUNT},'claims':{'sub':ACCOUNT}})
    assert capture(w,proof,event) in ('AUTH_REJECTED','ACCOUNT_REJECTED')
    assert w[3]('CHECK#'+proof)==before


@pytest.mark.parametrize('kind',['client','family','version','missing-client','metadata-family'])
def test_client_scope_version_and_source_family_are_exact(bounded_world,kind):
    w=bounded_world;a,_,_,row,_,_=w;proof,_,event=pending(w);kwargs={}
    if kind=='client':kwargs['client_check_id']='different'
    elif kind=='family':
        kwargs.update(family='message_check',transport_version='1.0.0-message-candidate.1')
        event['routeKey']='POST /v1/message-checks/reconcile'
    elif kind=='version':kwargs['transport_version']='1.0.0-candidate.1'
    else:
        changed=row('CHECK#'+proof)
        if kind=='missing-client':changed.pop('clientCheckId')
        else:changed['operationsReceipt'].update(service='message_check',inputKind='pasted_text')
        a.ddb.Table('authority').put_item(Item=changed)
    before=deepcopy(row('CHECK#'+proof))
    assert capture(w,proof,event,**kwargs)=='SOURCE_REJECTED'
    assert row('CHECK#'+proof)==before


def test_valid_other_account_cannot_capture_original_owner_even_with_body_selection(bounded_world):
    w=bounded_world;a,_,_,row,_,_=w;proof,_,event=pending(w)
    other='another-approved-account'
    a.ddb.Table('users').put_item(Item={'PK':'USER#'+other,'SK':'PROFILE','sub':other,'status':'ACTIVE','ageVerified':True})
    event['requestContext']['authorizer']['jwt']['claims']['sub']=other
    event['body']=json.dumps({'accountId':ACCOUNT,'operationProof':proof})
    before=deepcopy(row('CHECK#'+proof))
    assert capture(w,proof,event)=='SOURCE_REJECTED'
    assert row('CHECK#'+proof)==before


@pytest.mark.parametrize('boundary',['low-remaining','remaining-exception','unsafe-sdk','elapsed','backward-clock'])
def test_insufficient_or_uncertain_budget_starts_no_capture_transaction(bounded_world,monkeypatch,boundary):
    w=bounded_world;a=w[0];proof,_,event=pending(w);before=deepcopy(w[3]('CHECK#'+proof));kwargs={};calls=[]
    if boundary=='low-remaining':kwargs['remaining_ms']=lambda:5999
    elif boundary=='remaining-exception':
        def unavailable():raise RuntimeError('PRIVATE')
        kwargs['remaining_ms']=unavailable
    elif boundary=='unsafe-sdk':a.client.meta.config.read_timeout=60
    elif boundary=='backward-clock':
        values=iter([10.0,9.0]);kwargs['clock']=lambda:next(values)
    else:
        ticks=[0.0];kwargs['clock']=lambda:ticks[0]
        original=a._account
        def slow(value):result=original(value);ticks[0]=5.0;return result
        monkeypatch.setattr(a,'_account',slow)
    monkeypatch.setattr(a.client,'transact_write_items',lambda **kw:calls.append(kw))
    assert capture(w,proof,event,**kwargs) in ('TIME_SKIPPED','UNAVAILABLE')
    assert not calls and w[3]('CHECK#'+proof)==before


@pytest.mark.parametrize('orphan',[False,True])
def test_seen_older_expired_full_index_erases_only_links_without_remint(bounded_world,orphan):
    w=bounded_world;a,base,_,row,_,clock=w
    proof,before=history_receipt(w)
    # The existing test helper's bound client is 'history'.
    assert ReceiptIngestion(a).capture(WORKER,proof)['decision']=='CAPTURED'
    old=row('CHECK#'+proof)
    old['GSI1SK']=f'{int(old["expiresAt"]):012d}#{old["PK"]}#{old["SK"]}'
    if orphan:old.pop('operationsReceipt');old.pop('operationsTerminalReasonCode')
    a.ddb.Table('authority').put_item(Item=old)
    clock[0]=int(before['operationsReceipt']['expiresAtEpoch'])
    event=api_event(base,'POST /v1/url-checks/reconcile',{'transportVersion':URL_VERSION,'checkId':'history','operationProof':proof})
    event['requestContext']['authorizer']['jwt']['claims']['exp']=str(clock[0]+90)
    assert capture(w,proof,event,client_check_id='history')=='EXPIRED'
    after=row('CHECK#'+proof)
    assert not {'operationsReceipt','operationsTerminalReasonCode','operationsProjection'} & set(after)
    assert after['governedHistory']==before['governedHistory'] and after['expiresAt']==before['expiresAt']
    assert capture(w,proof,event,client_check_id='history')=='NOT_RECORDED'
    assert row('CHECK#'+proof)==after


def test_missing_legacy_metadata_is_not_inferred_from_modern_request(bounded_world):
    w=bounded_world;a,_,_,row,_,_=w
    a.s=replace(a.s,operations_producer_enabled=False)
    proof,_,event=pending(w);before=deepcopy(row('CHECK#'+proof))
    enable(a)
    assert capture(w,proof,event)=='NOT_RECORDED'
    assert row('CHECK#'+proof)==before


def test_default_off_precedes_new_clock_or_service_reads(world,monkeypatch):
    a=world[0]
    def forbidden(*a,**kw):raise AssertionError('NEW_WORK')
    monkeypatch.setattr(a,'_account',forbidden)
    assert capture_authenticated(a,None,None,family=None,client_check_id=None,transport_version=None,
        remaining_ms=forbidden,clock=forbidden)=='DISABLED'


class Budget:
    def reserve(self):return 1
    def failed(self,window):pass


def consumer(w,family):
    a,base,*_=w;calls=[]
    if family=='url_check':
        service,_,calls=url_system(w);service.remaining=lambda:29000
        return service,base,calls,'/v1/url-checks',url_request('flow-client')
    if family=='message_check':
        from message_consumer.service import Consumer
        from message_evaluator.policy import evaluate
        from shared_message_contract import VERSION
        def provider(body):calls.append(1);return evaluate(body['checkId'],body['intent'],budget_ms=body['executionBudgetMs'])
        service=Consumer(a,provider,lambda _:None,Budget())
        req={'transportVersion':VERSION,'checkId':'flow-client','entryPoint':'message','language':'en',
            'target':{'scope':'sanitized_message','sourceType':'pasted_text','sanitizedText':'Happy birthday.',
                'speakerRole':'other','entities':[],'withheldLinks':False,'reviewedLinks':[]}}
        return service,base,calls,'/v1/message-checks',req
    from recovery_consumer.service import Consumer
    from recovery_evaluator.policy import evaluate
    from shared_recovery_contract.constants import VERSION,PLAYBOOK
    def provider(body):
        calls.append(1)
        return evaluate(body['checkId'],body['intent'],ai=lambda *_:{'status':'classified','context':'clear','exposureIds':['sent_payment']})
    service=Consumer(a,provider,lambda _:None,Budget())
    req={'transportVersion':VERSION,'checkId':'flow-client','entryPoint':'recovery','language':'en',
        'target':{'scope':'recovery_clarification','sanitizedText':'I sent money.','entities':[],'playbookVersion':PLAYBOOK}}
    return service,base,calls,'/v1/recovery-clarifications',req


@pytest.mark.parametrize('family',['url_check','message_check','recovery_clarification'])
def test_actual_consumers_wire_success_replay_without_exposing_projection(bounded_world,family):
    w=bounded_world;service,base,calls,path,req=consumer(w,family)
    status,prepared=service.handle(api_event(base,'POST '+path+'/prepare',req))
    assert status==200 and prepared['state']=='prepared'
    proof=prepared['operationProof'];assert w[3]('CHECK#'+proof) is None
    status,response=service.handle(api_event(base,'POST '+path,req|{'operationProof':proof}))
    assert status==200 and response['state']=='settled'
    stored=w[3]('CHECK#'+proof)
    assert stored['operationsProjection']['sourceState']=='SETTLED'
    assert stored['operationsProjection']['reference']['service']==family
    for _ in range(2):
        status,replay=service.handle(api_event(base,'POST '+path+'/reconcile',
            {'transportVersion':req['transportVersion'],'checkId':req['checkId'],'operationProof':proof}))
        assert status==200 and replay['accounting']==response['accounting']
    assert len(calls)==1 and w[3]('PERIOD#p1')['usedChecks']==response['accounting']['chargedChecks']
    assert 'operationsProjection' not in json.dumps(response) and 'logicalCheckRef' not in json.dumps(response)


@pytest.mark.parametrize('family',['url_check','message_check','recovery_clarification'])
@pytest.mark.parametrize('failure',['helper-exception','import-error','capture-write-error'])
def test_optional_capture_failure_has_exact_business_response_and_accounting_parity(bounded_world,monkeypatch,family,failure):
    import shared_check_authority.operations_callers as module
    import builtins
    w=bounded_world;service,base,calls,path,req=consumer(w,family)
    _,prepared=service.handle(api_event(base,'POST '+path+'/prepare',req));proof=prepared['operationProof']
    _,response=service.handle(api_event(base,'POST '+path,req|{'operationProof':proof}))
    query=api_event(base,'POST '+path+'/reconcile',{'transportVersion':req['transportVersion'],'checkId':req['checkId'],'operationProof':proof})
    expected=service.handle(query);before=deepcopy(w[3]('PERIOD#p1'));injected=[]
    if failure=='helper-exception':
        def broken(*a,**kw):
            injected.append(1);raise RuntimeError('PRIVATE_CAPTURE_SENTINEL')
        monkeypatch.setattr(module,'capture_authenticated',broken)
    elif failure=='import-error':
        old_import=builtins.__import__
        def broken(name,*args,**kwargs):
            if name=='shared_check_authority.operations_callers':
                injected.append(1);raise ImportError('PRIVATE_MODULE_SENTINEL')
            return old_import(name,*args,**kwargs)
        monkeypatch.setattr(builtins,'__import__',broken)
    else:
        original=w[0].client.transact_write_items
        def broken(**kwargs):
            if any('operationsProjection' in str(action.get('ConditionCheck',{}).get('ExpressionAttributeNames',{}).values()) for action in kwargs['TransactItems']):
                injected.append(1);raise TimeoutError('PRIVATE_WRITE_SENTINEL')
            return original(**kwargs)
        monkeypatch.setattr(w[0].client,'transact_write_items',broken)
    assert service.handle(query)==expected
    assert w[3]('PERIOD#p1')==before and len(calls)==1
    assert injected, 'the intended capture failure was not exercised'


@pytest.mark.parametrize('family',['url_check','message_check','recovery_clarification'])
def test_non_receipt_and_invalid_paths_do_not_start_optional_capture(bounded_world,monkeypatch,family):
    import shared_check_authority.operations_callers as module
    w=bounded_world;service,base,calls,path,req=consumer(w,family);captured=[]
    monkeypatch.setattr(module,'capture_authenticated',lambda *a,**kw:captured.append(1))
    status,prepared=service.handle(api_event(base,'POST '+path+'/prepare',req))
    assert status==200 and prepared['state']=='prepared' and not captured and not calls
    proof=prepared['operationProof']
    status,missing=service.handle(api_event(base,'POST '+path+'/reconcile',
        {'transportVersion':req['transportVersion'],'checkId':req['checkId'],'operationProof':proof}))
    assert status==200 and missing['state']=='unknown' and not captured and not calls
    bad=api_event(base,'POST '+path,req|{'unknownField':True})
    status,error=service.handle(bad)
    assert status==422 and error['state']=='unknown' and not captured and not calls
