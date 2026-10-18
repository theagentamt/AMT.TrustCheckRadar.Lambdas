"""Scoped isolation, not proof of source completeness or actor unlinkability."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).parent))
from test_store import client, TABLE, DAY, START, FIRST, NEXT, summary, corrected, rows
from operations_reporting.reducer import aggregate_day
from operations_reporting.revisions import validate_summary, validate_revision, plan_revision, RevisionFault
from operations_reporting.store import AggregateStore, AggregateStoreFault
from operations_reporting.scope import SUMMARY_V2, REVISION_V2, validate_scope, ScopeFault


def scope(n=1,population='environment_customer'):
    return {'schemaVersion':1,'population':population,
        'generation':f'{n:08x}-1111-4111-8111-111111111111',
        'sourceContract':'authority-receipt-capture-v2',
        'coverage':'selected_subset_not_environment_total'if population=='selected_dev'else'environment_completeness_unqualified'}


def scoped(report=None,descriptor=None):
    r=deepcopy(summary()if report is None else report)
    r.update(schemaVersion=SUMMARY_V2,sourceScope=scope()if descriptor is None else deepcopy(descriptor))
    return validate_summary(r)


def store(c,descriptor=None,now=FIRST):
    return AggregateStore(c,TABLE,environment='dev',now=lambda:now,source_scope=scope()if descriptor is None else descriptor)


def fails(code,fn):
    with pytest.raises(AggregateStoreFault)as caught:fn()
    assert str(caught.value)==code


def test_disjoint_nonpersonal_scopes_never_replace_each_other_or_legacy_head(client):
    old=AggregateStore(client,TABLE,environment='dev',now=lambda:FIRST).commit(summary())['record']
    a=store(client).commit(scoped())['record']
    b=store(client,scope(2),NEXT).commit(scoped(corrected(),scope(2)))['record']
    assert a['revision']==b['revision']==1 and a['previousRecordSha256']is b['previousRecordSha256']is None
    assert store(client).read(DAY)['record']==a
    assert store(client,scope(2),NEXT).read(DAY)['record']==b
    assert AggregateStore(client,TABLE,environment='dev',now=lambda:NEXT).read(DAY)['record']==old
    assert len({r['PK']['S']for r in rows(client)})==3
    assert a['schemaVersion']==b['schemaVersion']==REVISION_V2
    assert all(r['expiresAtEpoch']=={'N':str(START+90*86400)}for r in rows(client))


@pytest.mark.parametrize('mismatch',['other-generation','legacy-in-scoped','scoped-in-legacy','wrong-population'])
def test_scope_mismatch_refused_before_any_storage_call(client,monkeypatch,mismatch):
    s=store(client);report=scoped()
    if mismatch=='other-generation':report=scoped(descriptor=scope(2))
    elif mismatch=='legacy-in-scoped':report=summary()
    elif mismatch=='scoped-in-legacy':s=AggregateStore(client,TABLE,environment='dev',now=lambda:FIRST)
    else:report=scoped(descriptor=scope(population='selected_dev'))
    monkeypatch.setattr(client,'get_item',lambda **k:pytest.fail('scope mismatch performed read'))
    monkeypatch.setattr(client,'transact_write_items',lambda **k:pytest.fail('scope mismatch performed write'))
    fails('SCOPE_MISMATCH',lambda:s.commit(report))


@pytest.mark.parametrize('generation',[1,2])
def test_selected_actor_scope_cannot_commit_or_read_even_with_valid_uuid(client,monkeypatch,generation):
    descriptor=scope(generation,'selected_dev');s=store(client,descriptor)
    monkeypatch.setattr(client,'get_item',lambda **k:pytest.fail('unqualified scope read'))
    monkeypatch.setattr(client,'transact_write_items',lambda **k:pytest.fail('unqualified scope write'))
    fails('SCOPE_UNQUALIFIED',lambda:s.commit(scoped(descriptor=descriptor)))
    fails('SCOPE_UNQUALIFIED',lambda:s.read(DAY))


def test_constructor_and_property_capture_scope_no_external_alias_retargets(client):
    descriptor=scope();s=store(client,descriptor);descriptor['generation']=scope(2)['generation']
    returned=s.source_scope;returned['generation']=scope(3)['generation']
    assert s.source_scope==scope()
    assert s.commit(scoped())['decision']=='COMMITTED'
    fails('SCOPE_MISMATCH',lambda:s.commit(scoped(descriptor=descriptor)))


@pytest.mark.parametrize('bad',[{'owner':'private'}, {'coverage':'complete'}, {'generation':'owner-hash'},
 {'generation':'00000000-0000-5000-8000-000000000000'},{'schemaVersion':True}, {'sourceContract':'arbitrary'}])
def test_scope_extensions_unsupported_hashlike_and_coverage_lies_rejected(bad):
    descriptor=scope()|bad
    with pytest.raises(ScopeFault):validate_scope(descriptor,environment='dev')
    with pytest.raises(RevisionFault):scoped(descriptor=descriptor)


def test_partial_scope_explicit_and_no_silent_legacy_schema_extension():
    descriptor=scope(population='selected_dev')
    r=scoped(descriptor=descriptor)
    assert r['sourceScope']['coverage']=='selected_subset_not_environment_total'
    assert r['producerCoverage']=='not_qualified'
    with pytest.raises(RevisionFault):validate_summary(summary()|{'sourceScope':descriptor})
    with pytest.raises(RevisionFault):validate_summary(r|{'schemaVersion':'1.0.0-operations-summary-candidate.1'})
    with pytest.raises(ScopeFault):validate_scope(descriptor,environment='prod')


def test_planner_refuses_scope_change_even_when_counts_increase():
    a=plan_revision(None,scoped(),generated_at_epoch=FIRST)['record']
    with pytest.raises(RevisionFault):plan_revision(a,scoped(corrected(),scope(2)),generated_at_epoch=NEXT)
    with pytest.raises(RevisionFault):plan_revision(a,corrected(),generated_at_epoch=NEXT)


def test_same_scope_replay_correction_unknown_write_and_no_duplicate_revision(client,monkeypatch):
    a=store(client).commit(scoped())['record'];before=deepcopy(rows(client))
    assert store(client,now=NEXT).commit(scoped(summary(NEXT)))['decision']=='NO_CHANGE'
    assert rows(client)==before
    s=store(client,now=NEXT);real=client.transact_write_items;calls=[]
    def lost(**kw):calls.append(1);real(**kw);raise TimeoutError('PRIVATE_WRITE_ACK')
    monkeypatch.setattr(client,'transact_write_items',lost)
    fails('WRITE_OUTCOME_UNKNOWN',lambda:s.commit(scoped(corrected())))
    assert calls==[1]
    monkeypatch.setattr(client,'transact_write_items',real)
    assert s.commit(scoped(corrected()))['decision']=='NO_CHANGE'
    b=s.read(DAY)['record'];assert b['revision']==2 and b['previousRecordSha256']==a['recordSha256']
    assert len(rows(client))==3 and b['expiresAtEpoch']==a['expiresAtEpoch']


def test_reducer_emits_scoped_version_only_for_explicit_valid_scope():
    r=aggregate_day([],[],local_day=DAY,environment='dev',as_of_epoch=FIRST,source_scope=scope(population='selected_dev'))
    assert r['schemaVersion']==SUMMARY_V2 and r['sourceScope']['coverage']=='selected_subset_not_environment_total'
    assert validate_summary(r)==r


def test_same_scoped_head_still_has_one_atomic_winner(client):
    import threading
    barrier=threading.Barrier(2)
    def submit(i):
        s=store(client,now=FIRST+i);real=s._read
        def synchronized(*a):
            result=real(*a);barrier.wait(timeout=10);return result
        s._read=synchronized
        try:return s.commit(scoped(summary(FIRST+i)))['decision']
        except AggregateStoreFault as e:return e.code
    with ThreadPoolExecutor(max_workers=2)as executor:
        result=list(executor.map(submit,range(2)))
    assert sorted(result)==['COMMITTED','CONDITIONAL_CONFLICT']
    assert len(rows(client))==2


def test_rehashed_other_scope_record_under_original_key_is_corrupt(client):
    from operations_reporting.revisions import _sha
    s=store(client);first=s.commit(scoped())['record']
    changed=deepcopy(first);changed['report']['sourceScope']=scope(2)
    changed['reportSha256']=_sha(changed['report'])
    changed['recordSha256']=_sha({k:v for k,v in changed.items()if k!='recordSha256'})
    validate_revision(changed)
    table_rows=rows(client)
    for row in table_rows:
        if row['SK']=={'S':'HEAD'}:row['recordSha256']={'S':changed['recordSha256']}
        else:row['recordJson']={'S':json.dumps(changed,sort_keys=True,separators=(',',':'))}
        client.put_item(TableName=TABLE,Item=row)
    fails('INVALID_STORED_RECORD',lambda:s.read(DAY))


def test_scoped_logical_retention_and_correction_fences_unchanged(client):
    s=store(client);s.commit(scoped())
    fails('CORRECTION_WINDOW_CLOSED',lambda:store(client,now=START+7*86400).commit(scoped()))
    assert store(client,now=START+90*86400).read(DAY)=={'state':'EXPIRED','record':None}
    fails('AGGREGATE_EXPIRED',lambda:store(client,now=START+90*86400).commit(scoped()))


def test_scope_rejects_nested_or_hostile_values_without_executing_copy_hooks():
    class Hostile:
        def __deepcopy__(self,memo):pytest.fail('caller copy hook executed')
        def __eq__(self,other):pytest.fail('caller equality hook executed')
    for key in ('population','generation','sourceContract','coverage'):
        with pytest.raises(ScopeFault):validate_scope(scope()|{key:Hostile()},environment='dev')
