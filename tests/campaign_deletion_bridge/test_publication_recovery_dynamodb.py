"""Real SDK transactions: owned deletion through frozen/published candidate recovery."""
from .test_progress_dynamodb import world, candidate, run, NOW, TOKEN, PART, OP
import pytest
import progress
from shared_campaign_locators import core
from shared_campaign_locators.publication import digest

PK='CANDIDATE#11111111-1111-4111-8111-111111111111'
ID=PK.split('#')[1]


def setup(world,state='FROZEN',count=2):
    d,put=world;candidate(put,count)
    d.create_table(TableName='intelligence',KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}],BillingMode='PAY_PER_REQUEST')
    summary=progress.get(d,'pipeline',PK,'SUMMARY')|{'candidateId':ID,'periodId':10,'lifecycleState':state,
        'lifecycleOperationId':OP,'lifecycleStartedAtEpoch':NOW-50,'lifecycleInventoryRevision':1}
    aggregate=None
    if state=='PUBLISHED':
        aggregate={'PK':'CAMPAIGN#'+ID,'SK':'AGGREGATE','campaignId':ID,'state':'PENDING_REVIEW','version':1,'expiresAt':NOW-50+400*86400,'contributorCount':12}
        summary['lifecycleAggregateDigest']=digest({k:v for k,v in aggregate.items() if k not in ('state','version')})
        d.put_item(TableName='intelligence',Item=progress.wire(aggregate))
    if state=='SUPPRESSED':summary['lifecycleAggregateDigest']=digest({})
    put(summary)
    # Real canonical other-owner pairs (the base repair fixture has only metadata).
    import base64
    for i in range(count):
        old=progress.get(d,'pipeline',PK,f'CONTRIB#other{i:03}')
        d.delete_item(TableName='pipeline',Key=progress.key(PK,old['SK']))
        token=base64.urlsafe_b64encode(i.to_bytes(32,'big')).decode().rstrip('=')
        row=old|{'SK':'CONTRIB#'+token,'periodId':10,'GSI1PK':'CONTRIB#10#'+token,'GSI1SK':PK}
        put(row);put(core.locator_for_target(row,'dev'))
    return d,put,summary,aggregate


def finish(d):
    for _ in range(30):
        result=run(d,max_steps=2,intelligence_table='intelligence')
        if result['locatorPassEnded']:return result
    pytest.fail('bounded cleanup did not finish')


def test_frozen_deletion_reconstructs_then_unfreezes_without_resetting_deadline(world):
    d,put,before,_=setup(world)
    run(d,max_steps=3,intelligence_table='intelligence')
    repairing=progress.get(d,'pipeline',PK,'SUMMARY')
    assert repairing['lifecycleState']=='REPAIRING'
    finish(d)
    after=progress.get(d,'pipeline',PK,'SUMMARY')
    assert after['lifecycleState']=='FROZEN' and after['contributorCount']==2
    assert after['signalIds']==['retained_signal'] and after['indicatorIds']==['retained_indicator']
    for name in ('expiresAt','lifecycleStartedAtEpoch','lifecycleOperationId','lifecycleInventoryRevision'):
        assert after[name]==before[name]
    assert progress.get(d,'pipeline',PK,'DELETION_RECOMPUTE') is None


@pytest.mark.parametrize('state',['PUBLISHED','SUPPRESSED'])
def test_committed_publication_cleanup_consumes_all_pairs_preserving_aggregate(world,state):
    d,put,summary,aggregate=setup(world,state,12)
    finish(d)
    assert progress.get(d,'pipeline',PK,'SUMMARY') is None
    assert progress.get(d,'intelligence','CAMPAIGN#'+ID,'AGGREGATE')==aggregate
    rows=[progress.plain(v) for v in d.scan(TableName='pipeline')['Items']]
    assert not any(v['PK']==PK or v.get('recordType')=='CAMPAIGN_CONTRIBUTOR_LOCATOR' for v in rows)


