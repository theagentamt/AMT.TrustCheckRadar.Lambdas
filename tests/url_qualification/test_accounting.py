"""Real Authority + Moto, original proofs, conservative counters and rollback."""
import os
import sys
import json
import importlib.util
from pathlib import Path
from copy import deepcopy
from dataclasses import replace

import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Isolated Moto environment required',allow_module_level=True)
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests/shared_check_authority'),str(Path(__file__).parent)]
from test_transactions import world, PAYLOAD, WORKER
from test_consumer import system, event, request, prepared, mapper
from test_scoped import make_table, NOW
from shared_url_qualification import Policy
from shared_url_qualification.store import Store
from shared_url_qualification.cleanup import sweep
from shared_check_authority.core import AuthorityError


def scoped(world):
    a,e,*_=world
    make_table(a.ddb)
    sweep(a.ddb,'qualification',NOW)
    policy=Policy(str(NOW-1)+'_accounting',NOW-1,NOW+3599,{'safe':PAYLOAD['target']['url']},'qualification',True,True,True,a.now)
    service,e,calls=system(world)
    service.qualification=Store(policy,a.ddb)
    return service,e,calls


def test_actual_receipt_transaction_ten_new_replay_and_failure_do_not_spend(world):
    a=world[0]
    service,e,calls=scoped(world)
    requests=[]
    for index in range(10):
        proof=prepared(service,e,'check-'+str(index))
        req=request('check-'+str(index))|{'operationProof':proof}
        status,body=service.handle(event(e,'POST /v1/url-checks',req))
        assert status==200 and body['accounting']['chargedChecks']==1
        requests.append(req)
    for req in requests:
        status,body=service.handle(event(e,'POST /v1/url-checks',req))
        assert status==200 and body['accounting']['chargedChecks']==1
    budget=service.qualification.table.get_item(Key=service.qualification.key('BUDGET'))['Item']
    assert budget['newAdmissions']==10 and len(calls)==10
    proof=prepared(service,e,'eleventh')
    status,body=service.handle(event(e,'POST /v1/url-checks',request('eleventh')|{'operationProof':proof}))
    assert status==503 and body['accounting']['chargedChecks'] is None
    assert world[3]('PERIOD#p1')['reservedChecks']==0 and world[3]('PERIOD#p1')['usedChecks']==10
    assert len(calls)==10


def test_account_device_conflict_checks_rollback_global_cap_atomically(world):
    service,e,calls=scoped(world)
    proof=prepared(service,e)
    world[4]('devices','ACTIVE_BINDING',stateVersion=2,bindingFingerprint='different')
    status,body=service.handle(event(e,'POST /v1/url-checks',request()|{'operationProof':proof}))
    assert status==403 and not calls
    assert 'Item' not in service.qualification.table.get_item(Key=service.qualification.key('BUDGET'))
    assert world[3]('PERIOD#p1')['reservedChecks']==0


def test_uncertain_committed_admission_never_replays_provider_or_double_counts(world,monkeypatch):
    a=world[0]
    service,e,calls=scoped(world)
    proof=prepared(service,e)
    original=a._transact
    def committed_but_lost(items):
        original(items)
        if any(action.get('Put',{}).get('Item',{}).get('recordType')=='V1_CHECK_RECEIPT' for action in items):
            raise AuthorityError('TRANSACTION_UNCERTAIN')
    monkeypatch.setattr(a,'_transact',committed_but_lost)
    req=request()|{'operationProof':proof}
    assert service.handle(event(e,'POST /v1/url-checks',req))[1]['state']=='pending'
    assert service.handle(event(e,'POST /v1/url-checks',req))[1]['state']=='pending'
    assert not calls and world[3]('PERIOD#p1')['reservedChecks']==1
    assert service.qualification.table.get_item(Key=service.qualification.key('BUDGET'))['Item']['newAdmissions']==1
    world[5][0]+=121
    e['requestContext']['authorizer']['jwt']['claims']['exp']=str(world[5][0]+90)
    reconciled=service.handle(event(e,'POST /v1/url-checks/reconcile',{'transportVersion':request()['transportVersion'],'checkId':'client-1','operationProof':proof}))[1]
    assert reconciled['state']=='settled' and reconciled['accounting']['chargedChecks']==0
    assert world[3]('PERIOD#p1')['reservedChecks']==0


def test_http_rollback_reconciles_owning_proof_without_window_retention_device_trial(world,monkeypatch):
    service,e,calls=scoped(world)
    proof=prepared(service,e)
    service.handle(event(e,'POST /v1/url-checks',request()|{'operationProof':proof}))
    world[4]('devices','ACTIVE_BINDING',stateVersion=2,bindingFingerprint='different')
    world[4]('authority','ACCESS',state='INACTIVE')
    import url_consumer.service as implementation
    monkeypatch.setitem(sys.modules,'service',implementation)
    spec=importlib.util.spec_from_file_location('scoped_rollback_http',ROOT/'src/url_consumer/app.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    import shared_url_qualification as qualification
    import shared_check_authority.runtime as runtime
    monkeypatch.setattr(qualification,'require_subject',lambda _:None)
    monkeypatch.setattr(qualification,'load_policy',lambda:pytest.fail('rollback required active window/retention'))
    monkeypatch.setattr(runtime,'load_authority',lambda:world[0])
    monkeypatch.setattr(module,'_mapper',lambda:mapper)
    for key,value in {'STAGE':'dev','CONSUMER_ENABLED':'false','URL_QUALIFICATION_ENABLED':'false',
        'URL_NEW_ADMISSION_ENABLED':'false','URL_PROVIDER_ENABLED':'false','URL_RETENTION_APPROVED':'false',
        'URL_RECONCILIATION_ENABLED':'true'}.items():monkeypatch.setenv(key,value)
    response=module.lambda_handler(event(e,'POST /v1/url-checks/reconcile',{'transportVersion':request()['transportVersion'],'checkId':'client-1','operationProof':proof}),None)
    body=json.loads(response['body'])
    assert response['statusCode']==200 and body['accounting']['chargedChecks']==1
    assert len(calls)==1
    wrong=deepcopy(e);wrong['requestContext']['authorizer']['jwt']['claims']['sub']='other-synthetic'
    response=module.lambda_handler(event(wrong,'POST /v1/url-checks/reconcile',{'transportVersion':request()['transportVersion'],'checkId':'client-1','operationProof':proof}),None)
    assert json.loads(response['body'])['accounting']['chargedChecks'] is None


def test_provider_control_closed_after_admission_preserves_accounting_without_invocation(world):
    service,e,calls=scoped(world)
    service.qualification.p=replace(service.qualification.p,provider=False)
    proof=prepared(service,e)
    status,body=service.handle(event(e,'POST /v1/url-checks',request()|{'operationProof':proof}))
    assert status==200 and body['accounting']['chargedChecks']==0 and not calls
    assert world[3]('PERIOD#p1')['reservedChecks']==0
    assert service.qualification.table.get_item(Key=service.qualification.key('BUDGET'))['Item']['newAdmissions']==1
