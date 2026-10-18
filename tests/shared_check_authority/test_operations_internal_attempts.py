"""Actual Moto receipt-local internal attempts; no vendor dispatch or new retention."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import json
import sys
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_transactions import ACCOUNT, WORKER, TrustedWorkerContext, AuthorityError, world, admit
from test_operations_ingestion import fresh
from test_operations_producer import SCHEMA
from test_operations_callers import bounded_world, consumer, api_event
from shared_check_authority.operations_ingestion import ReceiptIngestion
from shared_check_authority.operations_retirement import OperationsRetirement
from shared_check_authority.inventory import INVENTORY_KEY
from shared_operations.projection import validate_capture
from shared_operations.attempts import duration_bucket
from shared_operations.receipts import OperationsReceiptFault
from operations_reporting.reducer import aggregate_day, day_bounds
from operations_reporting.revisions import validate_summary


def attempt_events(projection):
    return [e for e in projection['events'] if e['eventType'].startswith('attempt_')]


def start(w):
    ingest, proof, op = fresh(w)
    result = ingest.start_attempt(WORKER, proof, op['executionToken'])
    return ingest, proof, op, result


@pytest.mark.parametrize('family,component', [('url_check','url_assessment'),
    ('message_check','message_evaluator'),('recovery_clarification','recovery_evaluator')])
def test_actual_family_derived_internal_start_finish_and_aggregate_are_not_provider_cost(bounded_world, family, component):
    w=bounded_world;a,base,_,row,_,clock=w
    service,_,_,path,req=consumer(w,family)
    status, prepared=service.handle(api_event(base,'POST '+path+'/prepare',req))
    assert status==200
    payload={k:req[k]for k in ('entryPoint','language','target')}
    if family=='url_check':payload['urlTransportVersion']=req['transportVersion']
    if family=='recovery_clarification':payload['recoveryTransportVersion']=req['transportVersion']
    proof=prepared['operationProof'];op=a.admit(base,payload,proof,client_check_id=req['checkId'])
    before=deepcopy(row('CHECK#'+proof));ledger=deepcopy(row('PERIOD#p1'))
    ingest=ReceiptIngestion(a)
    first=ingest.start_attempt(WORKER,proof,op['executionToken']);projection=first['projection']
    assert projection['schemaVersion']==2 and len(projection['events'])==2
    begun=attempt_events(projection)[0]
    assert begun['component']==component and begun['attemptKind']=='internal_component' and begun['attemptOrdinal']==1
    assert begun['providerFamily'] is begun['providerRequestUnits'] is begun['estimatedCostMicrousd'] is None
    clock[0]+=2
    finished=ingest.finish_attempt(WORKER,proof,op['executionToken'],begun['attemptRef'],outcome='succeeded',duration_ms=1500)
    assert len(finished['projection']['events'])==3
    assert row('PERIOD#p1')==ledger and row('INFLIGHT')['activeCount']==1
    assert {k:v for k,v in row('CHECK#'+proof).items()if k!='operationsProjection'}==before
    # An internally valid result may still settle the logical check unavailable.
    extra = {}
    if family == 'recovery_clarification':
        from shared_recovery_contract.validation import outcome
        extra['result_summary'] = outcome(req['checkId'], 'unavailable', 'SERVICE_NOT_ENABLED')
    a.settle(WORKER,proof,op['executionToken'],'unavailable',operations_reason_code='SERVICE_UNAVAILABLE',**extra)
    final=ingest.capture(WORKER,proof)['projection']
    assert final['sourceState']=='SETTLED' and len(final['events'])==4
    assert final['events'][1]['processingOutcome']=='unavailable' and attempt_events(final)[1]['attemptOutcome']=='succeeded'
    assert attempt_events(final)==attempt_events(finished['projection'])
    for event in final['events']:SCHEMA.validate(event)
    day=datetime.fromtimestamp(clock[0],ZoneInfo('America/Chicago')).date().isoformat()
    summary=aggregate_day(final['events']*2,[],local_day=day,environment='dev',as_of_epoch=day_bounds(day)[1]+60)
    validate_summary(summary)
    assert summary['accepted']==1 and summary['settled']['unavailable']==1
    assert summary['internalAttempts']['started']==summary['internalAttempts']['succeeded']==1
    assert all(p['started']==0 for p in summary['providerAttemptsByFamily'].values())
    assert summary['providerPriceCatalogCoverage']=='not_qualified' and summary['producerCoverage']=='not_qualified'
    wire=json.dumps(final)
    for forbidden in (ACCOUNT,proof,op['executionToken'],before['payloadHmac'],'http'):
        assert forbidden not in wire
    assert not any(r['SK'].startswith('OPS')for r in a.ddb.Table('authority').scan()['Items'])


def test_start_and_finish_replay_preserve_exact_refs_deadline_and_accounting(world):
    w=world;a,_,_,row,_,clock=w;ingest,proof,op,first=start(w)
    clock[0]+=3
    assert ingest.start_attempt(WORKER,proof,op['executionToken'])==first|{'decision':'REVALIDATED'}
    begun=attempt_events(first['projection'])[0]
    final=ingest.finish_attempt(WORKER,proof,op['executionToken'],begun['attemptRef'],outcome='failed',duration_ms=250)
    before=deepcopy(row('CHECK#'+proof));ledger=deepcopy(row('PERIOD#p1'));clock[0]+=5
    assert ingest.finish_attempt(WORKER,proof,op['executionToken'],begun['attemptRef'],outcome='failed',duration_ms=250)==final|{'decision':'REVALIDATED'}
    assert row('CHECK#'+proof)==before and row('PERIOD#p1')==ledger
    assert attempt_events(final['projection'])[1]['reasonCode']=='SERVICE_UNAVAILABLE'


def test_finish_after_settlement_cannot_rewrite_charge_or_terminal(world):
    a,_,_,row,_,clock=world;ingest,proof,op,first=start(world);begun=attempt_events(first['projection'])[0]
    a.settle(WORKER,proof,op['executionToken'],'complete');clock[0]+=2
    final=ingest.finish_attempt(WORKER,proof,op['executionToken'],begun['attemptRef'],outcome='succeeded',duration_ms=1500)
    assert final['projection']['sourceState']=='SETTLED' and final['projection']['events'][1]['chargedChecks']==1
    assert row('PERIOD#p1')['usedChecks']==1 and row('INFLIGHT')['activeCount']==0


@pytest.mark.parametrize('kind',['owner','token','unstarted','reference','outcome','duration','duplicate-outcome','duplicate-duration'])
def test_unbound_or_conflicting_finish_has_no_mutation(world,kind):
    a,_,_,row,_,_=world;ingest,proof,op,first=start(world);begun=attempt_events(first['projection'])[0]
    worker=WORKER;token=op['executionToken'];ref=begun['attemptRef'];outcome='succeeded';duration=100
    if kind.startswith('duplicate'):
        ingest.finish_attempt(worker,proof,token,ref,outcome=outcome,duration_ms=duration)
    if kind=='owner':worker=TrustedWorkerContext('another-approved-account')
    elif kind=='token':token='f'*32
    elif kind=='unstarted':
        changed=row('CHECK#'+proof);changed.pop('operationsProjection');a.ddb.Table('authority').put_item(Item=changed)
    elif kind=='reference':ref='at1_'+'f'*32
    elif kind in ('outcome','duplicate-outcome'):outcome='failed' if kind.startswith('duplicate')else 'PRIVATE_FINISH'
    elif kind in ('duration','duplicate-duration'):duration=1000 if kind.startswith('duplicate')else -1
    before=deepcopy(row('CHECK#'+proof));ledger=deepcopy(row('PERIOD#p1'))
    with pytest.raises(AuthorityError):ingest.finish_attempt(worker,proof,token,ref,outcome=outcome,duration_ms=duration)
    assert row('CHECK#'+proof)==before and row('PERIOD#p1')==ledger


@pytest.mark.parametrize('boundary',['lease','settled','clock-back','deadline','not-recorded','disabled'])
def test_start_rejects_or_skips_without_new_linkage(world,boundary):
    a,_,_,row,_,clock=world;ingest,proof,op=fresh(world)
    if boundary=='lease':clock[0]=int(row('CHECK#'+proof)['settleByEpoch'])
    elif boundary=='settled':a.settle(WORKER,proof,op['executionToken'],'complete')
    elif boundary=='clock-back':clock[0]-=1
    elif boundary=='deadline':clock[0]=int(row('CHECK#'+proof)['operationsReceipt']['expiresAtEpoch'])
    elif boundary=='not-recorded':
        changed=row('CHECK#'+proof);changed.pop('operationsReceipt');a.ddb.Table('authority').put_item(Item=changed)
    else:
        from dataclasses import replace
        a.s=replace(a.s,operations_producer_enabled=False)
    if boundary in ('deadline','not-recorded'):
        result=ingest.start_attempt(WORKER,proof,op['executionToken']);assert result['decision']in('EXPIRED','NOT_RECORDED')
    else:
        with pytest.raises(AuthorityError):ingest.start_attempt(WORKER,proof,op['executionToken'])
    assert 'operationsProjection' not in row('CHECK#'+proof)


@pytest.mark.parametrize('race',['delete-marker','erase','settle','projection','execution','inventory','expiry','lease-delay'])
def test_actual_transaction_races_cannot_restore_or_remint_attempt(world,monkeypatch,race):
    a,_,put,row,change,clock=world;ingest,proof,op=fresh(world);real=ingest.client.transact_write_items
    def raced(**kwargs):
        monkeypatch.setattr(ingest.client,'transact_write_items',real)
        if race=='delete-marker':put('deletion','ACCOUNT_DELETION',status='REQUESTED')
        elif race=='erase':a.ddb.Table('authority').delete_item(Key={'PK':a._partition(ACCOUNT,'k1'),'SK':'CHECK#'+proof})
        elif race=='settle':a.settle(WORKER,proof,op['executionToken'],'complete')
        elif race=='projection':ingest.capture(WORKER,proof)
        elif race=='execution':change('authority','CHECK#'+proof,executionToken='f'*32)
        elif race=='inventory':
            inv=a._get('authority',INVENTORY_KEY);a.ddb.Table('authority').put_item(Item=inv|{'revision':2})
        elif race=='expiry':
            clock[0]=int(row('CHECK#'+proof)['operationsReceipt']['expiresAtEpoch'])
            OperationsRetirement(a.ddb,'authority',now=a.now).retire(a._partition(ACCOUNT,'k1'),'CHECK#'+proof)
        else:clock[0]=int(row('CHECK#'+proof)['settleByEpoch'])
        return real(**kwargs)
    monkeypatch.setattr(ingest.client,'transact_write_items',raced)
    # Advancing only the local clock after the predispatch guard is not a
    # server-side time condition; it may commit, but must not return success.
    if race=='lease-delay':
        with pytest.raises(AuthorityError,match='^OPERATIONS_INGESTION_TIME_BUDGET$'):
            ingest.start_attempt(WORKER,proof,op['executionToken'])
        assert len(attempt_events(row('CHECK#'+proof)['operationsProjection']))==1
    else:
        with pytest.raises(AuthorityError):ingest.start_attempt(WORKER,proof,op['executionToken'])
        current=row('CHECK#'+proof)
        assert current is None or not attempt_events(current.get('operationsProjection',{'events':[]}))


def test_unknown_start_write_ack_never_automatically_retries_or_claims_dispatch(world,monkeypatch):
    a,_,_,row,_,_=world;ingest,proof,op=fresh(world);real=ingest.client.transact_write_items;calls=[]
    def lost(**kwargs):calls.append(1);real(**kwargs);raise TimeoutError('PRIVATE_WRITE')
    monkeypatch.setattr(ingest.client,'transact_write_items',lost)
    with pytest.raises(AuthorityError,match='^OPERATIONS_INGESTION_WRITE_UNKNOWN$'):
        ingest.start_attempt(WORKER,proof,op['executionToken'])
    assert calls==[1] and len(attempt_events(row('CHECK#'+proof)['operationsProjection']))==1
    monkeypatch.setattr(ingest.client,'transact_write_items',real)
    retained=deepcopy(row('CHECK#'+proof)['operationsProjection'])
    assert ingest.start_attempt(WORKER,proof,op['executionToken'])['decision']=='REVALIDATED'
    assert row('CHECK#'+proof)['operationsProjection']==retained


@pytest.mark.parametrize('orphan',[False,True])
def test_existing_selective_eraser_clears_new_projection_without_extending_full_ttl(world,orphan):
    a,_,_,row,_,clock=world;ingest,proof,op,first=start(world)
    a.settle(WORKER,proof,op['executionToken'],'complete');ingest.capture(WORKER,proof)
    before=deepcopy(row('CHECK#'+proof));clock[0]=int(before['operationsReceipt']['expiresAtEpoch'])
    if orphan:
        changed=deepcopy(before);changed.pop('operationsReceipt');a.ddb.Table('authority').put_item(Item=changed)
    assert OperationsRetirement(a.ddb,'authority',now=a.now).retire(a._partition(ACCOUNT,'k1'),'CHECK#'+proof)
    after=row('CHECK#'+proof)
    assert not {'operationsReceipt','operationsProjection','operationsTerminalReasonCode'}&set(after)
    assert after['expiresAt']==before['expiresAt']
    assert after['retentionDeadlineEpoch']==before['retentionDeadlineEpoch']
    assert row('PERIOD#p1')['usedChecks']==1
    assert ingest.finish_attempt(WORKER,proof,op['executionToken'],attempt_events(first['projection'])[0]['attemptRef'],outcome='succeeded',duration_ms=200)['decision']=='NOT_RECORDED'


@pytest.mark.parametrize('mutation',['cost','provider','account','ordinal','finish-without-start','future','schema','event-id','context','extra-attempt'])
def test_projection_closed_context_rejects_hostile_or_unjoined_facts(world,mutation):
    a,_,_,row,_,clock=world;ingest,proof,op,first=start(world);captured=deepcopy(first['projection']);event=captured['events'][1]
    if mutation=='cost':event['estimatedCostMicrousd']=0
    elif mutation=='provider':event['providerFamily']='openai'
    elif mutation=='account':event['accountId']='PRIVATE_FIELD'
    elif mutation=='ordinal':event['attemptOrdinal']=2
    elif mutation=='finish-without-start':event['eventType']='attempt_finished'
    elif mutation=='future':event['eventTimeEpoch']=captured['reference']['expiresAtEpoch']
    elif mutation=='schema':captured['schemaVersion']=1
    elif mutation=='event-id':event['eventId']=captured['events'][0]['eventId']
    elif mutation=='context':event['component']='message_evaluator'
    else:captured['events'].append(deepcopy(event))
    with pytest.raises(OperationsReceiptFault):validate_capture(captured)


@pytest.mark.parametrize('ms,bucket',[(0,'lt_250ms'),(249,'lt_250ms'),(250,'250_999ms'),(999,'250_999ms'),(1000,'1_4s'),(4999,'1_4s'),(5000,'5_14s'),(14999,'5_14s'),(15000,'15s_plus')])
def test_only_existing_coarse_internal_duration_bins(ms,bucket):assert duration_bucket(ms)==bucket


def test_actual_account_deletion_erases_attempts_without_extra_locator(world):
    from test_deletion import setup, finish, items
    a,_,_,row,_,_=world
    ingest,proof,op,first=start(world)
    bridge,command,_=setup(world)
    assert finish(bridge,command)['complete']
    assert items(a,a._partition(ACCOUNT,'k1'))==[]
    with pytest.raises(AuthorityError):
        ingest.finish_attempt(WORKER,proof,op['executionToken'],attempt_events(first['projection'])[0]['attemptRef'],outcome='succeeded',duration_ms=1)
    assert items(a,a._partition(ACCOUNT,'k1'))==[]


from test_entitlements import writer_world


def test_actual_paid_shortening_keeps_start_awaiting_finish_without_charge_or_refund_claim(writer_world):
    from test_operations_paid_closure import pending
    from test_purchase_shortening import revoke
    from shared_operations.receipts import RETENTION_SECONDS
    writer,w,decisions,period,proof,op=pending(writer_world)
    a,_,_,row,_,clock=w;ingest=ReceiptIngestion(a)
    first=ingest.start_attempt(WORKER,proof,op['executionToken'])['projection']
    result=revoke(writer,w,decisions,clock[0]-RETENTION_SECONDS)
    assert row('CHECK#'+proof)['chargedChecks']==0 and row('INFLIGHT')['activeCount']==0
    captured=ingest.capture(WORKER,proof)['projection']
    assert captured['sourceState']=='SETTLED'
    assert captured['events'][1]['processingOutcome']=='failed' and captured['events'][1]['reasonCode']=='OPERATION_EXPIRED'
    assert attempt_events(captured)==attempt_events(first)
    assert row(period['SK'])['reservedChecks']==0 and row(period['SK'])['usedChecks']==0


@pytest.mark.parametrize('phase',['start','finish'])
def test_disabled_adapter_performs_no_source_read_or_rng(world,monkeypatch,phase):
    a=world[0];ingest=ReceiptIngestion(a)
    def forbidden(*args,**kwargs):raise AssertionError('DISABLED_READ')
    monkeypatch.setattr(ingest,'_get',forbidden)
    with pytest.raises(AuthorityError,match='^OPERATIONS_INGESTION_DISABLED$'):
        if phase=='start':ingest.start_attempt(WORKER,None,None)
        else:ingest.finish_attempt(WORKER,None,None,None,outcome=None,duration_ms=None)


def test_ordinary_capture_rejects_attempt_in_future_after_clock_rollback(world):
    a,_,_,row,_,clock=world;ingest,proof,op=fresh(world)
    clock[0]+=5
    ingest.start_attempt(WORKER,proof,op['executionToken'])
    before=deepcopy(row('CHECK#'+proof));clock[0]-=2
    with pytest.raises(AuthorityError,match='^OPERATIONS_INGESTION_SOURCE_INVALID$'):
        ingest.capture(WORKER,proof)
    assert row('CHECK#'+proof)==before
