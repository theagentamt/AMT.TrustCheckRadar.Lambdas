"""Real Moto feedback races/ownership; never provider calls or deployed evidence."""
import sys,os,json
from pathlib import Path
from copy import deepcopy
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('isolated Moto',allow_module_level=True)
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests/shared_check_authority')]
from test_transactions import world,WORKER,PAYLOAD,ACCOUNT
from result_feedback.service import Feedback,plain
from result_feedback.validation import *
from shared_check_authority.core import AuthorityError


def setup(w):
    a,e,*_=w
    proof=a.prepare(e,PAYLOAD,'owned',client_check_id='owned')
    admission=a.admit(e,PAYLOAD,proof,client_check_id='owned')
    summary={'schemaVersion':1,'checkId':'owned','verdict':'no_known_threat_detected','processingOutcome':'complete','coverage':'supported_checks_complete','reasonCodes':['NO_LIST_MATCH','BROWSER_NAVIGATION_NOT_EVALUATED'],'transportWarnings':[],'threatTypes':[],'lookupCount':1,'providerCallCount':1,'observedHopCount':1,'scope':'HTTP_REDIRECTS_AND_GOOGLE_LOOKUP','consumerAccessEnabled':False}
    row=a.settle(WORKER,proof,admission['executionToken'],'complete',result_summary=summary)
    request={'feedbackTransportVersion':VERSION,'feedbackId':'report-one','result':{'checkId':'owned','operationProof':proof,'receiptId':row['receiptId'],'assessmentTransportVersion':'1.0.0-candidate.1'},'category':'unclear'}
    return Feedback(a),e,request


def http(e,body):
    e=deepcopy(e);e.update(version='2.0',routeKey='POST /v1/result-feedback',body=json.dumps(body),rawQueryString='',isBase64Encoded=False)
    e['requestContext']['http']={'method':'POST'};return e


def test_write_retry_new_vote_conflict_and_original_receipt_unchanged(world):
    service,e,r=setup(world);before=deepcopy(world[3]('CHECK#'+r['result']['operationProof']));period=deepcopy(world[3]('PERIOD#p1'))
    code,first=service.handle(http(e,r));assert code==200 and first['status']=='accepted',first
    _,retry=service.handle(http(e,r));assert retry==first
    _,second=service.handle(http(e,r|{'feedbackId':'report-two','category':'looks_like_scam'}));assert second['status']=='already_received' and second['receivedAt']==first['receivedAt']
    code,changed=service.handle(http(e,r|{'category':'looks_legitimate'}));assert code==409 and changed['reasonCode']=='FEEDBACK_ID_CONFLICT'
    after=world[3]('CHECK#'+r['result']['operationProof']);stored=after.pop('feedback')
    assert after==before and stored=={'feedbackId':'report-one','category':'unclear','receivedAt':world[5][0],'policyVersion':POLICY}
    assert world[3]('PERIOD#p1')==period


def test_admission_expired_proof_valid_until_original_receipt_expiry(world):
    service,e,r=setup(world);world[5][0]+=61
    _,reply=service.handle(http(e,r));assert reply['status']=='accepted'
    world[5][0]+=500;e['requestContext']['authorizer']['jwt']['claims']['exp']=str(world[5][0]+90)
    code,reply=service.handle(http(e,r));assert code==404 and reply['reasonCode']=='RESULT_UNAVAILABLE'


def test_subscription_expiry_does_not_block_feedback(world,monkeypatch):
    service,e,r=setup(world);world[4]('authority','ACCESS',state='INACTIVE');world[4]('authority','PERIOD#p1',usedChecks=200)
    original=world[0]._get
    def bounded(table,key):
        assert key['SK']!='ACCESS' and not key['SK'].startswith('PERIOD#'),'billing read forbidden'
        return original(table,key)
    monkeypatch.setattr(world[0],'_get',bounded)
    assert service.handle(http(e,r))[1]['status']=='accepted'

