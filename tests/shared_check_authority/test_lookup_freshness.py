"""Actual authority settlement/replay keeps provider clocks and usage independent."""
import sys,json,importlib.util
from pathlib import Path
from copy import deepcopy
import pytest
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'src/url_assessment'),str(Path(__file__).parent)]
from test_transactions import world,PAYLOAD,TrustedWorkerContext,ACCOUNT
from test_consumer import mapper,event,request,result
from url_consumer.service import Consumer,FRESH_VERSION,fresh_mapper,timestamp
from shared_check_authority.core import AuthorityError
from lookup_provider import parse_lookup,Unavailable
from shared_lookup_freshness import epoch,current

@pytest.mark.parametrize('expiry',['2027-01-15T08:00:00Z','2000-01-01T00:00:00Z','2027-01-15T08:00:00+00:00','2027-01-15','2027-01-15T08:00:00.1234567890Z',None,True])
def test_invalid_or_expired_provider_time(expiry):
    with pytest.raises(Unavailable):parse_lookup({'threat':{'threatTypes':['MALWARE'],'expireTime':expiry}},now=1800000000)

def test_nanosecond_boundary_is_not_extended():
    from decimal import Decimal
    expiry='2027-01-15T08:00:00.000000001Z'
    value=parse_lookup({'threat':{'threatTypes':['MALWARE'],'expireTime':expiry}},now=Decimal('1800000000'))
    assert value['validUntil']==expiry
    with pytest.raises(Unavailable):parse_lookup({'threat':{'threatTypes':['MALWARE'],'expireTime':expiry}},now=epoch(expiry))
    assert not current(value['observedAt'],expiry,epoch(expiry))

def request2():
    r=request();r.update(transportVersion=FRESH_VERSION,contractVersion=fresh_mapper().VERSION);return r

def modern(check,now,matched=False):
    r=result(check);r.update(schemaVersion=2,lookupObservedAt=timestamp(now),lookupValidUntil=None)
    if matched:r.update(verdict='high_risk',processingOutcome='complete',reasonCodes=['KNOWN_THREAT_MATCH'],threatTypes=['MALWARE'],lookupValidUntil=timestamp(now+20))
    return r

def submit(service,e):
    status,b=service.handle(event(e,'POST /v1/url-checks/prepare',request2()));assert status==200,b
    proof=b['operationProof'];status,b=service.handle(event(e,'POST /v1/url-checks',request2()|{'operationProof':proof}));assert status==200,b
    return proof,b

def test_actual_settlement_expired_replay_keeps_charge_and_original_times(world):
    a,e,_,row,_,clock=world;calls=[]
    def provider(body):calls.append(body);return modern(body['checkId'],clock[0],True)
    service=Consumer(a,mapper,provider,lambda _:None)
    proof,b=submit(service,e);before=deepcopy(row('CHECK#'+proof));assert b['accounting']['chargedChecks']==1
    assert b['outcome']['verdict']=='high_risk';clock[0]+=20
    status,after=service.handle(event(e,'POST /v1/url-checks/reconcile',{'transportVersion':FRESH_VERSION,'checkId':'client-1','operationProof':proof}))
    assert status==200,after
    assert after['outcome']['verdict']=='unknown' and after['outcome']['reasonCodes']==['PROVIDER_EVIDENCE_EXPIRED']
    assert after['accounting']==b['accounting'];assert len(calls)==1
    assert row('CHECK#'+proof)==before and row('PERIOD#p1')['usedChecks']==1
    assert after['outcome']['evidence'][0] == b['outcome']['evidence'][0] | {'freshness':'expired'}
    wrong=service.handle(event(e,'POST /v1/url-checks/reconcile',{'transportVersion':'1.0.0-candidate.1','checkId':'client-1','operationProof':proof}))
    assert wrong[0]==409 and wrong[1]['errorCode']=='CHECK_ID_CONFLICT'

def test_expired_before_initial_settlement_not_charged(world):
    a,e,_,row,_,clock=world
    def provider(body):r=modern(body['checkId'],clock[0],True);clock[0]+=20;return r
    service=Consumer(a,mapper,provider,lambda _:None)
    proof,b=submit(service,e)
    assert b['accounting']['chargedChecks']==0 and b['outcome'] is None
    assert row('CHECK#'+proof)['processingOutcome']=='failed'

def test_export_preserves_new_receipt_summary(world):
    from account_export_api.projection import receipt
    a,e,_,row,_,clock=world
    service=Consumer(a,mapper,lambda b:modern(b['checkId'],clock[0],True),lambda _:None)
    proof,_=submit(service,e);clock[0]+=20
    exported=receipt(row('CHECK#'+proof),clock[0])
    assert exported['chargedChecks']==1 and exported['processingOutcome']=='complete'
    assert exported['resultSummary']['lookupValidUntil']==timestamp(clock[0])

@pytest.mark.parametrize('poison',[[[]],[{}],[True],['MALWARE','MALWARE']])
def test_malformed_nested_threats_fail_bounded(poison):
    from shared_lookup_freshness import evidence
    with pytest.raises(ValueError,match='INVALID_PROVIDER_EVIDENCE'):
        evidence({'threatTypes':poison,'observedAt':'2027-01-15T08:00:00Z','validUntil':'2027-01-15T08:00:01Z'})