def test_changed_aggregate_prevents_owned_pair_erasure(world):
    d,put,summary,aggregate=setup(world,'PUBLISHED')
    run(d,max_steps=2,intelligence_table='intelligence')
    d.put_item(TableName='intelligence',Item=progress.wire(aggregate|{'contributorCount':13}))
    with pytest.raises(core.LocatorUnavailable):run(d,max_steps=1,intelligence_table='intelligence')
    assert progress.get(d,'pipeline',PK,'CONTRIB#'+TOKEN) is not None
    assert progress.get(d,'pipeline',PK,'SUMMARY')==summary


def test_aggregate_creation_race_rolls_back_frozen_pair_delete(world):
    d,put,summary,_=setup(world);run(d,max_steps=2,intelligence_table='intelligence')
    class Race:
        def __getattr__(self,name):return getattr(d,name)
        def transact_write_items(self,**kw):
            d.put_item(TableName='intelligence',Item=progress.wire({'PK':'CAMPAIGN#'+ID,'SK':'AGGREGATE'}))
            return d.transact_write_items(**kw)
    with pytest.raises(Exception):run(Race(),max_steps=1,intelligence_table='intelligence')
    assert progress.get(d,'pipeline',PK,'CONTRIB#'+TOKEN) is not None
    assert progress.get(d,'pipeline',PK,'SUMMARY')==summary


def test_lost_published_pair_delete_ack_resumes_without_aggregate_write(world):
    d,put,summary,aggregate=setup(world,'PUBLISHED');run(d,max_steps=3,intelligence_table='intelligence')
    class Lost:
        def __getattr__(self,name):return getattr(d,name)
        def transact_write_items(self,**kw):
            d.transact_write_items(**kw);raise RuntimeError('lost')
    with pytest.raises(RuntimeError):run(Lost(),max_steps=1,intelligence_table='intelligence')
    finish(d)
    assert progress.get(d,'intelligence','CAMPAIGN#'+ID,'AGGREGATE')==aggregate


def test_saved_delete_cursor_can_resume_after_other_cleanup_consumes_exact_pair(world):
    d,put,summary,aggregate=setup(world,'PUBLISHED');run(d,max_steps=2,intelligence_table='intelligence')
    state=progress.get(d,'pipeline',PART,'TOMBSTONE')['locatorCleanupState']
    d.transact_write_items(TransactItems=core.paired_delete_actions('pipeline',state['locator']))
    finish(d)
    assert progress.get(d,'pipeline',PK,'SUMMARY') is None
    assert progress.get(d,'intelligence','CAMPAIGN#'+ID,'AGGREGATE')==aggregate


def test_missing_locator_with_surviving_target_never_skips_evidence(world):
    d,put,summary,_=setup(world);run(d,max_steps=2,intelligence_table='intelligence')
    locator=progress.get(d,'pipeline',PART,'TOMBSTONE')['locatorCleanupState']['locator']
    d.delete_item(TableName='pipeline',Key=progress.key(locator['PK'],locator['SK']))
    with pytest.raises(Exception):run(d,max_steps=1,intelligence_table='intelligence')
    assert progress.get(d,'pipeline',PK,'CONTRIB#'+TOKEN) is not None


def test_summary_ttl_loss_consumes_exact_validated_orphan_repair(world):
    d,put=world;candidate(put,2);run(d,max_steps=4)
    saved=progress.get(d,'pipeline',PK,'DELETION_RECOMPUTE');assert saved
    d.delete_item(TableName='pipeline',Key=progress.key(PK,'SUMMARY'))
    finish(d)
    assert progress.get(d,'pipeline',PK,'DELETION_RECOMPUTE') is None


def test_unknown_orphan_checkpoint_preserved(world):
    d,put=world;candidate(put,2);run(d,max_steps=4)
    saved=progress.get(d,'pipeline',PK,'DELETION_RECOMPUTE')|{'unknown':'retained'};put(saved)
    d.delete_item(TableName='pipeline',Key=progress.key(PK,'SUMMARY'))
    with pytest.raises(Exception):finish(d)
    assert progress.get(d,'pipeline',PK,'DELETION_RECOMPUTE')==saved