@pytest.mark.parametrize('field,value',[('checkId','other'),('receiptId','f'*32),('assessmentTransportVersion','1.0.0-message-candidate.1'),('operationProof','v1_k1_6b49d200_1234567890abcdef_1234567890abcdef12345678')])
def test_forged_reference_rejected_without_write(world,field,value):
    service,e,r=setup(world);r['result'][field]=value
    code,reply=service.handle(http(e,r));assert code==404 and reply['reasonCode']=='RESULT_UNAVAILABLE'
    assert all('feedback' not in x for x in world[0].ddb.Table('authority').scan()['Items'])

@pytest.mark.parametrize('state',['failed','invalid_input','unsupported','blocked','unavailable'])
def test_nonassessed_terminal_outcomes_ineligible(world,state):
    service,e,r=setup(world);key='CHECK#'+r['result']['operationProof'];row=world[3](key);summary=deepcopy(row['resultSummary']);summary['processingOutcome']=state
    world[4]('authority',key,processingOutcome=state,resultSummary=summary)
    assert service.handle(http(e,r))[1]['reasonCode']=='RESULT_UNAVAILABLE'

@pytest.mark.parametrize('scope',[None,'legacy','recovery_clarification'])
def test_missing_or_unrecognized_scope_not_defaulted_to_url(world,scope):
    service,e,r=setup(world);world[4]('authority','CHECK#'+r['result']['operationProof'],projectionScope=scope)
    assert service.handle(http(e,r))[1]['reasonCode']=='RESULT_UNAVAILABLE'

@pytest.mark.parametrize('existing',[False,True])
@pytest.mark.parametrize('race',['device','deletion','expiry'])
def test_write_and_duplicate_ack_races_are_transaction_fenced(world,monkeypatch,existing,race):
    service,e,r=setup(world)
    if existing:assert service.handle(http(e,r))[1]['status']=='accepted'
    original=world[0]._transact
    def raced(items):
        if any(x.get('Update',x.get('ConditionCheck',{})).get('Key',{}).get('SK','').startswith('CHECK#') for x in items):
            if race=='device':world[4]('devices','ACTIVE_BINDING',stateVersion=2,bindingFingerprint='other-device')
            if race=='deletion':world[2]('deletion','ACCOUNT_DELETION',status='REQUESTED')
            if race=='expiry':world[4]('authority','CHECK#'+r['result']['operationProof'],expiresAt=world[5][0]-1)
        return original(items)
    monkeypatch.setattr(world[0],'_transact',raced)
    _,reply=service.handle(http(e,r));assert reply['status']=='unknown' and reply['receivedAt'] is None
    assert ('feedback' in world[3]('CHECK#'+r['result']['operationProof']))==existing


def test_lost_ack_same_id_confirms_without_duplicate(world,monkeypatch):
    service,e,r=setup(world);original=world[0]._transact
    def uncertain(items):
        original(items)
        if any('feedback' in x.get('Update',{}).get('ExpressionAttributeNames',{}).values() for x in items):raise AuthorityError('TRANSACTION_UNCERTAIN')
    monkeypatch.setattr(world[0],'_transact',uncertain)
    assert service.handle(http(e,r))[1]['status']=='unknown'
    stored=deepcopy(world[3]('CHECK#'+r['result']['operationProof'])['feedback'])
    monkeypatch.setattr(world[0],'_transact',original)
    assert service.handle(http(e,r))[1]['status']=='accepted'
    assert world[3]('CHECK#'+r['result']['operationProof'])['feedback']==stored


