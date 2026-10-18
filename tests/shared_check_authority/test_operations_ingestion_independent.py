"""Independent trusted-source races and closed projection tests, local Moto only."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).parent))
from test_transactions import ACCOUNT,WORKER,AuthorityError,world
from test_operations_ingestion import fresh
from test_operations_paid_closure import pending,writer_world
from test_purchase_shortening import revoke
from shared_check_authority.operations_ingestion import ReceiptIngestion
from shared_check_authority.operations_retirement import OperationsRetirement
from shared_operations.projection import validate_capture
from shared_operations.receipts import OperationsReceiptFault


def test_replay_revalidates_deletion_at_transaction_boundary(world,monkeypatch):
    _,_,put,row,_,_=world
    ingest,proof,_=fresh(world)
    ingest.capture(WORKER,proof)
    before=deepcopy(row('CHECK#'+proof));real=ingest.client.transact_write_items;calls=[]
    def deleting(**kwargs):
        calls.append(kwargs);put('deletion','ACCOUNT_DELETION',state='DELETING')
        return real(**kwargs)
    monkeypatch.setattr(ingest.client,'transact_write_items',deleting)
    with pytest.raises(AuthorityError,match='^OPERATIONS_INGESTION_CONFLICT$'):
        ingest.capture(WORKER,proof)
    assert len(calls)==1 and row('CHECK#'+proof)==before


def test_unknown_replay_response_is_not_revalidated_or_automatically_retried(world,monkeypatch):
    _,_,_,row,_,_=world
    ingest,proof,_=fresh(world)
    ingest.capture(WORKER,proof)
    before=deepcopy(row('CHECK#'+proof));real=ingest.client.transact_write_items;calls=[]
    def lost(**kwargs):
        calls.append(kwargs);real(**kwargs);raise TimeoutError('PRIVATE_SERVICE_DETAIL')
    monkeypatch.setattr(ingest.client,'transact_write_items',lost)
    with pytest.raises(AuthorityError,match='^OPERATIONS_INGESTION_WRITE_UNKNOWN$'):
        ingest.capture(WORKER,proof)
    assert len(calls)==1 and row('CHECK#'+proof)==before


def test_old_accepted_capture_cannot_overwrite_new_authoritative_terminal_capture(world,monkeypatch):
    a,_,_,row,_,_=world
    ingest,proof,operation=fresh(world)
    ingest.capture(WORKER,proof)
    real=ingest.client.transact_write_items
    def terminal(**kwargs):
        monkeypatch.setattr(ingest.client,'transact_write_items',real)
        a.settle(WORKER,proof,operation['executionToken'],'complete')
        assert ingest.capture(WORKER,proof)['projection']['sourceState']=='SETTLED'
        return real(**kwargs)
    monkeypatch.setattr(ingest.client,'transact_write_items',terminal)
    with pytest.raises(AuthorityError,match='^OPERATIONS_INGESTION_CONFLICT$'):
        ingest.capture(WORKER,proof)
    captured=row('CHECK#'+proof)['operationsProjection']
    assert captured['sourceState']=='SETTLED' and captured['events'][1]['chargedChecks']==1
    assert row('PERIOD#p1')['usedChecks']==1


def test_inflight_capture_cannot_restore_selectively_retired_references(world,monkeypatch):
    a,_,_,row,_,clock=world
    ingest,proof,_=fresh(world)
    deadline=int(row('CHECK#'+proof)['operationsReceipt']['expiresAtEpoch'])
    real=ingest.client.transact_write_items
    def retiring(**kwargs):
        monkeypatch.setattr(ingest.client,'transact_write_items',real)
        clock[0]=deadline
        assert OperationsRetirement(a.ddb,'authority',now=a.now).retire(a._partition(ACCOUNT,'k1'),'CHECK#'+proof)
        return real(**kwargs)
    monkeypatch.setattr(ingest.client,'transact_write_items',retiring)
    with pytest.raises(AuthorityError,match='^OPERATIONS_INGESTION_CONFLICT$'):
        ingest.capture(WORKER,proof)
    assert not {'operationsReceipt','operationsProjection'} & set(row('CHECK#'+proof))
    assert row('PERIOD#p1')['usedChecks']==0 and row('PERIOD#p1')['reservedChecks']==1


def test_profile_age_revocation_race_cannot_ingest_facts(world,monkeypatch):
    a,_,_,row,_,_=world
    ingest,proof,_=fresh(world);real=ingest.client.transact_write_items
    def revoked(**kwargs):
        a.ddb.Table(a.s.users_table).update_item(Key={'PK':'USER#'+ACCOUNT,'SK':'PROFILE'},
            UpdateExpression='SET ageVerified = :no',ExpressionAttributeValues={':no':False})
        return real(**kwargs)
    monkeypatch.setattr(ingest.client,'transact_write_items',revoked)
    with pytest.raises(AuthorityError,match='^OPERATIONS_INGESTION_CONFLICT$'):
        ingest.capture(WORKER,proof)
    assert 'operationsProjection' not in row('CHECK#'+proof)


@pytest.mark.parametrize('part',['accepted-private','terminal-private','reference-private','altered-outcome'])
def test_stored_projection_closed_schema_rejects_hidden_or_unjoined_fields(world,part):
    a,_,_,_,_,_=world
    ingest,proof,operation=fresh(world)
    a.settle(WORKER,proof,operation['executionToken'],'complete')
    captured=ingest.capture(WORKER,proof)['projection']
    if part=='accepted-private':captured['events'][0]['accountId']='PRIVATE_SENTINEL'
    elif part=='terminal-private':captured['events'][1]['providerResponse']='PRIVATE_SENTINEL'
    elif part=='reference-private':captured['reference']['subject']='PRIVATE_SENTINEL'
    else:captured['events'][1]['processingOutcome']='failed'
    with pytest.raises(OperationsReceiptFault,match='^OPERATIONS_RECEIPT_INVALID$'):
        validate_capture(captured)


def test_returned_capture_cannot_mutate_durable_snapshot(world):
    _,_,_,row,_,_=world
    ingest,proof,_=fresh(world)
    returned=ingest.capture(WORKER,proof)
    before=deepcopy(row('CHECK#'+proof)['operationsProjection'])
    returned['projection']['reference']['logicalCheckRef']='lc1_'+'e'*32
    returned['projection']['events'][0]['reasonCode']='PRIVATE_SENTINEL'
    assert row('CHECK#'+proof)['operationsProjection']==before
    assert ingest.capture(WORKER,proof)['projection']==before


def test_paid_closure_does_not_drop_a_racing_accepted_snapshot(writer_world,monkeypatch):
    writer,w,decisions,period,proof,_=pending(writer_world)
    a,_,_,row,_,clock=w
    ingest=ReceiptIngestion(a);real=a.client.transact_write_items
    def capture_first(**kwargs):
        monkeypatch.setattr(a.client,'transact_write_items',real)
        ingest.capture(WORKER,proof)
        return real(**kwargs)
    monkeypatch.setattr(a.client,'transact_write_items',capture_first)
    with pytest.raises(AuthorityError):revoke(writer,w,decisions,clock[0]-7*86400)
    after=row('CHECK#'+proof)
    assert after['state']=='ADMITTED' and after['operationsProjection']['sourceState']=='ADMITTED'
    assert row(period['SK'])['reservedChecks']==1 and row('INFLIGHT')['activeCount']==1


def test_unexpired_orphan_capture_cannot_be_used_to_authorize_paid_closure(writer_world):
    writer,w,decisions,period,proof,_=pending(writer_world)
    a,_,_,row,_,clock=w
    ReceiptIngestion(a).capture(WORKER,proof)
    changed=row('CHECK#'+proof);changed.pop('operationsReceipt')
    a.ddb.Table('authority').put_item(Item=changed)
    with pytest.raises(AuthorityError,match='^PURCHASE_USAGE_RECONCILIATION_REQUIRED$'):
        revoke(writer,w,decisions,clock[0]-7*86400)
    assert row('CHECK#'+proof)==changed and row(period['SK'])['reservedChecks']==1
