"""Real local Moto transactions; never real AWS, provider, or account actions."""
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys

import pytest

if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Storage regressions require isolated Moto job', allow_module_level=True)
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests/shared_check_authority')]
from test_transactions import world, ACCOUNT, PAYLOAD, WORKER, admit
from test_deletion import setup as deletion_setup, finish as deletion_finish
from shared_check_authority.core import AuthorityError
from shared_check_authority.expiry import Expiry
from shared_check_authority.recovery import Recovery
from shared_operations.contract import OperationsError, RETENTION_SECONDS
from shared_operations.publisher import CONTROL, Publisher, lookup_key

GEN='a'*32


@pytest.fixture
def opsworld(world):
    a=world[0]
    a.s=replace(a.s,receipt_retention_seconds=RETENTION_SECONDS,counter_retention_seconds=RETENTION_SECONDS)
    a.ddb.create_table(TableName='operations',BillingMode='PAY_PER_REQUEST',
        KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],
        AttributeDefinitions=[{'AttributeName':k,'AttributeType':'S'} for k in ('PK','SK')])
    a.ddb.Table('operations').put_item(Item=CONTROL|{'recordType':'V1_OPERATIONS_STORE','generation':GEN,
        'environment':'dev','state':'QUALIFIED','retentionSeconds':RETENTION_SECONDS,'backupsDisabled':True})
    a.operations=Publisher(a,'operations',GEN,'dev')
    return world


def metadata(world,proof):
    a=world[0]
    return a.operations._meta(a._partition(ACCOUNT,'k1'),proof,allow_expired=True)


def all_events(world,proof):
    a=world[0];m=metadata(world,proof)
    return a.ddb.Table('operations').query(KeyConditionExpression='PK = :pk',
        ExpressionAttributeValues={':pk':m['logicalCheckRef']},ConsistentRead=True)['Items']


def test_admit_settle_replay_atomic_and_no_content_in_child_records(opsworld):
    a,e,_,row,_,_=opsworld
    proof,operation=admit(opsworld)
    meta=metadata(opsworld,proof)
    assert meta['operationsExpiresAtEpoch']==meta['acceptedAtEpoch']+RETENTION_SECONDS and 'expiresAt' not in meta
    before=all_events(opsworld,proof);assert len(before)==1
    assert a.admit(e,PAYLOAD,proof)['admitted'] is False
    assert all_events(opsworld,proof)==before
    result=a.settle(WORKER,proof,operation['executionToken'],'complete')
    assert result['chargedChecks']==1 and len(all_events(opsworld,proof))==2
    assert a.settle(WORKER,proof,operation['executionToken'],'failed')==result
    events=a.operations.events_for(ACCOUNT,proof)
    assert {x['eventType'] for x in events}=={'check_accepted','check_terminal'}
    encoded=str(all_events(opsworld,proof))
    assert ACCOUNT not in encoded and 'example.com' not in encoded and proof not in encoded
    assert meta['logicalCheckRef'] not in str(result)
    assert row('PERIOD#p1')['usedChecks']==1


def test_admission_commit_then_ack_loss_does_not_allocate_new_facts(opsworld,monkeypatch):
    a,e,*_=opsworld;proof=a.prepare(e,PAYLOAD,'lost-ack')
    original=a._transact
    def uncertain(items):
        original(items)
        if any(x.get('Put',{}).get('Item',{}).get('recordType')=='V1_CHECK_RECEIPT' for x in items):
            raise AuthorityError('TRANSACTION_UNCERTAIN')
    monkeypatch.setattr(a,'_transact',uncertain)
    result=a.admit(e,PAYLOAD,proof)
    assert result['admitted'] is False
    before=all_events(opsworld,proof)
    assert len(before)==1 and a.admit(e,PAYLOAD,proof)['admitted'] is False
    assert all_events(opsworld,proof)==before


def test_store_not_qualified_prevents_receipt_or_reservation(opsworld):
    a,e,_,row,*_=opsworld
    a.ddb.Table('operations').update_item(Key=CONTROL,UpdateExpression='SET #s = :s',
        ExpressionAttributeNames={'#s':'state'},ExpressionAttributeValues={':s':'RESTORE_QUARANTINED'})
    proof=a.prepare(e,PAYLOAD,'quarantined')
    with pytest.raises(AuthorityError):a.admit(e,PAYLOAD,proof)
    assert row('CHECK#'+proof) is None and row('PERIOD#p1')['reservedChecks']==0
    assert row('OPS#'+proof) is None