def test_orphan_summary_recreation_race_preserves_checkpoint(world):
    d,put=world;candidate(put,2);run(d,max_steps=4)
    summary=progress.get(d,'pipeline',PK,'SUMMARY');saved=progress.get(d,'pipeline',PK,'DELETION_RECOMPUTE')
    d.delete_item(TableName='pipeline',Key=progress.key(PK,'SUMMARY'))
    class Race:
        def __getattr__(self,name):return getattr(d,name)
        def transact_write_items(self,**kw):put(summary);return d.transact_write_items(**kw)
    with pytest.raises(Exception):run(Race(),max_steps=1)
    assert progress.get(d,'pipeline',PK,'DELETION_RECOMPUTE')==saved


def test_lost_final_repair_ack_reconciles_frozen_summary_without_retention_reset(world):
    d,put,before,_=setup(world);run(d,max_steps=4,intelligence_table='intelligence')
    class Lost:
        def __getattr__(self,name):return getattr(d,name)
        def transact_write_items(self,**kw):
            result=d.transact_write_items(**kw)
            if any(a.get('Delete',{}).get('Key')==progress.key(PK,'DELETION_RECOMPUTE') for a in kw['TransactItems']):
                raise RuntimeError('lost final repair ack')
            return result
    with pytest.raises(RuntimeError):run(Lost(),max_steps=1,intelligence_table='intelligence')
    assert progress.get(d,'pipeline',PK,'SUMMARY')['lifecycleState']=='FROZEN'
    assert progress.get(d,'pipeline',PART,'TOMBSTONE')['locatorCleanupState']['phase']=='RECOMPUTE'
    frozen=progress.get(d,'pipeline',PK,'SUMMARY')
    finish(d)
    assert progress.get(d,'pipeline',PK,'SUMMARY')==frozen
    assert frozen['expiresAt']==before['expiresAt'] and frozen['lifecycleStartedAtEpoch']==before['lifecycleStartedAtEpoch']


def test_frozen_repair_reconciliation_requires_exact_owned_pair_absence(world):
    d,put,_,_=setup(world);run(d,max_steps=4,intelligence_table='intelligence')
    state=progress.get(d,'pipeline',PART,'TOMBSTONE')['locatorCleanupState']
    # Simulate another account completing the shared candidate repair first.
    assert progress.recompute_page(d,'pipeline',PK,NOW,environment='dev',intelligence_table='intelligence',erased_locator=state['locator'])
    put(state['locator'])
    with pytest.raises(Exception):run(d,max_steps=1,intelligence_table='intelligence')
    assert progress.get(d,'pipeline',PART,'TOMBSTONE')['locatorCleanupState']['phase']=='RECOMPUTE'


def test_two_owned_repair_cursors_share_recompute_and_both_resume(world):
    import base64,locator_progress
    d,put,_,_=setup(world);run(d,max_steps=4,intelligence_table='intelligence')
    other_token=base64.urlsafe_b64encode(bytes(32)).decode().rstrip('=')
    other_partition='CONTRIB#10#'+other_token
    other_op='00000000-0000-4000-8000-000000000002'
    tomb=progress.get(d,'pipeline',PART,'TOMBSTONE')
    put({k:v for k,v in (tomb|{'PK':other_partition}).items() if k not in ('locatorCleanupState','locatorCleanupRevision')})
    for _ in range(10):
        value=locator_progress.sweep(d,'pipeline','dev',other_partition,other_op,NOW,max_steps=2,intelligence_table='intelligence')
        if value['locatorPassEnded']:break
    else:pytest.fail('second repair did not complete')
    frozen=progress.get(d,'pipeline',PK,'SUMMARY')
    assert frozen['lifecycleState']=='FROZEN' and frozen['contributorCount']==1
    finish(d)
    assert progress.get(d,'pipeline',PK,'SUMMARY')==frozen


def test_publication_from_different_inventory_revision_is_not_adopted(world):
    d,put,summary,_=setup(world);put(summary|{'lifecycleInventoryRevision':2})
    run(d,max_steps=2,intelligence_table='intelligence')
    with pytest.raises(Exception):run(d,max_steps=1,intelligence_table='intelligence')
    assert progress.get(d,'pipeline',PK,'CONTRIB#'+TOKEN) is not None