def test_concurrent_other_id_wins_then_loser_gets_distinct_ack(world,monkeypatch):
    service,e,r=setup(world);original=world[0]._transact;fired=[False]
    def interleave(items):
        if not fired[0] and any('feedback' in x.get('Update',{}).get('ExpressionAttributeNames',{}).values() for x in items):
            fired[0]=True
            monkeypatch.setattr(world[0],'_transact',original)
            assert service.handle(http(e,r|{'feedbackId':'winner','category':'unhelpful'}))[1]['status']=='accepted'
        return original(items)
    monkeypatch.setattr(world[0],'_transact',interleave)
    assert service.handle(http(e,r))[1]['status']=='unknown'
    assert service.handle(http(e,r))[1]['status']=='already_received'
    assert world[3]('CHECK#'+r['result']['operationProof'])['feedback']['feedbackId']=='winner'

@pytest.mark.parametrize('change',[{'chargedChecks':0},{'assessmentEpoch':-1},{'assessmentEpoch':1800000010},{'GSI1PK':'wrong'},{'GSI1SK':'wrong'}])
def test_corrupt_receipt_integrity_cannot_accept_feedback(world,change):
    service,e,r=setup(world);world[4]('authority','CHECK#'+r['result']['operationProof'],**change)
    assert service.handle(http(e,r))[1]['reasonCode']=='RESULT_UNAVAILABLE'

@pytest.mark.parametrize('reason',['INVALID_URL','NON_PUBLIC_DESTINATION','UNSUPPORTED_SCHEME','INVENTED_REASON'])
def test_private_partial_technical_result_not_assessed_display(world,reason):
    service,e,r=setup(world);key='CHECK#'+r['result']['operationProof'];summary=deepcopy(world[3](key)['resultSummary'])
    summary.update(verdict='unknown',processingOutcome='partial',coverage='limited',reasonCodes=[reason],providerCallCount=0,lookupCount=0,observedHopCount=0)
    world[4]('authority',key,chargedChecks=0,processingOutcome='partial',resultSummary=summary)
    assert service.handle(http(e,r))[1]['reasonCode']=='RESULT_UNAVAILABLE'

@pytest.mark.parametrize('offset',[-1,1])
def test_impossible_stored_feedback_time_never_acknowledged(world,offset):
    service,e,r=setup(world);key='CHECK#'+r['result']['operationProof']
    assert service.handle(http(e,r))[1]['status']=='accepted'
    stored=plain(world[3](key)['feedback']);stored['receivedAt']+=offset
    world[4]('authority',key,feedback=stored)
    status,reply=service.handle(http(e,r));assert status==503 and reply['status']=='unknown' and reply['receivedAt'] is None

@pytest.mark.parametrize('version',[1,2])
@pytest.mark.parametrize('state',['complete','partial','inconclusive'])
def test_message_both_versions_assessed_states_accept_without_accounting_change(world,version,state):
    from message_evaluator.policy import result as result1
    from message_evaluator.policy_v2 import result as result2
    a,e,*_=world
    payload={'entryPoint':'message','language':'es','target':{'scope':'sanitized_message','sourceType':'pasted_text','sanitizedText':'Mensaje de ejemplo.','speakerRole':'other','entities':[],'withheldLinks':False,'reviewedLinks':[]}}
    transport=f'1.0.0-message-candidate.{version}'
    if version==2:payload['messageTransportVersion']=transport
    proof=a.prepare(e,payload,'message',client_check_id='message');admission=a.admit(e,payload,proof,client_check_id='message')
    args={'rules':['REQUEST_SECRET_DISCLOSURE']} if state=='complete' else {'rules':['REQUEST_SECRET_DISCLOSURE'],'limits':['WITHHELD_LINKS']} if state=='partial' else {}
    summary=(result2 if version==2 else result1)('message',**args)
    settled=a.settle(WORKER,proof,admission['executionToken'],state,result_summary=summary)
    body={'feedbackTransportVersion':VERSION,'feedbackId':'same-opaque-id','category':'looks_legitimate','result':{'checkId':'message','operationProof':proof,'receiptId':settled['receiptId'],'assessmentTransportVersion':transport}}
    period=deepcopy(world[3]('PERIOD#p1'));before=deepcopy(world[3]('CHECK#'+proof))
    assert Feedback(a).handle(http(e,body))[1]['status']=='accepted'
    after=world[3]('CHECK#'+proof);after.pop('feedback');assert after==before and world[3]('PERIOD#p1')==period

