import os
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':
    pytest.skip('Isolated emulator environment only',allow_module_level=True)
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from test_transactions import world, ACCOUNT, PAYLOAD
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from url_consumer.service import Consumer, GUARDED_VERSION as VERSION

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('canonical_url_mapper',ROOT/'contracts/url-assessment/0.4.0-candidate.1/reference_mapping.py')
mapper=importlib.util.module_from_spec(spec);spec.loader.exec_module(mapper)


def event(base,route,body):
    e=deepcopy(base);e.update(version='2.0',routeKey=route,body=json.dumps(body),isBase64Encoded=False,rawQueryString='')
    e['requestContext']['http']={'method':'POST'}
    return e


def request(check='client-1'):
    return {'transportVersion':VERSION,'contractVersion':mapper.VERSION,'checkId':check,**deepcopy(PAYLOAD)}


def result(check='client-1'):
    return {'schemaVersion':3,'matchScope':None,'targetContactStatus':'contacted','lookupObservedAt':'2027-01-15T08:00:00Z','lookupValidUntil':None,'checkId':check,'verdict':'no_known_threat_detected','processingOutcome':'complete','coverage':'supported_checks_complete','reasonCodes':['NO_LIST_MATCH','BROWSER_NAVIGATION_NOT_EVALUATED'],'transportWarnings':[],'threatTypes':[],'lookupCount':1,'providerCallCount':1,'observedHopCount':1,'scope':'GUARDED_HTTP_AND_GOOGLE_LOOKUP','consumerAccessEnabled':False}


def system(world,provider=None):
    a,e,*_=world;calls=[]
    def run(body):calls.append(body);return provider(body) if provider else result(body['checkId'])
    return Consumer(a,mapper,run,lambda _:None),e,calls


def prepared(service,e,check='client-1'):
    status,body=service.handle(event(e,'POST /v1/url-checks/prepare',request(check)))
    assert status==200 and body['state']=='prepared'
    return body['operationProof']


def test_real_core_consumer_complete_once_and_url_free_reconcile(world):
    service,e,calls=system(world)
    proof=prepared(service,e)
    assert not calls
    req=request()|{'operationProof':proof}
    status,body=service.handle(event(e,'POST /v1/url-checks',req))
    assert status==200 and body['state']=='settled' and body['accounting']['chargedChecks']==1
    assert body['outcome']['verdict']=='no_known_threat_detected'
    assert 14000<=calls[0]['executionBudgetMs']<=18000
    assert service.handle(event(e,'POST /v1/url-checks',req))[1]['accounting']==body['accounting']
    status,reconciled=service.handle(event(e,'POST /v1/url-checks/reconcile',{'transportVersion':VERSION,'checkId':'client-1','operationProof':proof}))
    assert reconciled['outcome']['verdict']=='no_known_threat_detected' and len(calls)==1


def test_partial_positive_preserved_without_charge(world):
    def provider(body):return result(body['checkId'])|{'verdict':'high_risk','processingOutcome':'partial','coverage':'limited','reasonCodes':['KNOWN_THREAT_MATCH'],'threatTypes':['MALWARE'],'lookupValidUntil':'2027-01-15T08:01:00Z','matchScope':'full_submitted_url','observedHopCount':0,'targetContactStatus':'not_contacted'}
    service,e,calls=system(world,provider)
    proof=prepared(service,e)
    status,body=service.handle(event(e,'POST /v1/url-checks',request()|{'operationProof':proof}))
    assert body['accounting']['chargedChecks']==0 and body['outcome']['verdict']=='high_risk'
    assert body['outcome']['messageKey']=='url.known_threat_partial'


def test_provider_exception_is_zero_settlement_without_replay(world):
    def broken(_):raise TimeoutError('synthetic')
    service,e,calls=system(world,broken)
    proof=prepared(service,e)
    status,body=service.handle(event(e,'POST /v1/url-checks',request()|{'operationProof':proof}))
    assert status==200 and body['accounting']['chargedChecks']==0 and body['errorCode']=='SERVICE_UNAVAILABLE'
    service.handle(event(e,'POST /v1/url-checks',request()|{'operationProof':proof}))
    assert len(calls)==1


def test_client_id_cannot_be_rebound_to_operation_proof(world):
    service,e,calls=system(world);proof=prepared(service,e)
    status,body=service.handle(event(e,'POST /v1/url-checks',request('other')|{'operationProof':proof}))
    assert status==409 and body['errorCode']=='CHECK_ID_CONFLICT' and not calls
    assert body['accounting']['chargedChecks'] is None