@pytest.mark.parametrize('table',['users','devices','deletion','authority'])
def test_operations_destination_cannot_alias_any_source_table(opsworld,table):
    with pytest.raises(OperationsError,match='CONFIGURATION_INVALID'):Publisher(opsworld[0],table,GEN,'dev')


def test_wrong_environment_generation_does_not_admit_customer_work(opsworld):
    a,e,_,row,*_=opsworld
    a.ddb.Table('operations').update_item(Key=CONTROL,UpdateExpression='SET environment = :e',ExpressionAttributeValues={':e':'prod'})
    proof=a.prepare(e,PAYLOAD,'wrong-environment')
    with pytest.raises(AuthorityError):a.admit(e,PAYLOAD,proof)
    assert row('CHECK#'+proof) is None and row('PERIOD#p1')['reservedChecks']==0


def test_one_winner_claim_replay_and_wrong_execution_owner(opsworld):
    a,*_=opsworld;proof,operation=admit(opsworld)
    args=dict(component='url_assessment',ordinal=1,provider='google_web_risk')
    with pytest.raises(AuthorityError):a.operations.start_attempt(ACCOUNT,proof,'f'*32,**args)
    permit=a.operations.start_attempt(ACCOUNT,proof,operation['executionToken'],**args)
    with pytest.raises(OperationsError,match='ALREADY_CLAIMED'):
        a.operations.start_attempt(ACCOUNT,proof,operation['executionToken'],**args)
    event=a.operations.finish_attempt(permit,outcome='failed',reason='PROVIDER_UNAVAILABLE',duration='1_4s')
    assert event['providerRequestUnits'] is None and event['estimatedCostMicrousd'] is None
    assert len(all_events(opsworld,proof))==3


def test_claim_commit_then_response_loss_cannot_issue_permit_or_reclaim(opsworld,monkeypatch):
    a,*_=opsworld;proof,operation=admit(opsworld)
    original=a._transact
    def lose(items):original(items);raise AuthorityError('TRANSACTION_UNCERTAIN')
    monkeypatch.setattr(a,'_transact',lose)
    args=dict(component='url_assessment',ordinal=1,provider='google_web_risk')
    with pytest.raises(AuthorityError):a.operations.start_attempt(ACCOUNT,proof,operation['executionToken'],**args)
    monkeypatch.setattr(a,'_transact',original)
    with pytest.raises(OperationsError,match='ALREADY_CLAIMED'):
        a.operations.start_attempt(ACCOUNT,proof,operation['executionToken'],**args)
    assert len(all_events(opsworld,proof))==2


def test_stale_concurrent_claim_cannot_win_twice(opsworld,monkeypatch):
    a,*_=opsworld;proof,operation=admit(opsworld)
    old=metadata(opsworld,proof);original=a.operations._meta
    args=dict(component='url_assessment',ordinal=1,provider='google_web_risk')
    a.operations.start_attempt(ACCOUNT,proof,operation['executionToken'],**args)
    monkeypatch.setattr(a.operations,'_meta',lambda *args,**kwargs:deepcopy(old))
    with pytest.raises(AuthorityError):a.operations.start_attempt(ACCOUNT,proof,operation['executionToken'],**args)
    monkeypatch.setattr(a.operations,'_meta',original)
    assert len(all_events(opsworld,proof))==2


def test_settlement_commit_then_ack_loss_keeps_one_fact_and_charge(opsworld,monkeypatch):
    a,_,_,row,*_=opsworld;proof,admission=admit(opsworld);original=a._transact
    def uncertain(items):original(items);raise AuthorityError('TRANSACTION_UNCERTAIN')
    monkeypatch.setattr(a,'_transact',uncertain)
    assert a.settle(WORKER,proof,admission['executionToken'],'complete')['chargedChecks']==1
    assert len(all_events(opsworld,proof))==2 and row('PERIOD#p1')['usedChecks']==1