@pytest.mark.parametrize('mode',['expiry','deletion'])
def test_existing_lifecycle_removes_entire_embedded_feedback(world,mode):
    service,e,r=setup(world);assert service.handle(http(e,r))[1]['status']=='accepted'
    key='CHECK#'+r['result']['operationProof'];a=world[0]
    if mode=='expiry':
        from shared_check_authority.expiry import Expiry
        world[5][0]+=a.s.receipt_retention_seconds
        assert Expiry(a.ddb,'authority',now=a.now).expire(a._partition(ACCOUNT,'k1'),key)
    else:
        from test_deletion import setup as deletion_setup,finish
        bridge,cmd,_=deletion_setup(world);assert finish(bridge,cmd)['complete']
    assert world[3](key) is None


def test_same_id_can_report_another_independently_owned_result(world):
    service,e,r=setup(world);assert service.handle(http(e,r))[1]['status']=='accepted'
    a=world[0];proof=a.prepare(e,PAYLOAD,'second',client_check_id='second');admission=a.admit(e,PAYLOAD,proof,client_check_id='second')
    summary=plain(world[3]('CHECK#'+r['result']['operationProof'])['resultSummary']);summary['checkId']='second'
    settled=a.settle(WORKER,proof,admission['executionToken'],'complete',result_summary=summary)
    r['result'].update(checkId='second',operationProof=proof,receiptId=settled['receiptId'])
    assert service.handle(http(e,r))[1]['status']=='accepted'


def test_other_authenticated_account_cannot_attach_feedback(world):
    service,e,r=setup(world);a=world[0];other='other-account'
    a.ddb.Table('users').put_item(Item={'PK':'USER#'+other,'SK':'PROFILE','sub':other,'status':'ACTIVE','ageVerified':True})
    for sk,attrs in [('ACTIVE_BINDING',{'recordType':'ACTIVE_BINDING_POINTER','stateVersion':1,'bindingFingerprint':'device-one'}),('DEVICE#device-one',{'accountId':other,'status':'ACTIVE','bindingFingerprint':'device-one'})]:
        a.ddb.Table('devices').put_item(Item={'PK':'USER#'+other,'SK':sk,**attrs})
    e=deepcopy(e);e['requestContext']['authorizer']['jwt']['claims']['sub']=other
    assert service.handle(http(e,r))[1]['reasonCode']=='RESULT_UNAVAILABLE'
    assert 'feedback' not in world[3]('CHECK#'+r['result']['operationProof'])

@pytest.mark.parametrize('raw',['{"category":"private","category":"duplicate"}','x'*4097,'['*1000])
def test_malformed_input_redacted_and_no_receipt_write(world,raw,capsys):
    service,e,r=setup(world);event=http(e,r);event['body']=raw
    status,reply=service.handle(event);assert status==422 and reply['reasonCode']=='INPUT_REJECTED'
    assert 'feedback' not in world[3]('CHECK#'+r['result']['operationProof']) and capsys.readouterr().out==''


def test_inactive_device_and_rate_limit_cannot_write(world):
    from dataclasses import replace
    service,e,r=setup(world);world[4]('devices','ACTIVE_BINDING',stateVersion=2,bindingFingerprint='other')
    assert service.handle(http(e,r))[1]['reasonCode']=='ACTIVE_DEVICE_REQUIRED'
    world[0].s=replace(world[0].s,attempts_per_window=1)
    status,reply=service.handle(http(e,r));assert status==429 and reply['retryable'] is True
    assert 'feedback' not in world[3]('CHECK#'+r['result']['operationProof'])
