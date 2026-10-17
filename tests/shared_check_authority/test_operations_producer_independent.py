"""Independent source-boundary checks; actual accounting uses local Moto only."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest
from jsonschema import Draft202012Validator

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(Path(__file__).parent))
from test_transactions import ACCOUNT,PAYLOAD,WORKER,AuthorityError,world,admit
from shared_operations.receipts import admission_fields,receipt_events,OperationsReceiptFault

SCHEMA=Draft202012Validator(json.loads((ROOT/'src/operations_reporting/event.schema.json').read_text()))


def enabled(world):
    a=world[0];a.s=replace(a.s,operations_producer_enabled=True);return a


@pytest.mark.parametrize('source',[None,'unknown','free-form-private',[],{}])
def test_unclassified_message_source_cannot_produce_schema_invalid_admission(source):
    with pytest.raises(OperationsReceiptFault,match='^OPERATIONS_RECEIPT_INVALID$'):
        admission_fields({'entryPoint':'message','target':{'sourceType':source}},
                         accepted_at=10,receipt_deadline=100,access_class='paid',environment='dev')


@pytest.mark.parametrize('source',['pasted_text','ocr','mixed'])
def test_every_valid_message_source_produces_contract_valid_facts(source):
    metadata=admission_fields({'entryPoint':'message','target':{'sourceType':source}},
                             accepted_at=10,receipt_deadline=100,access_class='paid',environment='dev')
    projected=receipt_events({'recordType':'V1_CHECK_RECEIPT','state':'ADMITTED','basis':'paid',**metadata},now_epoch=11)
    assert projected['coverage']=='ACCEPTED_ONLY'
    SCHEMA.validate(projected['events'][0])


def test_lost_admission_response_uses_committed_refs_without_second_reservation(world,monkeypatch):
    a=enabled(world);_,event,_,row,_,clock=world
    proof=a.prepare(event,PAYLOAD,'lost-ops')
    real=a._transact;calls=[]
    def lose_after_commit(items):
        calls.append(True);real(items);raise AuthorityError('TRANSACTION_UNCERTAIN')
    monkeypatch.setattr(a,'_transact',lose_after_commit)
    returned=a.admit(event,PAYLOAD,proof,count_attempt=False)
    assert returned['admitted'] is False and len(calls)==1
    metadata=deepcopy(row('CHECK#'+proof)['operationsReceipt'])
    a.admit(event,PAYLOAD,proof,count_attempt=False)
    assert len(calls)==1 and row('CHECK#'+proof)['operationsReceipt']==metadata
    assert row('PERIOD#p1')['reservedChecks']==1
    public=json.dumps(returned,default=str)
    for ref in ['logicalCheckRef','acceptedEventId','terminalEventId',metadata['logicalCheckRef']]:assert ref not in public


def test_lost_settlement_response_preserves_committed_cause_and_exact_charge(world,monkeypatch):
    a=enabled(world);_,_,_,row,_,clock=world
    proof,op=admit(world);real=a._transact;calls=[]
    def lose_after_commit(items):
        calls.append(True);real(items);raise AuthorityError('TRANSACTION_UNCERTAIN')
    monkeypatch.setattr(a,'_transact',lose_after_commit)
    public=a.settle(WORKER,proof,op['executionToken'],'complete')
    before=deepcopy(row('CHECK#'+proof))
    assert public['chargedChecks']==row('PERIOD#p1')['usedChecks']==1 and len(calls)==1
    a.settle(WORKER,proof,op['executionToken'],'failed',operations_reason_code='SETTLED_FAILED')
    assert len(calls)==1 and row('CHECK#'+proof)==before
    terminal=receipt_events(before,now_epoch=clock[0])['events'][1]
    assert terminal['reasonCode']=='NONE' and terminal['chargedChecks']==1
    SCHEMA.validate(terminal)


def test_deletion_before_settlement_prevents_cause_or_accounting_write(world):
    a=enabled(world);_,_,put,row,_,_=world
    proof,op=admit(world);before=deepcopy(row('CHECK#'+proof))
    put('deletion','ACCOUNT_DELETION',state='DELETING')
    with pytest.raises(AuthorityError,match='ACCOUNT_UNAVAILABLE'):
        a.settle(WORKER,proof,op['executionToken'],'complete')
    assert row('CHECK#'+proof)==before and row('PERIOD#p1')['usedChecks']==0


def test_removed_captured_metadata_race_fails_atomic_settlement(world,monkeypatch):
    a=enabled(world);_,_,_,row,_,_=world
    proof,op=admit(world);real=a._transact
    def remove_metadata(items):
        current=row('CHECK#'+proof);current.pop('operationsReceipt')
        a.ddb.Table('authority').put_item(Item=current)
        return real(items)
    monkeypatch.setattr(a,'_transact',remove_metadata)
    with pytest.raises(AuthorityError):a.settle(WORKER,proof,op['executionToken'],'complete')
    assert row('CHECK#'+proof)['state']=='ADMITTED'
    assert row('PERIOD#p1')['usedChecks']==0 and row('PERIOD#p1')['reservedChecks']==1


def test_shorter_original_receipt_deadline_is_never_extended_by_projection(world):
    a=enabled(world);_,_,_,row,_,clock=world
    proof,_=admit(world);stored=row('CHECK#'+proof)
    deadline=int(stored['operationsReceipt']['expiresAtEpoch'])
    assert deadline==stored['retentionDeadlineEpoch']<clock[0]+7*86400
    assert receipt_events(stored,now_epoch=deadline-1)['coverage']=='ACCEPTED_ONLY'
    assert receipt_events(stored,now_epoch=deadline)=={'coverage':'EXPIRED','events':[]}
    assert receipt_events(stored,now_epoch=deadline+1)=={'coverage':'EXPIRED','events':[]}


def test_forged_post_settlement_accounting_cannot_be_projected(world):
    a=enabled(world);_,_,_,row,_,clock=world
    proof,op=admit(world)
    a.settle(WORKER,proof,op['executionToken'],'failed',operations_reason_code='SETTLED_FAILED')
    corrupt=row('CHECK#'+proof);corrupt['chargedChecks']=1
    with pytest.raises(OperationsReceiptFault,match='^OPERATIONS_RECEIPT_INVALID$'):
        receipt_events(corrupt,now_epoch=clock[0])


def test_hmac_recovery_without_operations_keeps_original_receipt_shape(world):
    a,_,_,row,_,clock=world
    proof,_=admit(world)
    clock[0]+=121
    from shared_check_authority.recovery import Recovery
    worker=Recovery(a.ddb,a.s.authority_table,now=lambda:clock[0])
    assert worker.expire(a._partition(ACCOUNT,'k1'),proof) is True
    stored=row('CHECK#'+proof)
    assert stored['state']=='SETTLED' and stored['chargedChecks']==0
    assert 'assessmentEpoch' not in stored
    assert 'operationsTerminalReasonCode' not in stored and 'operationsReceipt' not in stored
    assert row('PERIOD#p1')['reservedChecks']==row('PERIOD#p1')['usedChecks']==0


def test_hmac_recovery_at_operations_expiry_adds_no_terminal_operations_fact(world):
    a=enabled(world);_,_,_,row,_,clock=world
    proof,_=admit(world)
    metadata=deepcopy(row('CHECK#'+proof)['operationsReceipt'])
    clock[0]=int(metadata['expiresAtEpoch'])
    from shared_check_authority.recovery import Recovery
    worker=Recovery(a.ddb,a.s.authority_table,now=lambda:clock[0])
    assert worker.expire(a._partition(ACCOUNT,'k1'),proof) is True
    stored=row('CHECK#'+proof)
    assert 'operationsTerminalReasonCode' not in stored and 'assessmentEpoch' not in stored
    assert stored['operationsReceipt']==metadata
    assert receipt_events(stored,now_epoch=clock[0])=={'coverage':'EXPIRED','events':[]}
    assert row('PERIOD#p1')['reservedChecks']==row('PERIOD#p1')['usedChecks']==0


def test_lost_hmac_recovery_response_does_not_repeat_accounting_or_event(world,monkeypatch):
    a=enabled(world);_,_,_,row,_,clock=world
    proof,_=admit(world);clock[0]+=121
    from shared_check_authority.recovery import Recovery
    worker=Recovery(a.ddb,a.s.authority_table,now=lambda:clock[0])
    real=worker.client.transact_write_items;calls=[]
    def lost(**kwargs):
        calls.append(True);real(**kwargs);raise RuntimeError('PRIVATE_STORAGE_DETAIL')
    monkeypatch.setattr(worker.client,'transact_write_items',lost)
    with pytest.raises(AuthorityError,match='^RECOVERY_TRANSACTION_UNCERTAIN$'):
        worker.expire(a._partition(ACCOUNT,'k1'),proof)
    before=deepcopy(row('CHECK#'+proof))
    projected=receipt_events(before,now_epoch=clock[0])
    assert projected['coverage']=='CHECK_LIFECYCLE_RECORDED'
    for event in projected['events']:SCHEMA.validate(event)
    assert projected['events'][1]['reasonCode']=='OPERATION_EXPIRED'
    assert before['receiptId'] not in json.dumps(projected)
    assert worker.expire(a._partition(ACCOUNT,'k1'),proof) is False
    assert len(calls)==1 and row('CHECK#'+proof)==before
    assert row('PERIOD#p1')['reservedChecks']==row('PERIOD#p1')['usedChecks']==0


def test_hmac_recovery_account_deletion_race_aborts_accounting_and_terminal(world,monkeypatch):
    a=enabled(world);_,_,_,row,_,clock=world
    proof,_=admit(world);clock[0]+=121
    from shared_check_authority.recovery import Recovery
    worker=Recovery(a.ddb,a.s.authority_table,now=lambda:clock[0])
    real=worker.client.transact_write_items
    def deleting(**kwargs):
        access=row('ACCESS');access['state']='DELETING'
        a.ddb.Table(a.s.authority_table).put_item(Item=access)
        return real(**kwargs)
    monkeypatch.setattr(worker.client,'transact_write_items',deleting)
    with pytest.raises(AuthorityError,match='^RECOVERY_TRANSACTION_UNCERTAIN$'):
        worker.expire(a._partition(ACCOUNT,'k1'),proof)
    stored=row('CHECK#'+proof)
    assert stored['state']=='ADMITTED' and 'operationsTerminalReasonCode' not in stored
    assert row('PERIOD#p1')['reservedChecks']==1 and row('PERIOD#p1')['usedChecks']==0


@pytest.mark.parametrize('outcome,reason',[('complete','NONE'),('failed','SETTLED_FAILED'),
                                         ('unavailable','SERVICE_UNAVAILABLE'),('failed',None)])
def test_expired_receipt_id_exception_cannot_project_other_terminal_causes(world,outcome,reason):
    a=enabled(world);_,_,_,row,_,clock=world
    proof,op=admit(world)
    a.settle(WORKER,proof,op['executionToken'],outcome,operations_reason_code=reason)
    stored=row('CHECK#'+proof);stored['receiptId']='expired_'+'a'*32
    with pytest.raises(OperationsReceiptFault,match='^OPERATIONS_RECEIPT_INVALID$'):
        receipt_events(stored,now_epoch=clock[0])
