"""Fresh verified shortening closes accounting atomically, without new retention."""
import os
import sys
from pathlib import Path
from dataclasses import replace
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('isolated authority SDK suite', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).parent))
from test_transactions import world, ACCOUNT, WORKER, PAYLOAD, admit, failure
from test_entitlements import writer_world
from test_deletion import setup, finish, receipt
from shared_check_authority import purchase_usage as usage
from shared_check_authority.recovery import Recovery
from shared_check_authority.core import AuthorityError


def funded(fixture):
    writer, w, decisions, _ = fixture
    a, e, _, row, _, clock = w
    decisions[0] = replace(decisions[0], period_start_epoch=clock[0]-700000)
    writer.synchronize_paid(e, 'reference', 'fund')
    period = row('PERIOD#'+row('ACCESS')['periodId'])
    return writer, w, decisions, period


def revoke(writer, w, decisions, end, op='revoke'):
    decisions[0] = replace(decisions[0], source_revision=decisions[0].source_revision+1,
                           active=False, verified_at_epoch=w[5][0], access_until_epoch=end)
    return writer.synchronize_paid(w[1], 'reference', op)


@pytest.mark.parametrize('outcome,charge', [('complete',1), ('partial',0), ('failed',0)])
def test_pending_future_shortening_keeps_complete_only_settlement(writer_world, outcome, charge):
    writer,w,decisions,period = funded(writer_world)
    a,e,_,row,_,clock=w
    proof,admission=admit(w)
    end=clock[0]-1
    revoke(writer,w,decisions,end)
    assert row('ACCESS')['state']=='INACTIVE'
    failure('EXTERNAL_ACCESS_UNAVAILABLE',lambda:a.prepare(e,PAYLOAD,'after-revoke'))
    result=a.settle(WORKER,proof,admission['executionToken'],outcome)
    assert result['chargedChecks']==charge
    assert a.settle(WORKER,proof,admission['executionToken'],'complete')==result
    local=row(period['SK']);glob=a._get('authority',period['purchaseUsageKey'])
    assert local['reservedChecks']==glob['reservedChecks']==0
    assert local['usedChecks']==glob['usedChecks']==charge
    assert glob['expiresAt']==end+604800 and local['endEpoch']==period['endEpoch']


def test_pending_shortening_then_lease_recovery_zero_charge(writer_world):
    writer,w,decisions,period=funded(writer_world)
    a,_,_,row,_,clock=w
    proof,_=admit(w)
    revoke(writer,w,decisions,clock[0]-1)
    clock[0]+=121
    assert Recovery(a.ddb,'authority',now=a.now).expire(period['PK'],proof)
    assert row(period['SK'])['reservedChecks']==0
    assert a._get('authority',period['purchaseUsageKey'])['usedChecks']==0


def test_already_due_shortening_closes_pending_atomically_and_replays(writer_world):
    writer,w,decisions,period=funded(writer_world)
    a,_,_,row,_,clock=w
    proof,admission=admit(w)
    result=revoke(writer,w,decisions,clock[0]-604800)
    assert writer.synchronize_paid(w[1],'reference','revoke')==result
    assert row('ACCESS')['state']=='INACTIVE'
    assert a._get('authority',period['purchaseUsageKey']) is None
    assert row(period['SK'])['reservedChecks']==0 and row('INFLIGHT')['activeCount']==0
    settled=row('CHECK#'+proof)
    assert settled['state']=='SETTLED' and settled['chargedChecks']==0 and settled['processingOutcome']=='failed'
    assert settled['expiresAt']==settled['retentionDeadlineEpoch']
    # A late provider completion cannot re-charge a forcibly closed receipt.
    assert a.settle(WORKER,proof,admission['executionToken'],'complete')['chargedChecks']==0


@pytest.mark.parametrize('race', ['receipt','global','period','inflight','deletion'])
def test_due_closure_race_rolls_back_all_other_writes(writer_world,monkeypatch,race):
    writer,w,decisions,period=funded(writer_world)
    a,_,put,row,_,clock=w
    proof,_=admit(w)
    access=row('ACCESS');original=a.client.transact_write_items
    def changed(**kwargs):
        if race=='deletion':put('deletion','ACCOUNT_DELETION',status='REQUESTED')
        else:
            key={'PK':period['PK'],'SK':{'receipt':'CHECK#'+proof,'period':period['SK'],'inflight':'INFLIGHT'}.get(race,'')}
            if race=='global':key=period['purchaseUsageKey']
            value=a._get('authority',key)
            if race=='receipt':value['executionToken']='f'*32
            else:
                field='activeCount' if race=='inflight' else 'usedChecks'
                value[field]+=1
            a.ddb.Table('authority').put_item(Item=value)
        return original(**kwargs)
    monkeypatch.setattr(a.client,'transact_write_items',changed)
    with pytest.raises(AuthorityError):revoke(writer,w,decisions,clock[0]-604800)
    assert row('ACCESS')==access
    assert row('CHECK#'+proof)['state']=='ADMITTED'
    assert a._get('authority',period['purchaseUsageKey'])['reservedChecks']==1
    assert row(period['SK'])['reservedChecks']==1


