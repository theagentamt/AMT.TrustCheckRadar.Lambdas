"""Isolated deletion component regressions; no handler wiring or real AWS."""
import os
import sys
from pathlib import Path
import hashlib
from copy import deepcopy
import pytest

if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run in isolated Moto environment; see deletion handoff', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).parent))
from test_transactions import world, ACCOUNT, PAYLOAD, WORKER, admit, failure
from shared_check_authority.deletion import AuthorityDeletion, INVENTORY_KEY
from shared_check_authority.recovery import Recovery
from shared_check_authority.core import AuthorityError

OP = 'ee9a078b-ae70-4d6f-8229-e8a34a72c9ec'


def setup(world, *, keys=None, fence=True):
    a,e,put,row,change,clock=world
    keys = keys or a.s.hmac_keys
    inventory = INVENTORY_KEY | {'recordType':'V1_HMAC_KEY_INVENTORY','schemaVersion':1,'revision':1,'coverage':'VERIFIED_COMPLETE','issuedKeys':{k:hashlib.sha256(v).hexdigest() for k,v in keys.items()}}
    a.ddb.Table('authority').put_item(Item=inventory)
    cmd = {'PK':'ACCOUNT#'+ACCOUNT,'SK':'ACCOUNT_DELETION','schemaVersion':1,'recordVersion':1,'environment':'dev','eventType':'account.deletion.requested','accountId':ACCOUNT,'operationId':OP,'status':'REQUESTED','occurredAtEpoch':clock[0],'deleteByEpoch':clock[0]+86400}
    if fence:
        a.ddb.Table('deletion').put_item(Item=cmd)
        change('users','PROFILE',status='DELETION_REQUESTED')
    bridge = AuthorityDeletion(a.ddb,authority_table='authority',ledger_table='deletion',environment='dev',keyring=keys,receipt_retention_seconds=120*86400,now=a.now)
    return bridge,cmd,inventory


def items(a, partition):
    return a.ddb.Table('authority').query(KeyConditionExpression='PK = :pk',ExpressionAttributeValues={':pk':partition},ConsistentRead=True)['Items']


def receipt(a):
    return a._get('deletion',{'PK':'ACCOUNT#'+ACCOUNT,'SK':'ACCOUNT_DELETION#V1_AUTHORITY'})


def finish(bridge,cmd,**kwargs):
    for _ in range(30):
        result=bridge.delete_batch(cmd,**kwargs)
        if result['complete']:return result
    pytest.fail('bounded deletion did not converge')


def test_all_families_multiple_retained_keys_and_other_account_preserved(world):
    a,e,put,row,change,clock=world
    proof,admission=admit(world)
    keys=a.s.hmac_keys|{'k2':b'other-synthetic-key-material-00000000'}
    bridge,cmd,inventory=setup(world,keys=keys)
    for kid in keys:
        partition=bridge._partition(ACCOUNT,kid)
        for sk in ('0FUTURE','00FUTURE','ACCESS','TRIAL_HISTORY','WRITER#AUDIT','PERIOD#old','INFLIGHT','Z_FUTURE'):
            attrs={'recordType':'V1_ACCESS_AUTHORITY','state':'ACTIVE'} if sk=='ACCESS' else {'synthetic':True}
            a.ddb.Table('authority').put_item(Item={'PK':partition,'SK':sk,**attrs})
    other={'PK':bridge._partition('another-account','k1'),'SK':'ACCESS','state':'ACTIVE'}
    a.ddb.Table('authority').put_item(Item=other)
    assert finish(bridge,cmd,page_size=1,max_pages=1)['complete']
    for kid in keys:assert items(a,bridge._partition(ACCOUNT,kid))==[]
    assert a._get('authority',INVENTORY_KEY)==inventory
    assert a._get('authority',{'PK':other['PK'],'SK':'ACCESS'})==other
    done=receipt(a)
    assert done['component']=='V1_AUTHORITY' and done['retainUntilEpoch']==clock[0]+120*86400
    assert a._get('deletion',{'PK':cmd['PK'],'SK':'ACCOUNT_DELETION#ENTITLEMENTS'}) is None
    assert bridge.delete_batch(cmd)=={'deleted':0,'complete':True,'alreadyComplete':True}


