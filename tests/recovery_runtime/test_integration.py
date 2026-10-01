"""Moto transactions and fake evaluated proposals, never a model accuracy claim."""
import os,sys,json
from pathlib import Path
from copy import deepcopy
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('isolated Moto only',allow_module_level=True)
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests/shared_check_authority')]
from test_transactions import world,ACCOUNT,PAYLOAD,WORKER
from shared_check_authority.core import AuthorityError
from shared_recovery_contract.constants import VERSION,PLAYBOOK
from shared_recovery_contract.validation import outcome
from recovery_consumer.service import Consumer
from recovery_evaluator.policy import evaluate

class Budget:
    def __init__(self):self.count=0
    def reserve(self):self.count+=1;return 1
    def failed(self,w):pass

def request(check='recovery-check',text='I sent money.'):
    return {'transportVersion':VERSION,'checkId':check,'entryPoint':'recovery','language':'en','target':{'scope':'recovery_clarification','sanitizedText':text,'entities':[],'playbookVersion':PLAYBOOK}}

def event(base,route,body):
    e=deepcopy(base);e.update(version='2.0',routeKey='POST /v1/recovery-clarifications'+route,body=json.dumps(body),isBase64Encoded=False,rawQueryString='')
    e['requestContext']['http']={'method':'POST'};return e

def system(w,proposal=None):
    a,base,*_=w;calls=[];budget=Budget()
    def provider(body):
        calls.append(body)
        return evaluate(body['checkId'],body['intent'],ai=lambda *_:proposal or {'status':'classified','context':'clear','exposureIds':['sent_payment']})
    return Consumer(a,provider,lambda _:None,budget),base,calls,budget

def prepare(s,e,req=None):
    code,r=s.handle(event(e,'/prepare',req or request()));assert code==200 and r['state']=='prepared',r
    return r['operationProof']

def test_complete_lost_response_reconcile_usage_only_exact_once(world):
    s,e,calls,budget=system(world);proof=prepare(s,e);req=request()|{'operationProof':proof}
    code,r=s.handle(event(e,'',req));assert code==200 and r['state']=='settled',r
    assert r['accounting']['chargedChecks']==1 and r['resultAvailability']=='available'
    assert r['outcome']['exposureIds']==['sent_payment']
    assert r['outcome']['actionIds']==['payment_contact','payment_receipts']
    for route,body in [('',req),('/prepare',request()),('/reconcile',{'transportVersion':VERSION,'checkId':req['checkId'],'operationProof':proof})]:
        code,replay=s.handle(event(e,route,body));assert code==200,replay
        assert replay['accounting']==r['accounting'] and replay['outcome'] is None and replay['resultAvailability']=='not_retained'
    assert len(calls)==budget.count==1
    assert world[3]('PERIOD#p1')['usedChecks']==1 and world[3]('PERIOD#p1')['reservedChecks']==0
    stored=json.dumps(world[0].ddb.Table('authority').scan()['Items'],default=str)
    for sensitive in ['I sent money.','sent_payment','payment_contact','exposureIds','sanitizedText','actionIds']:assert sensitive not in stored

@pytest.mark.parametrize('context',['insufficient','contradictory','unsupported','suspected_injection'])
def test_abstain_zero_charge(world,context):
    s,e,c,b=system(world,{'status':'abstain','context':context,'exposureIds':[]});proof=prepare(s,e)
    _,r=s.handle(event(e,'',request()|{'operationProof':proof}))
    assert r['state']=='settled' and r['accounting']['chargedChecks']==0 and r['outcome']['exposureIds']==[]
    assert world[3]('PERIOD#p1')['reservedChecks']==0

@pytest.mark.parametrize('text',['I visited https://example.invalid/secret','My IP is fe80::1%eth0','Email me at a@example.invalid','Call 202-555-0123'])
def test_raw_privacy_before_preparation_or_provider(world,text):
    s,e,c,b=system(world);_,r=s.handle(event(e,'/prepare',request(text=text)))
    assert r['errorCode']=='PRIVACY_REVIEW_REQUIRED'
    assert world[3]('PREPARE#recovery-check') is None and c==[] and b.count==0
    assert world[3]('PERIOD#p1')['reservedChecks']==0


def test_expired_lease_usage_only_no_second_evaluation(world):
    s,e,c,b=system(world);proof=prepare(s,e);a=world[0]
    intent={k:v for k,v in request().items() if k in ('entryPoint','language','target')}|{'recoveryTransportVersion':VERSION}
    a.admit(e,intent,proof,client_check_id='recovery-check');world[5][0]+=121
    e['requestContext']['authorizer']['jwt']['claims']['exp']=str(world[5][0]+90)
    _,r=s.handle(event(e,'/reconcile',{'transportVersion':VERSION,'checkId':'recovery-check','operationProof':proof}))
    assert r['state']=='settled' and r['usage']['processingOutcome']=='failed' and r['accounting']['chargedChecks']==0
    assert r['outcome'] is None and c==[]