def test_reconcile_after_device_and_subscription_change(world):
    service,e,calls=system(world);proof=prepared(service,e)
    req=request()|{'operationProof':proof}
    service.handle(event(e,'POST /v1/url-checks',req))
    a,_,_,_,change,_=world
    change('authority','ACCESS',state='REVOKED');change('devices','ACTIVE_BINDING',bindingFingerprint='new-device',stateVersion=2)
    status,body=service.handle(event(e,'POST /v1/url-checks/reconcile',{'transportVersion':VERSION,'checkId':'client-1','operationProof':proof}))
    assert status==200 and body['accounting']['chargedChecks']==1 and body['access']['state']=='device_action_required'
    assert len(calls)==1


@pytest.mark.parametrize('patch',[{'target':{'url':'https://example.com/#private','scope':'full_url','withheldComponents':[]}}, {'target':{'url':'https://example.com/path','scope':'origin_only','withheldComponents':['path']}},{'transportVersion':'future'},{'extra':True}])
def test_malformed_projection_and_unknown_version_never_call_provider(world,patch):
    service,e,calls=system(world)
    status,body=service.handle(event(e,'POST /v1/url-checks/prepare',request()|patch))
    assert status==422 and not calls


def test_url_in_reconciliation_request_rejected(world):
    service,e,calls=system(world);proof=prepared(service,e)
    status,body=service.handle(event(e,'POST /v1/url-checks/reconcile',{'transportVersion':VERSION,'checkId':'client-1','operationProof':proof,'url':'https://example.com/'}))
    assert status==422 and not calls


def test_expired_admission_recovered_without_provider(world):
    service,e,calls=system(world);proof=prepared(service,e)
    a,_,_,_,_,clock=world
    a.admit(e,PAYLOAD|{'urlTransportVersion':VERSION},proof,client_check_id='client-1')
    clock[0]+=a.s.worker_settlement_seconds;e['requestContext']['authorizer']['jwt']['claims']['exp']=str(clock[0]+100)
    status,body=service.handle(event(e,'POST /v1/url-checks/reconcile',{'transportVersion':VERSION,'checkId':'client-1','operationProof':proof}))
    assert status==200 and body['state']=='settled' and body['accounting']['chargedChecks']==0 and not calls
    assert body['errorCode']=='SERVICE_UNAVAILABLE'


def test_repeat_prepare_returns_existing_charge_not_zero(world):
    service,e,calls=system(world);proof=prepared(service,e)
    service.handle(event(e,'POST /v1/url-checks',request()|{'operationProof':proof}))
    status,body=service.handle(event(e,'POST /v1/url-checks/prepare',request()))
    assert status==200 and body['state']=='settled' and body['accounting']['chargedChecks']==1 and len(calls)==1


@pytest.mark.parametrize('route',['POST /v1/url-checks','POST /v1/url-checks/reconcile'])
def test_auth_failure_never_erases_prior_charge(world,route):
    service,e,calls=system(world);proof=prepared(service,e)
    service.handle(event(e,'POST /v1/url-checks',request()|{'operationProof':proof}))
    bad=deepcopy(e);bad['requestContext']['authorizer']['jwt']['claims']['exp']='1'
    body={'transportVersion':VERSION,'checkId':'client-1','operationProof':proof} if route.endswith('/reconcile') else request()|{'operationProof':proof}
    status,response=service.handle(event(bad,route,body))
    assert status==401 and response['accounting']['state']=='unknown' and response['accounting']['chargedChecks'] is None
    assert len(calls)==1


def test_refresh_failure_does_not_claim_prior_check_free(world):
    service,e,calls=system(world);proof=prepared(service,e)
    service.handle(event(e,'POST /v1/url-checks',request()|{'operationProof':proof}))
    from shared_check_authority.core import AuthorityError
    def fail(_):raise AuthorityError('TRANSACTION_UNCERTAIN')
    service.refresh=fail
    status,body=service.handle(event(e,'POST /v1/url-checks',request()|{'operationProof':proof}))
    assert status==503 and body['accounting']['chargedChecks'] is None


def test_disabled_handler_returns_complete_unknown_envelope(monkeypatch):
    from url_consumer.service import unavailable_envelope,validate_envelope
    body=unavailable_envelope()
    assert validate_envelope(body)==body and body['accounting']['chargedChecks'] is None