def test_access_fenced_before_page_and_deleted_last(world,monkeypatch):
    a,e,put,row,change,clock=world
    bridge,cmd,_=setup(world)
    observed=[]
    original=bridge._transact
    def watching(ops):
        deleted=[x['Delete']['Key']['SK'] for x in ops if 'Delete' in x and x['Delete']['TableName']=='authority']
        if deleted:
            assert row('ACCESS')['state']=='DELETING'
            if 'ACCESS' in deleted:assert [x['SK'] for x in items(a,bridge._partition(ACCOUNT,'k1'))]==['ACCESS']
            observed.extend(deleted)
        return original(ops)
    monkeypatch.setattr(bridge,'_transact',watching)
    first=bridge.delete_batch(cmd,page_size=1,max_pages=1)
    assert not first['complete'] and row('ACCESS')['state']=='DELETING'
    assert receipt(a) is None
    finish(bridge,cmd)
    assert observed[-1]=='ACCESS'


def test_orphan_partition_without_access_is_erased(world):
    a,*_=world
    bridge,cmd,_=setup(world)
    a.ddb.Table('authority').delete_item(Key={'PK':bridge._partition(ACCOUNT,'k1'),'SK':'ACCESS'})
    assert finish(bridge,cmd)['complete']
    assert items(a,bridge._partition(ACCOUNT,'k1'))==[]


@pytest.mark.parametrize('mutation',[
    lambda c:c.update(operationId='not-a-uuid'),lambda c:c.update(accountId='other-account'),
    lambda c:c.update(schemaVersion=True),lambda c:c.update(deleteByEpoch=1),
    lambda c:c.update(environment='prod'),lambda c:c.update(extra=True)])
def test_invalid_command_cannot_delete(world,mutation):
    a,*_=world
    bridge,cmd,_=setup(world)
    mutation(cmd)
    failure('DELETION_COMMAND_INVALID',lambda:bridge.delete_batch(cmd))
    assert len(items(a,bridge._partition(ACCOUNT,'k1')))==2 and receipt(a) is None


def test_command_must_exist_in_authoritative_ledger(world):
    a,*_=world
    bridge,cmd,_=setup(world,fence=False)
    failure('DELETION_COMMAND_UNVERIFIED',lambda:bridge.delete_batch(cmd))
    assert len(items(a,bridge._partition(ACCOUNT,'k1')))==2


@pytest.mark.parametrize('change',['missing','unverified','dropped_key','changed_material','extra_field'])
def test_inventory_incomplete_or_lost_keys_cannot_claim_complete(world,change):
    a,*_=world
    bridge,cmd,inventory=setup(world)
    if change=='missing':a.ddb.Table('authority').delete_item(Key=INVENTORY_KEY)
    else:
        if change=='unverified':inventory['coverage']='UNVERIFIED'
        if change=='dropped_key':inventory['issuedKeys']['old']='0'*64
        if change=='changed_material':inventory['issuedKeys']['k1']='0'*64
        if change=='extra_field':inventory['unexpected']=True
        a.ddb.Table('authority').put_item(Item=inventory)
    failure('DELETION_KEY_INVENTORY_UNAVAILABLE',lambda:bridge.delete_batch(cmd))
    assert receipt(a) is None
    assert len(items(a,bridge._partition(ACCOUNT,'k1')))==2


def test_inventory_revision_change_during_partial_deletion_fails_closed(world):
    a,*_=world
    bridge,cmd,inventory=setup(world)
    assert not bridge.delete_batch(cmd,page_size=1,max_pages=1)['complete']
    inventory['revision']=2
    a.ddb.Table('authority').put_item(Item=inventory)
    failure('DELETION_INVENTORY_CHANGED',lambda:bridge.delete_batch(cmd))
    assert receipt(a) is None