def test_cross_scope_reconcile_and_edited_intent_rejected(world):
    s,e,c,b=system(world);proof=prepare(s,e)
    with pytest.raises(AuthorityError,match='CHECK_ID_CONFLICT'):world[0].reconcile(e,proof,client_check_id='recovery-check',expected_scope='url')
    _,r=s.handle(event(e,'',request(text='I installed software.')|{'operationProof':proof}))
    assert r['errorCode']=='CHECK_ID_CONFLICT' and c==[]


def test_unknown_settlement_never_claims_zero_or_returns_sensitive_result(world,monkeypatch):
    s,e,c,b=system(world);proof=prepare(s,e)
    def uncertain(*args,**kw):raise AuthorityError('RECONCILIATION_REQUIRED')
    monkeypatch.setattr(world[0],'settle',uncertain)
    _,r=s.handle(event(e,'',request()|{'operationProof':proof}))
    assert r['state']=='unknown' and r['accounting']['chargedChecks'] is None and r['outcome'] is None


def test_authority_cannot_charge_usage_summary_as_if_grounded_result(world):
    s,e,c,b=system(world);proof=prepare(s,e);a=world[0]
    intent={k:v for k,v in request().items() if k in ('entryPoint','language','target')}|{'recoveryTransportVersion':VERSION}
    admitted=a.admit(e,intent,proof,client_check_id='recovery-check')
    from shared_recovery_contract.usage import usage
    with pytest.raises(AuthorityError,match='RESULT_SUMMARY_INVALID'):
        a.settle(WORKER,proof,admitted['executionToken'],'complete',result_summary=usage('recovery-check','complete'))
    assert world[3]('PERIOD#p1')['usedChecks']==0

@pytest.mark.parametrize('mutation',['device','account','allowance'])
def test_revocation_between_preview_and_dispatch_blocks_provider(world,mutation):
    s,e,c,b=system(world);proof=prepare(s,e)
    if mutation=='device':world[4]('devices','ACTIVE_BINDING',stateVersion=2,bindingFingerprint='device-two')
    if mutation=='account':world[4]('users','PROFILE',status='DELETING')
    if mutation=='allowance':world[4]('authority','PERIOD#p1',usedChecks=200)
    _,r=s.handle(event(e,'',request()|{'operationProof':proof}))
    assert r['state']=='unknown' and r['outcome'] is None and c==[]
    assert world[3]('CHECK#'+proof) is None


def test_complimentary_complete_zero_is_authoritative(world):
    world[4]('authority','ACCESS',basis='complimentary',validUntilEpoch=None)
    s,e,c,b=system(world);proof=prepare(s,e)
    _,r=s.handle(event(e,'',request()|{'operationProof':proof}))
    assert r['usage']['processingOutcome']=='complete' and r['accounting']['chargedChecks']==0


def test_prepared_cancel_has_no_reservation_or_dispatch(world):
    s,e,c,b=system(world);proof=prepare(s,e)
    assert world[3]('PERIOD#p1')['reservedChecks']==0 and c==[]
    world[5][0]+=61
    _,r=s.handle(event(e,'/reconcile',{'transportVersion':VERSION,'checkId':'recovery-check','operationProof':proof}))
    assert r['state']=='rejected' and r['accounting']['chargedChecks']==0 and c==[]


def test_corrupt_stored_summary_never_releases_category_data(world):
    s,e,c,b=system(world);proof=prepare(s,e);s.handle(event(e,'',request()|{'operationProof':proof}))
    world[4]('authority','CHECK#'+proof,resultSummary=outcome('recovery-check','complete',None,['sent_payment']))
    _,r=s.handle(event(e,'/reconcile',{'transportVersion':VERSION,'checkId':'recovery-check','operationProof':proof}))
    assert r['state']=='unknown' and r['outcome'] is None and r['usage'] is None
    assert r['accounting']['chargedChecks'] is None


def test_background_lease_worker_produces_reconcilable_usage_only_failure(world):
    from shared_check_authority.recovery import Recovery
    s,e,c,b=system(world);proof=prepare(s,e);a=world[0]
    intent={k:v for k,v in request().items() if k in ('entryPoint','language','target')}|{'recoveryTransportVersion':VERSION}
    a.admit(e,intent,proof,client_check_id='recovery-check');world[5][0]+=121
    worker=Recovery(a.ddb,'authority',now=a.now)
    assert worker.expire(a._partition(ACCOUNT,'k1'),proof) is True
    assert worker.expire(a._partition(ACCOUNT,'k1'),proof) is False
    e['requestContext']['authorizer']['jwt']['claims']['exp']=str(world[5][0]+90)
    _,r=s.handle(event(e,'/reconcile',{'transportVersion':VERSION,'checkId':'recovery-check','operationProof':proof}))
    assert r['state']=='settled' and r['accounting']['chargedChecks']==0 and r['usage']['processingOutcome']=='failed'
    assert r['outcome'] is None and c==[] and world[3]('PERIOD#p1')['reservedChecks']==0
