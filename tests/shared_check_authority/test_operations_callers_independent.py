"""Independent authenticated caller boundaries; synthetic local Moto only."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0,str(Path(__file__).parent))
from test_transactions import ACCOUNT,PAYLOAD,world
from test_operations_retirement import history_receipt
from test_consumer import system,prepared,event as http_event,request
from shared_check_authority.operations_callers import capture_authenticated


def ready(world,*,client='bound-client'):
    a,base,_,row,_,_=world
    a.s=replace(a.s,operations_producer_enabled=True)
    a.client.meta.config.connect_timeout=.2
    a.client.meta.config.read_timeout=.3
    proof=a.prepare(base,PAYLOAD,'bound-prepare',client_check_id=client)
    a.admit(base,PAYLOAD,proof,client_check_id=client)
    e=deepcopy(base)
    e.update(version='2.0',routeKey='POST /v1/url-checks',isBase64Encoded=False,rawQueryString='')
    e['requestContext']['http']={'method':'POST'}
    return a,e,proof


def call(a,e,proof,**extra):
    options={'family':'url_check','client_check_id':'bound-client',
             'transport_version':'1.0.0-candidate.1','remaining_ms':lambda:10000,'clock':lambda:10.0}
    options.update(extra)
    return capture_authenticated(a,e,proof,**options)


def test_body_identity_is_ignored_and_projection_never_exposed(world,capsys):
    a,e,proof=ready(world)
    e['body']=json.dumps({'account':'other-account','owner':'other-owner','worker':{'account':'other'}})
    result=call(a,e,proof)
    assert result=='CAPTURED' and type(result) is str
    stored=world[3]('CHECK#'+proof)
    assert stored['operationsProjection']['reference']==stored['operationsReceipt']
    assert capsys.readouterr().out==''


@pytest.mark.parametrize('mismatch',['client','version','family'])
def test_receipt_binding_refuses_cross_client_version_or_family(world,mismatch):
    a,e,proof=ready(world);options={}
    if mismatch=='client':options['client_check_id']='wrong-client'
    elif mismatch=='version':options['transport_version']='1.0.0-candidate.2'
    else:
        options.update(family='message_check',transport_version='1.0.0-message-candidate.1')
        e['routeKey']='POST /v1/message-checks'
    assert call(a,e,proof,**options)=='SOURCE_REJECTED'
    assert 'operationsProjection' not in world[3]('CHECK#'+proof)


@pytest.mark.parametrize('copy_receipt',[False,True])
def test_authorizer_owner_not_body_or_copied_row_selects_partition(world,copy_receipt):
    a,e,proof=ready(world);other='other-account'
    a.ddb.Table(a.s.users_table).put_item(Item={'PK':'USER#'+other,'SK':'PROFILE',
        'sub':other,'status':'ACTIVE','ageVerified':True})
    if copy_receipt:
        changed=world[3]('CHECK#'+proof);changed['PK']=a._partition(other,'k1')
        a.ddb.Table(a.s.authority_table).put_item(Item=changed)
    e['requestContext']['authorizer']['jwt']['claims']['sub']=other
    e['body']=json.dumps({'account':ACCOUNT})
    assert call(a,e,proof)=='SOURCE_REJECTED'
    assert 'operationsProjection' not in world[3]('CHECK#'+proof)


def test_missing_legacy_client_id_is_not_inferred_from_request(world):
    a,e,proof=ready(world,client=None)
    assert call(a,e,proof)=='SOURCE_REJECTED'
    assert 'operationsProjection' not in world[3]('CHECK#'+proof)


def test_too_little_remaining_time_skips_before_owner_reads(world,monkeypatch):
    a,e,proof=ready(world);calls=[]
    def unexpected(*args):calls.append(args);raise AssertionError('READ_NOT_ALLOWED')
    monkeypatch.setattr(a,'_account',unexpected)
    assert call(a,e,proof,remaining_ms=lambda:5999)=='TIME_SKIPPED'
    assert calls==[]


def test_monotonic_budget_exhausted_during_source_read_dispatches_no_mutation(world,monkeypatch):
    a,e,proof=ready(world);mono=[10.0];factory=a.ddb.Table;writes=[]
    def table(name):
        t=factory(name);get=t.get_item
        def read(**kwargs):
            result=get(**kwargs)
            if kwargs['Key']['SK']=='CHECK#'+proof:mono[0]=14.0
            return result
        t.get_item=read
        return t
    monkeypatch.setattr(a.ddb,'Table',table)
    monkeypatch.setattr(a.client,'transact_write_items',lambda **kwargs:writes.append(kwargs))
    assert call(a,e,proof,clock=lambda:mono[0])=='TIME_SKIPPED'
    assert writes==[]


@pytest.mark.parametrize('orphan',[False,True])
def test_authenticated_seen_expired_old_index_erases_without_remint(world,orphan):
    a,base,_,row,_,clock=world
    proof,before=history_receipt(world)
    a.client.meta.config.connect_timeout=.2;a.client.meta.config.read_timeout=.3
    if orphan:
        from shared_check_authority.operations_ingestion import ReceiptIngestion
        from test_transactions import WORKER
        ReceiptIngestion(a).capture(WORKER,proof)
    old=row('CHECK#'+proof)
    old['GSI1SK']=f'{int(old["expiresAt"]):012d}#{old["PK"]}#{old["SK"]}'
    if orphan:
        old.pop('operationsReceipt');old.pop('operationsTerminalReasonCode')
    a.ddb.Table(a.s.authority_table).put_item(Item=old)
    clock[0]=int(before['operationsReceipt']['expiresAtEpoch'])
    e=deepcopy(base);e.update(version='2.0',routeKey='POST /v1/url-checks/reconcile',rawQueryString='')
    e['requestContext']['http']={'method':'POST'}
    e['requestContext']['authorizer']['jwt']['claims']['exp']=str(clock[0]+90)
    assert call(a,e,proof,client_check_id='history',transport_version='1.0.0-candidate.2')=='EXPIRED'
    after=row('CHECK#'+proof)
    assert not {'operationsReceipt','operationsProjection','operationsTerminalReasonCode'} & set(after)
    assert after['governedHistory']==old['governedHistory'] and after['expiresAt']==old['expiresAt']
    assert row('PERIOD#p1')['usedChecks']==1


def test_capture_hook_exception_cannot_change_validated_business_envelope(world,monkeypatch):
    service,base,_=system(world)
    proof=prepared(service,base)
    service.handle(http_event(base,'POST /v1/url-checks',request()|{'operationProof':proof}))
    row=world[3]('CHECK#'+proof)
    e=http_event(base,'POST /v1/url-checks/reconcile',{'transportVersion':service.version,
        'checkId':'client-1','operationProof':proof})
    before=service.envelope(e,'client-1',proof,'settled',row=row)
    world[0].s=replace(world[0].s,operations_producer_enabled=True)
    calls=[]
    def broken(*args,**kwargs):calls.append(kwargs);raise RuntimeError('PRIVATE_SENTINEL')
    monkeypatch.setattr('shared_check_authority.operations_callers.capture_authenticated',broken)
    assert service.envelope(e,'client-1',proof,'settled',row=row)==before
    assert len(calls)==1 and calls[0]['family']=='url_check'
    assert world[3]('PERIOD#p1')['usedChecks']==1


@pytest.mark.parametrize('field',['clientCheckId','urlTransportVersion'])
def test_caller_capture_cas_rejects_bound_client_change_and_version_absence_insertion(world,monkeypatch,field):
    a,e,proof=ready(world);row=world[3];real=a.client.transact_write_items;calls=[]
    assert 'urlTransportVersion' not in row('CHECK#'+proof)
    def racing(**kwargs):
        calls.append(kwargs)
        changed=row('CHECK#'+proof)
        changed[field]='other-client' if field=='clientCheckId' else '1.0.0-candidate.1'
        a.ddb.Table(a.s.authority_table).put_item(Item=changed)
        return real(**kwargs)
    monkeypatch.setattr(a.client,'transact_write_items',racing)
    assert call(a,e,proof)=='CONFLICT'
    assert len(calls)==1 and 'operationsProjection' not in row('CHECK#'+proof)
    assert row('PERIOD#p1')['reservedChecks']==1 and row('PERIOD#p1')['usedChecks']==0


def test_caller_boundary_deletion_race_refuses_capture_after_valid_authentication(world,monkeypatch):
    a,e,proof=ready(world);put=world[2];row=world[3];real=a.client.transact_write_items;calls=[]
    def deleting(**kwargs):
        calls.append(kwargs);put('deletion','ACCOUNT_DELETION',state='DELETING')
        return real(**kwargs)
    monkeypatch.setattr(a.client,'transact_write_items',deleting)
    assert call(a,e,proof)=='CONFLICT'
    assert len(calls)==1 and 'operationsProjection' not in row('CHECK#'+proof)
    assert row('PERIOD#p1')['reservedChecks']==1 and row('PERIOD#p1')['usedChecks']==0