@pytest.mark.parametrize('stage',['fence','delete','complete'])
def test_global_command_race_never_reports_completion(world,monkeypatch,stage):
    a,*_=world
    bridge,cmd,_=setup(world)
    original=bridge._transact
    fired=[]
    def racing(ops):
        has_delete=any('Delete' in x and x['Delete']['TableName']=='authority' for x in ops)
        has_receipt=any('Put' in x and x['Put']['Item'].get('component')=='V1_AUTHORITY' for x in ops)
        matches={'fence':not has_delete and not has_receipt,'delete':has_delete,'complete':has_receipt}[stage]
        if matches and not fired:
            a.ddb.Table('deletion').put_item(Item=cmd|{'environment':'changed'})
            fired.append(True)
        return original(ops)
    monkeypatch.setattr(bridge,'_transact',racing)
    failure('DELETION_TRANSACTION_UNCERTAIN',lambda:bridge.delete_batch(cmd))
    assert fired and receipt(a) is None


@pytest.mark.parametrize('stage',['fence','delete','complete'])
def test_ambiguous_committed_transaction_is_safe_to_retry(world,monkeypatch,stage):
    a,*_=world
    bridge,cmd,_=setup(world)
    original=bridge.client.transact_write_items
    fired=[]
    def uncertain(**kwargs):
        ops=kwargs['TransactItems']
        has_delete=any('Delete' in x and x['Delete']['TableName']=='authority' for x in ops)
        has_receipt=any('Put' in x and x['Put']['Item'].get('component')=='V1_AUTHORITY' for x in ops)
        result=original(**kwargs)
        if {'fence':not has_delete and not has_receipt,'delete':has_delete,'complete':has_receipt}[stage] and not fired:
            fired.append(True)
            raise TimeoutError('synthetic transport loss')
        return result
    monkeypatch.setattr(bridge.client,'transact_write_items',uncertain)
    failure('DELETION_TRANSACTION_UNCERTAIN',lambda:bridge.delete_batch(cmd))
    assert fired
    assert finish(bridge,cmd)['complete']
    assert items(a,bridge._partition(ACCOUNT,'k1'))==[]


def test_recovery_after_fence_and_after_deletion_cannot_recreate_rows(world,monkeypatch):
    a,e,put,row,change,clock=world
    proof,op=admit(world)
    recovery=Recovery(a.ddb,'authority',now=a.now)
    clock[0]+=121
    bridge,cmd,_=setup(world)
    original=bridge._transact
    checked=[]
    def fenced(ops):
        result=original(ops)
        if row('ACCESS') and row('ACCESS').get('state')=='DELETING' and not checked:
            failure('RECOVERY_TRANSACTION_UNCERTAIN',lambda:recovery.expire(bridge._partition(ACCOUNT,'k1'),proof))
            checked.append(True)
        return result
    monkeypatch.setattr(bridge,'_transact',fenced)
    assert finish(bridge,cmd)['complete'] and checked
    assert recovery.expire(bridge._partition(ACCOUNT,'k1'),proof) is False
    assert items(a,bridge._partition(ACCOUNT,'k1'))==[]


def test_writers_cannot_recreate_between_empty_read_and_receipt(world,monkeypatch):
    a,e,put,row,change,clock=world
    proof,op=admit(world)
    bridge,cmd,_=setup(world)
    original=bridge._transact
    checked=[]
    def completion(ops):
        if any('Put' in x and x['Put']['Item'].get('component')=='V1_AUTHORITY' for x in ops):
            # Keep PROFILE ACTIVE to isolate the global deletion ledger fence.
            change('users','PROFILE',status='ACTIVE')
            failure('ACCOUNT_UNAVAILABLE',lambda:a.prepare(e,PAYLOAD,'after-empty'))
            failure('ACCOUNT_UNAVAILABLE',lambda:a.admit(e,PAYLOAD,proof))
            failure('ACCOUNT_UNAVAILABLE',lambda:a.settle(WORKER,proof,op['executionToken'],'complete'))
            assert items(a,bridge._partition(ACCOUNT,'k1'))==[]
            checked.append(True)
        return original(ops)
    monkeypatch.setattr(bridge,'_transact',completion)
    assert finish(bridge,cmd)['complete'] and checked


def test_invalid_existing_component_receipt_not_accepted(world):
    a,*_=world
    bridge,cmd,_=setup(world)
    finish(bridge,cmd)
    done=receipt(a)|{'unreviewed':True}
    a.ddb.Table('deletion').put_item(Item=done)
    failure('DELETION_RECEIPT_INVALID',lambda:bridge.delete_batch(cmd))