def test_expired_unadmitted_preparation_closes_authoritatively(world):
    service,e,calls=system(world);proof=prepared(service,e)
    a,_,_,row,_,clock=world
    req={'transportVersion':VERSION,'checkId':'client-1','operationProof':proof}
    assert service.handle(event(e,'POST /v1/url-checks/reconcile',req))[1]['accounting']['chargedChecks'] is None
    clock[0]+=a.s.operation_validity_seconds
    status,body=service.handle(event(e,'POST /v1/url-checks/reconcile',req))
    assert status==200 and body['state']=='rejected' and body['errorCode']=='OPERATION_EXPIRED'
    assert body['accounting']=={'state':'not_started','chargedChecks':0,'receiptId':None,'requiresReconciliation':False}
    assert row('PREPARE#client-1')['admissionState']=='CLOSED' and not calls
    assert service.handle(event(e,'POST /v1/url-checks/reconcile',req))[1]['accounting']==body['accounting']


def test_missing_or_retention_expired_preparation_remains_unknown(world):
    service,e,calls=system(world);proof=prepared(service,e)
    a,_,_,row,_,clock=world
    clock[0]+=a.s.receipt_retention_seconds+1
    e['requestContext']['authorizer']['jwt']['claims']['exp']=str(clock[0]+100)
    req={'transportVersion':VERSION,'checkId':'client-1','operationProof':proof}
    body=service.handle(event(e,'POST /v1/url-checks/reconcile',req))[1]
    assert body['state']=='unknown' and body['accounting']['chargedChecks'] is None
    a.ddb.Table('authority').delete_item(Key={'PK':a._partition(ACCOUNT,'k1'),'SK':'PREPARE#client-1'})
    assert service.handle(event(e,'POST /v1/url-checks/reconcile',req))[1]['accounting']['chargedChecks'] is None

@pytest.mark.parametrize('old',['1.0.0-candidate.1','1.0.0-candidate.2'])
def test_old_new_submissions_cannot_fall_back_to_fetch_first(world,old):
    service,e,calls=system(world)
    status,body=service.handle(event(e,'POST /v1/url-checks/prepare',request()|{'transportVersion':old}))
    assert status==422 and body['errorCode']=='CONTRACT_UNSUPPORTED' and not calls


def test_new_version_and_exact_projection_bound_to_proof(world):
    service,e,calls=system(world);proof=prepared(service,e)
    body=request()|{'operationProof':proof}
    body['target']['url']='https://other.example/'
    status,result=service.handle(event(e,'POST /v1/url-checks',body))
    assert status==409 and result['errorCode']=='CHECK_ID_CONFLICT' and not calls
    status,result=service.handle(event(e,'POST /v1/url-checks/reconcile',
        {'transportVersion':'1.0.0-candidate.2','checkId':'client-1','operationProof':proof}))
    assert status==409 and not calls


def test_frozen_candidate_two_receipt_still_reconciles_without_provider(world):
    from shared_check_authority.core import TrustedWorkerContext
    a,e,_,row,_,_=world
    payload=deepcopy(PAYLOAD)|{'urlTransportVersion':'1.0.0-candidate.2'}
    proof=a.prepare(e,payload,'historical',client_check_id='historical')
    admitted=a.admit(e,payload,proof,client_check_id='historical')
    value=result('historical');value.pop('matchScope');value.pop('targetContactStatus')
    value.update(schemaVersion=2,scope='HTTP_REDIRECTS_AND_GOOGLE_LOOKUP')
    a.settle(TrustedWorkerContext(ACCOUNT),proof,admitted['executionToken'],'complete',result_summary=value)
    before=deepcopy(row('CHECK#'+proof))
    s=Consumer(a,mapper,lambda _:pytest.fail('historical provider replay'),lambda _:None)
    status,body=s.handle(event(e,'POST /v1/url-checks/reconcile',
        {'transportVersion':'1.0.0-candidate.2','checkId':'historical','operationProof':proof}))
    assert status==200 and body['accounting']['chargedChecks']==1
    assert body['outcome']['contractVersion']=='0.3.0-candidate.1' and row('CHECK#'+proof)==before