@pytest.mark.parametrize('access_change',['device','subscription','exhausted'])
def test_expired_replay_is_terminal_even_when_new_check_access_changes(world,access_change):
    a,e,_,row,change,clock=world
    s=Consumer(a,mapper,lambda b:modern(b['checkId'],clock[0],True),lambda _:None)
    proof,accepted=submit(s,e);clock[0]+=20
    if access_change=='device':change('devices','ACTIVE_BINDING',bindingFingerprint='another-device')
    elif access_change=='subscription':change('authority','ACCESS',state='INACTIVE')
    else:change('authority','PERIOD#p1',usedChecks=200)
    before=deepcopy(row('CHECK#'+proof))
    status,b=s.handle(event(e,'POST /v1/url-checks/reconcile',{'transportVersion':FRESH_VERSION,'checkId':'client-1','operationProof':proof}))
    assert status==200 and b['outcome']['retry']['disposition']=='do_not_retry'
    assert b['accounting']==accepted['accounting'] and row('CHECK#'+proof)==before

@pytest.mark.parametrize('late',['provider','account_read'])
def test_precise_clock_prevents_within_same_second_initial_charge(world,late):
    from decimal import Decimal
    a,e,_,row,_,clock=world;precise=[Decimal(clock[0])+Decimal('.01')]
    a.freshness_now=lambda:precise[0]
    calls=[];original=a._account_conditions
    def condition(account):
        value=original(account)
        if late=='account_read':precise[0]=Decimal(clock[0])+Decimal('.9')
        return value
    def provider(body):
        r=modern(body['checkId'],clock[0],True);r['lookupValidUntil']=timestamp(clock[0]).replace('Z','.1Z')
        calls.append(body)
        if late=='provider':precise[0]=Decimal(clock[0])+Decimal('.9')
        else:a._account_conditions=condition
        return r
    proof,b=submit(Consumer(a,mapper,provider,lambda _:None),e)
    assert b['accounting']['chargedChecks']==0 and len(calls)==1
    assert row('CHECK#'+proof)['processingOutcome']=='failed' and row('PERIOD#p1')['reservedChecks']==0

@pytest.mark.parametrize('route',['POST /v1/url-checks/prepare','POST /v1/url-checks'])
def test_legacy_new_requests_refuse_before_provider_or_reservation(world,route):
    a,e,_,row,_,_=world
    s=Consumer(a,mapper,lambda _:pytest.fail('provider'),lambda _:pytest.fail('refresh'))
    status,b=s.handle(event(e,route,request()|{'transportVersion':'1.0.0-candidate.1','contractVersion':'0.2.0-candidate.1'}))
    assert status==422 and b['errorCode']=='CONTRACT_UNSUPPORTED'
    assert row('PERIOD#p1')['reservedChecks']==0 and row('INFLIGHT') is None

def test_legacy_original_receipt_reconciles_without_rebinding_or_current_warning(world):
    a,e,_,row,_,clock=world;payload=PAYLOAD
    proof=a.prepare(e,payload,'client-1',client_check_id='client-1');admitted=a.admit(e,payload,proof,client_check_id='client-1')
    legacy={k:v for k,v in modern('client-1',clock[0],True).items() if k not in ('lookupObservedAt','lookupValidUntil')};legacy['schemaVersion']=1
    a.settle(TrustedWorkerContext(ACCOUNT),proof,admitted['executionToken'],'complete',result_summary=legacy)
    before=deepcopy(row('CHECK#'+proof))
    import importlib.util
    spec=importlib.util.spec_from_file_location('original_mapper',ROOT/'contracts/url-assessment/v1-draft/reference_mapping.py');old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    service=Consumer(a,old,lambda _:pytest.fail('replay provider'),lambda _:None)
    req={'transportVersion':'1.0.0-candidate.1','checkId':'client-1','operationProof':proof}
    status,b=service.handle(event(e,'POST /v1/url-checks/reconcile',req))
    assert status==200 and b['outcome']['verdict']=='unknown' and b['accounting']['chargedChecks']==1
    assert service.handle(event(e,'POST /v1/url-checks/reconcile',req|{'transportVersion':FRESH_VERSION}))[0]==409
    assert row('CHECK#'+proof)==before

@pytest.mark.parametrize('modern_intent',[False,True])
def test_settlement_cannot_cross_bound_private_schema(world,modern_intent):
    a,e,_,row,_,clock=world;payload=PAYLOAD|({'urlTransportVersion':FRESH_VERSION} if modern_intent else {})
    proof=a.prepare(e,payload,'client-1',client_check_id='client-1');admitted=a.admit(e,payload,proof,client_check_id='client-1')
    value=modern('client-1',clock[0])
    if modern_intent:value={k:v for k,v in value.items() if k not in ('lookupObservedAt','lookupValidUntil')}|{'schemaVersion':1}
    before=deepcopy(row('CHECK#'+proof))
    with pytest.raises(AuthorityError,match='RESULT_SUMMARY_INVALID'):
        a.settle(TrustedWorkerContext(ACCOUNT),proof,admitted['executionToken'],'complete',result_summary=value)
    assert row('CHECK#'+proof)==before and row('PERIOD#p1')['usedChecks']==0
