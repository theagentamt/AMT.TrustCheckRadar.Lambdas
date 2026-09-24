"""Real SDK/Moto reconstruction, pagination and optimistic-fence regressions."""
import os
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run isolated SDK tests', allow_module_level=True)
from tests.campaign_deletion_bridge.test_progress_dynamodb import (
    world, candidate, progress, NOW, COMMAND, run, TOKEN)
from shared_campaign_contracts.metadata import empty_metadata

PK='CANDIDATE#11111111-1111-4111-8111-111111111111'


def recompute(d):
    return progress.recompute_page(progress.CommandGuardedClient(d,'ledger',COMMAND),'pipeline',PK,NOW)


def row(d, sk): return progress.get(d,'pipeline',PK,sk)


def finish(d):
    for _ in range(10):
        if recompute(d): return
    pytest.fail('bounded fixture did not finish')


def test_deleted_metadata_removed_only_after_complete_paginated_repair(world):
    d,put=world;candidate(put,61)
    original=row(d,'SUMMARY')|{'lexicalFingerprint':['ffffffffffffffff'],'signalIds':['deleted_signal'],
                               'indicatorIds':['deleted_indicator']}
    put(original)
    run(d,max_steps=4)
    assert not recompute(d)
    assert row(d,'SUMMARY')==original|{'version':2}
    assert row(d,'DELETION_RECOMPUTE')['metadataSchemaVersion']==1
    finish(d)
    result=row(d,'SUMMARY')
    assert result['lexicalFingerprint']==['0123456789abcdef']
    assert result['signalIds']==['retained_signal'] and result['indicatorIds']==['retained_indicator']
    assert result['expiresAt']==original['expiresAt'] and result['contributorCount']==61
    assert row(d,'DELETION_RECOMPUTE') is None
    assert progress.get(d,'ledger',COMMAND['PK'],'ACCOUNT_DELETION#CAMPAIGN') is None


def test_bounded_unions_include_later_pages_and_are_order_independent(world):
    d,put=world;candidate(put,51)
    # Remove the fixture's deleted owner before initializing this isolated repair.
    d.delete_item(TableName='pipeline',Key=progress.key(PK,'CONTRIB#'+TOKEN))
    for i in range(51):
        original=row(d,f'CONTRIB#other{i:03}')
        value=50-i  # smallest values occur on the last page
        put(original|{'lexicalFingerprint':[f'{value:016x}'], 'signalIds':[f's{value:02}'], 'indicatorIds':[f'i{value:02}']})
    finish(d);result=row(d,'SUMMARY')
    assert result['lexicalFingerprint']==[f'{v:016x}' for v in range(32)]
    assert result['signalIds']==[f's{v:02}' for v in range(16)]
    assert result['indicatorIds']==[f'i{v:02}' for v in range(16)]


@pytest.mark.parametrize('bad',[
    {'metadataSchemaVersion':None}, {'metadataSchemaVersion':True}, {'metadataSchemaVersion':2},
    {'lexicalFingerprint':['raw input']}, {'signalIds':['same','same']}, {'indicatorIds':['x']*17},
])
def test_unknown_live_contribution_never_publishes_or_changes_checkpoint(world,bad):
    d,put=world;candidate(put,1);assert not recompute(d)
    put(row(d,'CONTRIB#other000')|bad)
    before=row(d,'SUMMARY');saved=row(d,'DELETION_RECOMPUTE')
    with pytest.raises(progress.CoverageUnavailable):recompute(d)
    assert row(d,'SUMMARY')==before and row(d,'DELETION_RECOMPUTE')==saved
    assert row(d,'CONTRIB#other000') is not None


@pytest.mark.parametrize('change_version',[False,True])
def test_legacy_checkpoint_is_not_reset_even_when_summary_version_changes(world,change_version):
    d,put=world;candidate(put,1);assert not recompute(d)
    legacy=row(d,'DELETION_RECOMPUTE');legacy.pop('metadataSchemaVersion');put(legacy)
    if change_version: put(row(d,'SUMMARY')|{'version':8})
    before=row(d,'SUMMARY')
    with pytest.raises(progress.CoverageUnavailable):recompute(d)
    assert row(d,'DELETION_RECOMPUTE')==legacy and row(d,'SUMMARY')==before


def test_qualified_version_restart_discards_all_previous_partial_metadata(world):
    d,put=world;candidate(put,31);assert not recompute(d);assert not recompute(d)
    assert row(d,'DELETION_RECOMPUTE')['contributors']==25
    put(row(d,'SUMMARY')|{'version':2});assert not recompute(d)
    restarted=row(d,'DELETION_RECOMPUTE')
    assert restarted['contributors']==0 and restarted['cursor'] is None
    assert all(restarted[k]==v for k,v in empty_metadata().items())
    assert restarted['expiresAt']==NOW+1000


def test_expired_contribution_metadata_not_copied_even_before_physical_ttl(world):
    d,put=world;candidate(put,1)
    put(row(d,'CONTRIB#'+TOKEN)|{'expiresAt':NOW,'lexicalFingerprint':['ffffffffffffffff']})
    finish(d)
    assert row(d,'SUMMARY')['lexicalFingerprint']==['0123456789abcdef']
    assert row(d,'CONTRIB#'+TOKEN) is not None


def test_commit_time_summary_change_preserves_old_metadata_and_checkpoint(world):
    d,put=world;candidate(put,1);assert not recompute(d)
    before=row(d,'DELETION_RECOMPUTE')
    changed=row(d,'SUMMARY')|{'version':2,'indicatorIds':['concurrent']}
    class Race:
        def __getattr__(self,name): return getattr(d,name)
        def transact_write_items(self,**kwargs):
            put(changed)
            return d.transact_write_items(**kwargs)
    with pytest.raises(d.exceptions.TransactionCanceledException):recompute(Race())
    assert row(d,'SUMMARY')==changed and row(d,'DELETION_RECOMPUTE')==before


def test_commit_time_deletion_fence_change_rolls_back_all_metadata(world):
    d,put=world;candidate(put,1);assert not recompute(d)
    before=row(d,'SUMMARY');saved=row(d,'DELETION_RECOMPUTE')
    put(COMMAND|{'status':'COMPLETE'},'ledger')
    with pytest.raises(d.exceptions.TransactionCanceledException):recompute(d)
    assert row(d,'SUMMARY')==before and row(d,'DELETION_RECOMPUTE')==saved


def test_lost_final_ack_replay_preserves_reconstructed_values_and_deadline(world):
    d,put=world;candidate(put,1);assert not recompute(d)
    class Lost:
        def __getattr__(self,name): return getattr(d,name)
        def transact_write_items(self,**kwargs):
            d.transact_write_items(**kwargs);raise RuntimeError('synthetic lost response')
    with pytest.raises(RuntimeError):recompute(Lost())
    before=row(d,'SUMMARY');assert row(d,'DELETION_RECOMPUTE') is None
    finish(d)
    after=row(d,'SUMMARY')
    assert {k:v for k,v in after.items() if k!='version'}=={k:v for k,v in before.items() if k!='version'}
