"""Independent selective-retirement adversarial checks, local Moto only."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).parent))
from test_transactions import ACCOUNT,WORKER,AuthorityError,world,admit
from test_operations_retirement import history_receipt,helper,enable
from test_governed_history import url_payload
from shared_check_authority.expiry import Expiry
from shared_check_authority.recovery import Recovery
from shared_operations.receipts import RETENTION_SECONDS
from shared_check_authority.inventory import INVENTORY_KEY


@pytest.mark.parametrize('access',['DELETING','MISSING'])
def test_expiry_queue_still_erases_operations_when_full_row_delete_is_not_permitted(world,access):
    a,_,_,row,change,clock=world
    proof,before=history_receipt(world)
    pk=a._partition(ACCOUNT,'k1')
    clock[0]=int(before['expiresAt'])+1
    if access=='MISSING':
        a.ddb.Table('authority').delete_item(Key={'PK':pk,'SK':'ACCESS'})
    else:
        change('authority','ACCESS',state='DELETING')
    access_before=deepcopy(row('ACCESS'))
    result=Expiry(a.ddb,'authority',now=a.now).sweep()
    after=row('CHECK#'+proof)
    assert result.get('operationsRetired')==1
    assert 'operationsReceipt' not in after and 'operationsTerminalReasonCode' not in after
    assert after['governedHistory']==before['governedHistory']
    assert row('ACCESS')==access_before
    assert row('PERIOD#p1')['usedChecks']==1


def test_history_expiry_extension_race_cannot_reschedule_from_stale_snapshot(world,monkeypatch):
    a,_,_,row,_,clock=world
    proof,before=history_receipt(world)
    worker,pk=helper(world);clock[0]=int(before['operationsReceipt']['expiresAtEpoch'])
    original=worker.client.transact_write_items
    def extend(**kwargs):
        changed=deepcopy(before);changed['expiresAt']+=1;changed['retentionDeadlineEpoch']+=1
        a.ddb.Table('authority').put_item(Item=changed)
        return original(**kwargs)
    monkeypatch.setattr(worker.client,'transact_write_items',extend)
    with pytest.raises(AuthorityError,match='^OPERATIONS_RETIREMENT_TRANSACTION_UNCERTAIN$'):
        worker.retire(pk,'CHECK#'+proof)
    after=row('CHECK#'+proof)
    assert after['expiresAt']==before['expiresAt']+1
    assert after['operationsReceipt']==before['operationsReceipt']
    assert after['GSI1SK']==before['GSI1SK']


def test_settlement_race_with_reference_removal_cannot_recreate_operations_metadata(world,monkeypatch):
    a,_,_,row,_,_=world
    enable(a)
    proof,operation=admit(world)
    real=a._transact
    def removing(items):
        stored=row('CHECK#'+proof);stored.pop('operationsReceipt')
        a.ddb.Table('authority').put_item(Item=stored)
        return real(items)
    monkeypatch.setattr(a,'_transact',removing)
    with pytest.raises(AuthorityError):
        a.settle(WORKER,proof,operation['executionToken'],'complete')
    after=row('CHECK#'+proof)
    assert 'operationsReceipt' not in after and 'operationsTerminalReasonCode' not in after
    assert after['state']=='ADMITTED'
    assert row('PERIOD#p1')['usedChecks']==0 and row('PERIOD#p1')['reservedChecks']==1


def test_hmac_settlement_indexes_ops_deadline_when_receipt_retention_is_longer(world):
    a,_,_,row,_,clock=world
    enable(a)
    a.s=replace(a.s,receipt_retention_seconds=2*RETENTION_SECONDS,
                counter_retention_seconds=2*RETENTION_SECONDS)
    a.s.validate()
    proof,_=admit(world);before=row('CHECK#'+proof)
    operations_deadline=int(before['operationsReceipt']['expiresAtEpoch'])
    assert before['retentionDeadlineEpoch']>operations_deadline
    clock[0]+=121
    assert Recovery(a.ddb,'authority',now=a.now).sweep()['recovered']==1
    stored=row('CHECK#'+proof)
    assert stored['GSI1SK'].startswith(f'{operations_deadline:012d}#')
    clock[0]=operations_deadline
    result=Expiry(a.ddb,'authority',now=a.now).sweep()
    assert result.get('operationsRetired')==1
    after=row('CHECK#'+proof)
    assert 'operationsReceipt' not in after
    assert after['expiresAt']==before['retentionDeadlineEpoch']


@pytest.mark.parametrize('malformed',[{}, {'ResponseMetadata':{}}, {'ResponseMetadata':{'HTTPStatusCode':503}}])
def test_unacknowledged_retirement_write_never_retries_or_claims_success(world,monkeypatch,malformed):
    a,_,_,row,_,clock=world
    proof,before=history_receipt(world);worker,pk=helper(world)
    clock[0]=int(before['operationsReceipt']['expiresAtEpoch']);calls=[]
    def fail(**kwargs):calls.append(kwargs);return malformed
    monkeypatch.setattr(worker.client,'transact_write_items',fail)
    with pytest.raises(AuthorityError,match='^OPERATIONS_RETIREMENT_TRANSACTION_UNCERTAIN$'):
        worker.retire(pk,'CHECK#'+proof)
    assert len(calls)==1 and row('CHECK#'+proof)==before


@pytest.mark.parametrize('history',[False,True])
def test_paid_due_reservation_closure_accepts_reviewed_metadata_without_mutating_on_plan(world,history):
    a,event,_,row,_,clock=world
    enable(a,history=history)
    if history:
        # Isolate the pre-existing governed metadata compatibility obligation.
        a.s=replace(a.s,operations_producer_enabled=False)
        payload=url_payload()
        proof=a.prepare(event,payload,'privacy',client_check_id='privacy')
        a.admit(event,payload,proof,client_check_id='privacy')
    else:
        proof,_=admit(world)
    from shared_check_authority import purchase_usage
    period=row('PERIOD#p1');usage=a._get('authority',period['purchaseUsageKey'])
    clock[0]=int(usage['expiresAt'])+1
    before=deepcopy(row('CHECK#'+proof))
    actions=purchase_usage.due_reservation_actions(a.ddb,'authority',period,usage,
                                                   period['endEpoch'],now=clock[0])
    assert any(action.get('Delete',{}).get('Key',{}).get('SK')=='CHECK#'+proof for action in actions)
    assert row('CHECK#'+proof)==before
    assert row('PERIOD#p1')['reservedChecks']==1 and row('PERIOD#p1')['usedChecks']==0


def test_unacknowledged_inventory_read_does_not_dispatch_retirement(world,monkeypatch):
    a,_,_,row,_,clock=world
    proof,before=history_receipt(world);worker,pk=helper(world)
    clock[0]=int(before['operationsReceipt']['expiresAtEpoch'])
    table=a.ddb.Table('authority');original=table.get_item
    def invalid_inventory(**kwargs):
        result=original(**kwargs)
        if kwargs['Key']==INVENTORY_KEY:
            result.pop('ResponseMetadata',None)
        return result
    monkeypatch.setattr(table,'get_item',invalid_inventory)
    monkeypatch.setattr(a.ddb,'Table',lambda name:table)
    calls=[]
    def write(**kwargs):calls.append(kwargs);raise AssertionError('UNEXPECTED_WRITE')
    monkeypatch.setattr(worker.client,'transact_write_items',write)
    with pytest.raises(AuthorityError):worker.retire(pk,'CHECK#'+proof)
    assert calls==[]