@pytest.mark.parametrize('field',['schemaVersion','recordVersion','occurredAtEpoch','retainUntilEpoch'])
def test_boolean_receipt_versions_and_epochs_rejected(world,field):
    a,*_=world
    bridge,cmd,_=setup(world)
    finish(bridge,cmd)
    done=receipt(a)|{field:True}
    a.ddb.Table('deletion').put_item(Item=done)
    failure('DELETION_RECEIPT_INVALID',lambda:bridge.delete_batch(cmd))


def test_stale_recovery_transaction_cannot_recreate_after_complete_deletion(world,monkeypatch):
    a,e,put,row,change,clock=world
    proof,op=admit(world)
    recovery=Recovery(a.ddb,'authority',now=a.now)
    clock[0]+=121
    bridge,cmd,_=setup(world)
    original=a.client.transact_write_items
    fired=[]
    def stale(**kwargs):
        # Recovery has already strongly read the pending receipt and assembled
        # updates. Delete the account partition before that transaction commits.
        if any('Update' in x and x['Update'].get('ExpressionAttributeValues',{}).get(':settled')=='SETTLED' for x in kwargs['TransactItems']) and not fired:
            fired.append(True)
            assert finish(bridge,cmd)['complete']
        return original(**kwargs)
    monkeypatch.setattr(a.client,'transact_write_items',stale)
    failure('RECOVERY_TRANSACTION_UNCERTAIN',lambda:recovery.expire(bridge._partition(ACCOUNT,'k1'),proof))
    assert fired and receipt(a)
    assert items(a,bridge._partition(ACCOUNT,'k1'))==[]


def test_writer_commit_and_recovery_between_query_and_delete_blocked(world,monkeypatch):
    from shared_check_authority.entitlements import EntitlementWriter
    a,e,put,row,change,clock=world
    proof,op=admit(world)
    old=row('ACCESS')
    recovery=Recovery(a.ddb,'authority',now=a.now)
    writer=EntitlementWriter(a,approved_products=frozenset(),operator_principals=frozenset(),verification_max_age_seconds=1,trial_retention_approved=True)
    clock[0]+=121
    bridge,cmd,_=setup(world)
    original=bridge._transact
    fired=[]
    def racing(ops):
        # This callback runs after the bridge's strong page query and before its
        # delete transaction. _commit simulates an earlier-authorized writer.
        if any('Delete' in x and x['Delete']['TableName']=='authority' for x in ops) and not fired:
            fired.append(True)
            failure('TRANSACTION_UNCERTAIN',lambda:writer._commit(ACCOUNT,bridge._partition(ACCOUNT,'k1'),old,{},'stale-writer','refresh',{}))
            failure('RECOVERY_TRANSACTION_UNCERTAIN',lambda:recovery.expire(bridge._partition(ACCOUNT,'k1'),proof))
        return original(ops)
    monkeypatch.setattr(bridge,'_transact',racing)
    assert finish(bridge,cmd)['complete'] and fired
    assert items(a,bridge._partition(ACCOUNT,'k1'))==[]


def test_inventory_rotation_race_during_delete_is_conditioned(world,monkeypatch):
    a,*_=world
    bridge,cmd,inventory=setup(world)
    original=bridge._transact
    fired=[]
    def racing(ops):
        if any('Delete' in x and x['Delete']['TableName']=='authority' for x in ops) and not fired:
            fired.append(True)
            a.ddb.Table('authority').put_item(Item=inventory|{'revision':2})
        return original(ops)
    monkeypatch.setattr(bridge,'_transact',racing)
    failure('DELETION_TRANSACTION_UNCERTAIN',lambda:bridge.delete_batch(cmd))
    assert fired and receipt(a) is None
    assert len(items(a,bridge._partition(ACCOUNT,'k1')))==2


def test_access_ttl_removed_during_partial_deletion_and_no_sensitive_logging(world,capsys):
    a,e,put,row,change,clock=world
    bridge,cmd,_=setup(world)
    change('authority','ACCESS',expiresAt=clock[0]+1)
    assert not bridge.delete_batch(cmd,page_size=1,max_pages=1)['complete']
    assert 'expiresAt' not in row('ACCESS')
    assert capsys.readouterr()==('', '')