@pytest.mark.parametrize('lose_ack',[False,True])
def test_real_openai_dispatch_hook_uses_moto_one_winner_journal(opsworld,monkeypatch,lose_ack):
    sys.path.insert(0,str(ROOT/'tests/message_evaluator'))
    from test_proposer import intent,settings,wire,Connection
    from message_evaluator import proposer
    from shared_message_contract.validation import MessageError
    from shared_operations.dispatch import JournalObserver
    a,e,*_=opsworld;request=intent()
    proof=a.prepare(e,request,'message',client_check_id='message')
    admission=a.admit(e,request,proof,client_check_id='message')
    original=a._transact;connections=[]
    if lose_ack:
        def uncertain(items):original(items);raise AuthorityError('TRANSACTION_UNCERTAIN')
        monkeypatch.setattr(a,'_transact',uncertain)
    def factory(*args,**kwargs):
        connection=Connection(wire(),*args,**kwargs);connections.append(connection);return connection
    def observer():
        return JournalObserver(a.operations,ACCOUNT,proof,admission['executionToken'],
            component='message_evaluator',ordinal=1,provider='openai',clock=lambda:0.)
    def invoke():
        return proposer.propose(request,settings(),1000,clock=lambda:0.,connection_factory=factory,
            secret_loader=lambda *args:'synthetic-only-key',operations_observer=observer())
    if lose_ack:
        with pytest.raises(MessageError):invoke()
        monkeypatch.setattr(a,'_transact',original)
    else:assert invoke()['ruleId']=='UNRESOLVED'
    with pytest.raises(MessageError):invoke()  # New observer cannot reclaim the same slot.
    assert sum(bool(c.test_socket.sent) for c in connections)==(0 if lose_ack else 1)
    events=[x['event'] for x in all_events(opsworld,proof)]
    assert sum(x['eventType']=='attempt_started' for x in events)==1
    assert sum(x['eventType']=='attempt_finished' for x in events)==(0 if lose_ack else 1)


@pytest.mark.parametrize('mutation',['missing_lookup','missing_receipt','missing_profile','generation','changed_acceptance'])
def test_missing_restored_or_foreign_source_never_admits_an_attempt(opsworld,mutation):
    a,_,_,_,change,_=opsworld;proof,operation=admit(opsworld);pk=a._partition(ACCOUNT,'k1')
    if mutation=='missing_lookup':a.ddb.Table('authority').delete_item(Key=lookup_key(pk,proof))
    elif mutation=='missing_receipt':a.ddb.Table('authority').delete_item(Key={'PK':pk,'SK':'CHECK#'+proof})
    elif mutation=='missing_profile':a.ddb.Table('users').delete_item(Key={'PK':'USER#'+ACCOUNT,'SK':'PROFILE'})
    elif mutation=='generation':a.ddb.Table('operations').update_item(Key=CONTROL,UpdateExpression='SET generation = :g',ExpressionAttributeValues={':g':'b'*32})
    else:change('authority','CHECK#'+proof,operationsAcceptedAtEpoch=0)
    with pytest.raises((AuthorityError,OperationsError)):
        a.operations.start_attempt(ACCOUNT,proof,operation['executionToken'],component='url_assessment',ordinal=1,provider='google_web_risk')


def test_deletion_fence_between_source_read_and_claim_aborts(opsworld,monkeypatch):
    a,*_=opsworld;proof,operation=admit(opsworld);original=a._transact
    def racing(items):
        a.ddb.Table('deletion').put_item(Item={'PK':'ACCOUNT#'+ACCOUNT,'SK':'ACCOUNT_DELETION','status':'REQUESTED'})
        return original(items)
    monkeypatch.setattr(a,'_transact',racing)
    with pytest.raises(AuthorityError):
        a.operations.start_attempt(ACCOUNT,proof,operation['executionToken'],component='url_assessment',ordinal=1,provider='google_web_risk')
    assert len(all_events(opsworld,proof))==1


def test_background_recovery_publishes_same_atomic_zero_charge_terminal(opsworld):
    a,_,_,row,_,clock=opsworld;proof,_=admit(opsworld)
    clock[0]+=a.s.worker_settlement_seconds+1
    r=Recovery(a.ddb,'authority',now=a.now,operations=a.operations)
    assert r.expire(a._partition(ACCOUNT,'k1'),proof)
    terminal=[x['event'] for x in all_events(opsworld,proof) if x['event']['eventType']=='check_terminal'][0]
    assert terminal['reasonCode']=='OPERATION_EXPIRED' and terminal['chargedChecks']==0
    assert row('PERIOD#p1')['usedChecks']==0
    assert not r.expire(a._partition(ACCOUNT,'k1'),proof)


