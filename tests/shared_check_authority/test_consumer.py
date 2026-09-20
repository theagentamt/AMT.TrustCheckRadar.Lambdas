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
from url_consumer.service import Consumer, VERSION

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('canonical_url_mapper',ROOT/'contracts/url-assessment/v1-draft/reference_mapping.py')
mapper=importlib.util.module_from_spec(spec);spec.loader.exec_module(mapper)


def event(base,route,body):
    e=deepcopy(base);e.update(version='2.0',routeKey=route,body=json.dumps(body),isBase64Encoded=False,rawQueryString='')
    e['requestContext']['http']={'method':'POST'}
    return e


def request(check='client-1'):
    return {'transportVersion':VERSION,'contractVersion':mapper.VERSION,'checkId':check,**deepcopy(PAYLOAD)}


def result(check='client-1'):
    return {'schemaVersion':1,'checkId':check,'verdict':'no_known_threat_detected','processingOutcome':'complete','coverage':'supported_checks_complete','reasonCodes':['NO_LIST_MATCH','BROWSER_NAVIGATION_NOT_EVALUATED'],'transportWarnings':[],'threatTypes':[],'lookupCount':1,'providerCallCount':1,'observedHopCount':1,'scope':'HTTP_REDIRECTS_AND_GOOGLE_LOOKUP','consumerAccessEnabled':False}


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
    def provider(body):return result(body['checkId'])|{'verdict':'high_risk','processingOutcome':'partial','coverage':'limited','reasonCodes':['KNOWN_THREAT_MATCH'],'threatTypes':['MALWARE']}
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
    a.admit(e,PAYLOAD,proof,client_check_id='client-1')
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