@pytest.mark.parametrize('basis',['paid','trial','complimentary'])
@pytest.mark.parametrize('matched',[False,True])
def test_guarded_profile_preserves_each_existing_access_basis_and_charge(world,basis,matched):
    from shared_check_authority.core import TRIAL_SECONDS
    a,e,_,row,change,clock=world
    if basis=='trial':
        change('authority','ACCESS',basis='trial',activationKind='explicit',activatedAtEpoch=clock[0],
            validFromEpoch=clock[0],validUntilEpoch=clock[0]+TRIAL_SECONDS)
        change('authority','PERIOD#p1',limit=10,startEpoch=clock[0],endEpoch=clock[0]+TRIAL_SECONDS)
    elif basis=='complimentary':
        change('authority','ACCESS',basis='complimentary',validUntilEpoch=None)
    def provider(body):
        r=result(body['checkId'])
        if matched:r.update(verdict='high_risk',processingOutcome='partial',coverage='limited',
            reasonCodes=['KNOWN_THREAT_MATCH'],threatTypes=['MALWARE'],lookupValidUntil='2027-01-15T08:01:00Z',
            matchScope='full_submitted_url',targetContactStatus='not_contacted',observedHopCount=0)
        return r
    service,e,calls=system(world,provider);proof=prepared(service,e)
    _,body=service.handle(event(e,'POST /v1/url-checks',request()|{'operationProof':proof}))
    charge=int(not matched and basis!='complimentary')
    assert body['accounting']['chargedChecks']==charge and body['access']['basis']==basis
    assert row('PERIOD#p1')['usedChecks']==charge and row('PERIOD#p1')['reservedChecks']==0
    assert len(calls)==1

@pytest.mark.parametrize('url,reason',[('https://example.com/reset','SENSITIVE_URL_BLOCKED'),
    ('http://127.0.0.1/','DESTINATION_BLOCKED'),('http://169.254.169.254/','DESTINATION_BLOCKED')])
def test_initial_url_policy_rejection_settles_exact_operation_and_releases_reservation(world,url,reason):
    import sys
    sys.path.insert(0,str(ROOT/'src/url_assessment'))
    from guarded_service import assess
    from assessment_service import Budget
    class NoWork:
        provider_call_count=0
        def validate_destination(self,*_):pytest.fail('DNS forbidden for local rejection')
        def lookup(self,*_):pytest.fail('provider forbidden for local rejection')
        def step(self,*_):pytest.fail('target forbidden for local rejection')
    seen=[]
    def provider(body):
        value=assess(body,NoWork(),Budget(18));seen.append(value);return value
    service,e,calls=system(world,provider);a,_,_,row,_,_=world
    req=request();req['target']['url']=url
    status,body=service.handle(event(e,'POST /v1/url-checks/prepare',req));assert status==200
    proof=body['operationProof']
    status,body=service.handle(event(e,'POST /v1/url-checks',req|{'operationProof':proof}))
    assert status==200 and body['state']=='settled' and body['accounting']['chargedChecks']==0
    assert body['accounting']['requiresReconciliation'] is False
    assert reason in body['outcome']['reasonCodes'] and seen[0]['checkId']=='client-1'
    assert row('PERIOD#p1')['reservedChecks']==0 and row('PERIOD#p1')['usedChecks']==0
    assert row('CHECK#'+proof)['state']=='SETTLED' and len(calls)==1
    assert seen[0]['lookupCount']==seen[0]['providerCallCount']==seen[0]['observedHopCount']==0
    assert service.handle(event(e,'POST /v1/url-checks/reconcile',
        {'transportVersion':VERSION,'checkId':'client-1','operationProof':proof}))[1]['accounting']==body['accounting']
    assert len(calls)==1


def test_retired_private_profile_releases_previously_admitted_candidate_two_operation(world,monkeypatch):
    import importlib.util
    from shared_check_authority.core import TrustedWorkerContext
    a,e,_,row,_,_=world;payload=deepcopy(PAYLOAD)|{'urlTransportVersion':'1.0.0-candidate.2'}
    proof=a.prepare(e,payload,'pre-migration',client_check_id='pre-migration')
    admitted=a.admit(e,payload,proof,client_check_id='pre-migration')
    spec=importlib.util.spec_from_file_location('retired_legacy_assessor',ROOT/'src/url_assessment/app.py')
    app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
    monkeypatch.setenv('STAGE','dev')
    from unittest.mock import Mock
    value=app.lambda_handler({'schemaVersion':1,'checkId':'pre-migration','url':payload['target']['url'],'scope':'full_url'},Mock(get_remaining_time_in_millis=lambda:35000))
    assert value['checkId']=='pre-migration' and value['providerCallCount']==value['observedHopCount']==0
    settled=a.settle(TrustedWorkerContext(ACCOUNT),proof,admitted['executionToken'],value['processingOutcome'],result_summary=value)
    assert settled['chargedChecks']==0 and row('PERIOD#p1')['reservedChecks']==0
    service=Consumer(a,mapper,lambda _:pytest.fail('no provider replay'),lambda _:None)
    status,body=service.handle(event(e,'POST /v1/url-checks/reconcile',
        {'transportVersion':'1.0.0-candidate.2','checkId':'pre-migration','operationProof':proof}))
    assert status==200 and body['state']=='settled' and body['accounting']['chargedChecks']==0