def test_operations_expiry_does_not_extend_or_delete_history(opsworld):
    a,e,_,row,_,clock=opsworld
    a.s=replace(a.s,governed_history_settlement_enabled=True)
    from message_evaluator.policy_v3 import evaluate
    from shared_message_contract.validation_v3 import VERSION
    request={'entryPoint':'message','language':'en','messageTransportVersion':VERSION,
        'target':{'scope':'sanitized_message','sourceType':'pasted_text','sanitizedText':'An ordinary sentence.',
        'speakerRole':'other','entities':[],'withheldLinks':False,'reviewedLinks':[]}}
    proof=a.prepare(e,request,'history',client_check_id='history')
    op=a.admit(e,request,proof,client_check_id='history');accepted=clock[0]
    clock[0]+=20
    result=evaluate('history',{k:request[k] for k in ('entryPoint','language','target')},rules_only=True)
    a.settle(WORKER,proof,op['executionToken'],result['processingOutcome'],result_summary=result)
    saved=deepcopy(row('CHECK#'+proof));meta=metadata(opsworld,proof)
    assert saved['expiresAt']==accepted+20+RETENTION_SECONDS
    pk=a._partition(ACCOUNT,'k1');clock[0]=accepted+RETENTION_SECONDS
    with pytest.raises(OperationsError):a.operations.events_for(ACCOUNT,proof)
    expiry=Expiry(a.ddb,'authority',now=a.now,operations=a.operations)
    assert expiry.expire(pk,'OPS#'+proof)
    assert row('OPS#'+proof) is None and row('CHECK#'+proof)==saved|{'operationsErasedGeneration':GEN}
    assert a.ddb.Table('operations').query(KeyConditionExpression='PK = :pk',
        ExpressionAttributeValues={':pk':meta['logicalCheckRef']})['Items']==[]


def test_deletion_drains_children_before_source_and_no_restored_ingestion(opsworld,monkeypatch):
    a,*_=opsworld;proof,_=admit(opsworld);meta=metadata(opsworld,proof)
    bridge,command,_=deletion_setup(opsworld);bridge.operations=a.operations
    original=bridge._transact
    def observed(items):
        for item in items:
            if item.get('Delete',{}).get('Key',{}).get('SK')=='CHECK#'+proof:
                assert a._get('authority',lookup_key(meta['PK'],proof)) is None
                assert not a.ddb.Table('operations').query(KeyConditionExpression='PK = :pk',
                    ExpressionAttributeValues={':pk':meta['logicalCheckRef']},ConsistentRead=True)['Items']
        return original(items)
    monkeypatch.setattr(bridge,'_transact',observed)
    assert deletion_finish(bridge,command,page_size=1,max_pages=1)['complete']
    a.ddb.Table('authority').put_item(Item=meta)  # Source-only restored orphan, no account/deletion bypass.
    with pytest.raises((AuthorityError,OperationsError)):a.operations.events_for(ACCOUNT,proof)


def test_cleanup_without_qualified_operations_adapter_never_drops_lookup(opsworld):
    a,*_=opsworld;proof,_=admit(opsworld)
    bridge,command,_=deletion_setup(opsworld)
    with pytest.raises(AuthorityError,match='CLEANUP_REQUIRED'):bridge.delete_batch(command)
    assert metadata(opsworld,proof)


@pytest.mark.parametrize('source_missing',[False,True])
def test_direct_drain_missing_lookup_requires_positive_erasure_or_legacy_proof(opsworld,source_missing):
    a,*_=opsworld;proof,_=admit(opsworld);pk=a._partition(ACCOUNT,'k1')
    a.ddb.Table('authority').delete_item(Key=lookup_key(pk,proof))
    if source_missing:a.ddb.Table('authority').delete_item(Key={'PK':pk,'SK':'CHECK#'+proof})
    with pytest.raises(OperationsError,match='CLEANUP_UNQUALIFIED'):
        a.operations.drain(pk,proof,a._account_conditions(ACCOUNT),mode='expiry')


def test_deletion_during_partial_drain_is_resumable_and_never_reopens_dispatch(opsworld):
    a,*_=opsworld;proof,_=admit(opsworld);meta=metadata(opsworld,proof)
    bridge,command,_=deletion_setup(opsworld);bridge.operations=a.operations
    # One-page child deletion cannot yet erase the lookup; next run proves empty.
    original=a.operations.drain
    a.operations.drain=lambda *args,**kwargs:original(*args,**(kwargs|{'max_pages':1}))
    for _ in range(5):
        first=bridge.delete_batch(command,page_size=1,max_pages=1)
        if metadata(opsworld,proof)['lookupState']=='DRAINING':break
    assert not first['complete'] and metadata(opsworld,proof)['lookupState']=='DRAINING'
    a.operations.drain=original
    assert deletion_finish(bridge,command,page_size=1,max_pages=1)['complete']


def test_pre_operations_receipts_keep_exact_legacy_path(world):
    a,*_=world;proof,op=admit(world)
    assert a.settle(WORKER,proof,op['executionToken'],'complete')['chargedChecks']==1
    assert a._get('authority',lookup_key(a._partition(ACCOUNT,'k1'),proof)) is None