@pytest.mark.parametrize('kind',['missing','extra-field','unknown-scope','bad-counter'])
def test_due_closure_incomplete_or_unknown_evidence_preserved(writer_world,kind):
    writer,w,decisions,period=funded(writer_world)
    a,_,_,row,_,clock=w
    proof,_=admit(w)
    key={'PK':period['PK'],'SK':'CHECK#'+proof}
    value=row(key['SK'])
    if kind=='missing':a.ddb.Table('authority').delete_item(Key=key)
    elif kind=='bad-counter':a.ddb.Table('authority').put_item(Item=row('INFLIGHT')|{'activeCount':0})
    else:a.ddb.Table('authority').put_item(Item=value|({'unknown':True} if kind=='extra-field' else {'projectionScope':'unknown'}))
    before=a.ddb.Table('authority').scan()['Items']
    failure('PURCHASE_USAGE_RECONCILIATION_REQUIRED',lambda:revoke(writer,w,decisions,clock[0]-604800))
    assert a.ddb.Table('authority').scan()['Items']==before


def test_zero_reserved_old_period_deletion_does_not_read_or_mutate_new_owner_usage(world,monkeypatch):
    a,_,_,row,_,_=world
    period=row('PERIOD#p1');pointer=period['purchaseUsageKey']
    newer=a._get('authority',pointer)|{'usedChecks':123,'reservedChecks':2}
    a.ddb.Table('authority').put_item(Item=newer)
    bridge,command,_=setup(world)
    assert finish(bridge,command)['complete']
    assert a._get('authority',pointer)==newer and receipt(a)


def test_due_closure_paginates_all_pending_and_preserves_other_period_inflight(writer_world,monkeypatch):
    writer,w,decisions,period=funded(writer_world)
    a,_,_,row,_,clock=w
    proofs=[admit(w,'check-'+str(i))[0] for i in range(3)]
    # One unrelated reservation is not part of this funded-period closure.
    a.ddb.Table('authority').put_item(Item=row('INFLIGHT')|{'activeCount':4})
    factory=a.ddb.Table;seen=[]
    def table(name):
        result=factory(name)
        if name=='authority':
            query=result.query
            def paginated(**kwargs):
                seen.append(kwargs.get('ConsistentRead'))
                return query(**(kwargs|{'Limit':1}))
            result.query=paginated
        return result
    monkeypatch.setattr(a.ddb,'Table',table)
    revoke(writer,w,decisions,clock[0]-604800)
    assert 3 <= len(seen) <= 8 and all(seen)
    assert row('INFLIGHT')['activeCount']==1
    assert all(row('CHECK#'+proof)['chargedChecks']==0 for proof in proofs)


def test_due_closure_query_budget_exhaustion_preserves_all_rows(writer_world,monkeypatch):
    writer,w,decisions,period=funded(writer_world)
    a,_,_,row,_,clock=w
    proof,_=admit(w)
    before=a.ddb.Table('authority').scan()['Items'];factory=a.ddb.Table;calls=[]
    def table(name):
        result=factory(name)
        if name=='authority':
            def endless(**kwargs):
                calls.append(1)
                return {'Items':[], 'LastEvaluatedKey':{'PK':period['PK'],'SK':'CHECK#cursor'}}
            result.query=endless
        return result
    monkeypatch.setattr(a.ddb,'Table',table)
    failure('PURCHASE_USAGE_RECONCILIATION_REQUIRED',lambda:revoke(writer,w,decisions,clock[0]-604800))
    assert len(calls)==8 and a.ddb.Table('authority').scan()['Items']==before


def test_due_closure_deletes_expired_receipts_without_new_retention(writer_world):
    writer,w,decisions,period=funded(writer_world)
    a,e,_,row,_,clock=w
    proof,_=admit(w);pending=row('CHECK#'+proof)
    clock[0]=int(pending['retentionDeadlineEpoch'])+1
    e['requestContext']['authorizer']['jwt']['claims']['exp']=str(clock[0]+90)
    revoke(writer,w,decisions,int(period['startEpoch']))
    assert row('CHECK#'+proof) is None
    assert row(period['SK'])['reservedChecks']==0 and row('INFLIGHT')['activeCount']==0
    assert a._get('authority',period['purchaseUsageKey']) is None


@pytest.mark.parametrize('scope,extra', [
    ('origin_only',{}),
    ('sanitized_message',{}),
    ('sanitized_message',{'messageTransportVersion':'1.0.0-message-candidate.2'}),
    ('recovery_clarification',{'recoveryTransportVersion':'1.0.0-recovery-candidate.1'}),
])
def test_due_closure_supported_projection_has_only_failed_usage(writer_world,scope,extra):
    from shared_recovery_contract.constants import VERSION
    writer,w,decisions,period=funded(writer_world)
    a,_,_,row,_,clock=w
    proof,_=admit(w)
    pending=row('CHECK#'+proof)|{'clientCheckId':'client-check','projectionScope':scope}|extra
    if scope=='recovery_clarification':pending['recoveryTransportVersion']=VERSION
    a.ddb.Table('authority').put_item(Item=pending)
    revoke(writer,w,decisions,clock[0]-604800)
    settled=row('CHECK#'+proof)
    assert settled['chargedChecks']==0 and settled['processingOutcome']=='failed'
    if scope=='recovery_clarification':
        assert settled['resultSummary']['contentDisposition']=='not_retained'
    else:assert 'resultSummary' not in settled


def test_due_closure_lost_commit_response_reconciles_without_reapplying(writer_world,monkeypatch):
    writer,w,decisions,period=funded(writer_world)
    a,_,_,row,_,clock=w
    proof,_=admit(w)
    transact=a.client.transact_write_items
    def lose(**kwargs):
        transact(**kwargs)
        raise TimeoutError('synthetic response loss')
    monkeypatch.setattr(a.client,'transact_write_items',lose)
    result=revoke(writer,w,decisions,clock[0]-604800)
    assert result['state']=='INACTIVE'
    assert row('CHECK#'+proof)['chargedChecks']==0 and row('INFLIGHT')['activeCount']==0