@pytest.mark.parametrize('operation',['start','finish','read'])
def test_deadline_crossed_during_authority_reads_blocks_operation(opsworld,monkeypatch,operation):
    a,_,_,_,_,clock=opsworld;proof,admission=admit(opsworld)
    args=dict(component='url_assessment',ordinal=1,provider='google_web_risk')
    permit=a.operations.start_attempt(ACCOUNT,proof,admission['executionToken'],**args) if operation=='finish' else None
    before=deepcopy(all_events(opsworld,proof));original=a._account_conditions
    def delayed(account):
        guards=original(account);clock[0]+=RETENTION_SECONDS;return guards
    monkeypatch.setattr(a,'_account_conditions',delayed)
    with pytest.raises((AuthorityError,OperationsError)):
        if operation=='start':a.operations.start_attempt(ACCOUNT,proof,admission['executionToken'],**args)
        elif operation=='finish':a.operations.finish_attempt(permit,outcome='succeeded',reason='NONE',duration='1_4s',request_units=1)
        else:a.operations.events_for(ACCOUNT,proof)
    assert all_events(opsworld,proof)==before


def test_fabricated_or_restored_finish_permit_does_not_match_claim(opsworld):
    a,*_=opsworld;proof,admission=admit(opsworld)
    permit=a.operations.start_attempt(ACCOUNT,proof,admission['executionToken'],component='url_assessment',ordinal=1,provider='google_web_risk')
    forged=replace(permit,finish_id='op1_'+'f'*32)
    with pytest.raises(OperationsError,match='SOURCE_INVALID'):
        a.operations.finish_attempt(forged,outcome='succeeded',reason='NONE',duration='1_4s',request_units=1)
    assert len(all_events(opsworld,proof))==2


@pytest.mark.parametrize('path',['delete','expiry'])
def test_missing_lookup_with_surviving_children_is_not_erasure_proof(opsworld,path):
    a,_,_,row,_,clock=opsworld;proof,admission=admit(opsworld)
    a.settle(WORKER,proof,admission['executionToken'],'failed')
    meta=metadata(opsworld,proof);pk=meta['PK']
    a.ddb.Table('authority').delete_item(Key=lookup_key(pk,proof))
    if path=='delete':
        bridge,command,_=deletion_setup(opsworld);bridge.operations=a.operations
        with pytest.raises(AuthorityError,match='CLEANUP_UNQUALIFIED'):deletion_finish(bridge,command)
    else:
        clock[0]=row('CHECK#'+proof)['expiresAt']
        with pytest.raises(AuthorityError,match='CLEANUP_UNQUALIFIED'):
            Expiry(a.ddb,'authority',now=a.now,operations=a.operations).expire(pk,'CHECK#'+proof)
    assert row('CHECK#'+proof) is not None
    assert a.ddb.Table('operations').query(KeyConditionExpression='PK = :pk',
        ExpressionAttributeValues={':pk':meta['logicalCheckRef']})['Items']


def test_poisoned_operations_index_row_does_not_block_valid_expiry(opsworld):
    a,_,_,_,_,clock=opsworld;proof,admission=admit(opsworld)
    a.settle(WORKER,proof,admission['executionToken'],'failed');meta=metadata(opsworld,proof)
    clock[0]=meta['operationsExpiresAtEpoch']
    bad=proof[:-24]+'0'*24;key=lookup_key(meta['PK'],bad)
    a.ddb.Table('authority').put_item(Item=key|{'recordType':'V1_OPERATIONS_LOOKUP',
        'GSI1PK':'V1_OPERATIONS_EXPIRING','GSI1SK':f'{clock[0]-1:012d}#{meta["PK"]}#{key["SK"]}'})
    counts=Expiry(a.ddb,'authority',now=a.now,operations=a.operations).sweep_operations(page_size=1,max_pages=4)
    assert counts['failed']==1 and counts['deleted']==1 and counts['cursor'] is None


@pytest.mark.parametrize('mutation',[{'state':'ADMITTED'},{'policyVersion':'foreign'}])
def test_source_expiry_race_cannot_close_operations_before_complete_source_guard(opsworld,monkeypatch,mutation):
    a,_,_,row,change,clock=opsworld;proof,admission=admit(opsworld)
    a.settle(WORKER,proof,admission['executionToken'],'failed');meta=metadata(opsworld,proof)
    clock[0]=row('CHECK#'+proof)['expiresAt'];original=a.operations.drain
    def racing(*args,**kwargs):
        change('authority','CHECK#'+proof,**mutation)
        return original(*args,**kwargs)
    monkeypatch.setattr(a.operations,'drain',racing)
    with pytest.raises(AuthorityError):
        Expiry(a.ddb,'authority',now=a.now,operations=a.operations).expire(meta['PK'],'CHECK#'+proof)
    assert metadata(opsworld,proof)['lookupState']=='OPEN' and len(all_events(opsworld,proof))==2
