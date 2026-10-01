"""Migration uses actual conditional transactions; approval remains external."""
from copy import deepcopy
import pytest
from tests.shared_campaign_work.test_transactions_dynamodb import world,PERIOD,NOW
from shared_campaign_work import bootstrap as B,configuration as C,records as R


def prepare(world):
    writer,d,put,get,modern,marker,control=world
    old={k:v for k,v in modern.items() if k not in ('workSchemaVersion','workManifestSha256','workInventoryRevision')}
    old['admissionSchemaVersion']=1;put(old)
    d.delete_item(TableName='pipeline',Key=C.wire(R.control_key(PERIOD)))
    row={'PK':f'CONTRIB#{PERIOD}#'+'A'*43,'SK':'TOMBSTONE','recordType':'CAMPAIGN_DELETION_TOMBSTONE',
        'schemaVersion':2,'environment':'dev','periodId':PERIOD,'createdAtEpoch':NOW-100,'deletionDeadlineEpoch':NOW+100,
        'GSI3PK':'EXPIRY#dev','GSI3SK':NOW+100,'locatorCleanupRevision':3,
        'locatorCleanupState':{'operationId':'11111111-1111-4111-8111-111111111111','phase':'SEEK','cursor':None},
        'retainedPeriodSweepRevision':2,'retainedPeriodSweep':{'schemaVersion':1,'operationId':'11111111-1111-4111-8111-111111111111',
            'inventoryRevision':1,'minimumPeriodId':PERIOD,'maximumPeriodId':PERIOD,'nextPeriodId':PERIOD}}
    put(row)
    return d,put,get,old,marker,row


def test_exact_migration_preserves_suppression_clocks_and_creates_authoritative_pairs(world):
    d,put,get,old,marker,row=prepare(world)
    actions=B.actions([old],[row],marker,C.pins(),NOW)
    d.transact_write_items(TransactItems=actions)
    assert get({'PK':row['PK'],'SK':row['SK']})==row
    assert get(R.control_key(PERIOD))['pendingCount']==1
    assert get(R.work_key(PERIOD,1))['deadlineEpoch']==row['deletionDeadlineEpoch']
    assert get(R.lookup_key('pipeline',row['PK'],row['SK']))['ordinal']==1
    assert get({'PK':old['PK'],'SK':old['SK']})['admissionChangedAtEpoch']==old['admissionChangedAtEpoch']
    with pytest.raises(Exception):d.transact_write_items(TransactItems=actions)
    assert get(R.control_key(PERIOD))['pendingCount']==1


def test_target_race_rolls_back_all_migration_actions(world):
    d,put,get,old,marker,row=prepare(world);actions=B.actions([old],[row],marker,C.pins(),NOW)
    put(row|{'locatorCleanupRevision':4})
    with pytest.raises(Exception):d.transact_write_items(TransactItems=actions)
    assert get(R.control_key(PERIOD)) is None and get(R.work_key(PERIOD,1)) is None
    assert get({'PK':old['PK'],'SK':old['SK']})==old


@pytest.mark.parametrize('change',[{'extra':True},{'deletionDeadlineEpoch':True},{'locatorCleanupState':{'phase':'DELETE'}},
    {'retainedPeriodSweep':{}},{'expiresAt':NOW+100},{'periodId':PERIOD+1}])
def test_unknown_or_unfinished_tombstones_refuse_without_mutation(world,change):
    d,put,get,old,marker,row=prepare(world)
    with pytest.raises(R.WorkUnavailable):B.actions([old],[row|change],marker,C.pins(),NOW)
    assert get(R.control_key(PERIOD)) is None and get({'PK':row['PK'],'SK':row['SK']})==row


def test_duplicate_target_and_unknown_registry_fail_closed(world):
    d,put,get,old,marker,row=prepare(world)
    for registries,rows in (([old],[row,row]),([old|{'extra':True}],[row]),([old,old],[row])):
        with pytest.raises(Exception):B.actions(registries,rows,marker,C.pins(),NOW)
    assert get(R.control_key(PERIOD)) is None


def test_prior_global_period_bootstrap_never_seals_or_retires_before_recovery_deadline(world,monkeypatch):
    from tests.campaign_period_fixtures import row as registry_row
    from shared_campaign_locators import period as P
    from shared_campaign_work.lifecycle import Lifecycle
    d,put,get,old,marker,tomb=prepare(world);prior=PERIOD-1
    legacy={k:v for k,v in registry_row(prior).items() if k in P.BASE_FIELDS}
    put(legacy);marker=marker|{'minimumPeriodId':prior};put(marker)
    actions=B.actions([legacy,old],[tomb],marker,C.pins(),NOW)
    d.transact_write_items(TransactItems=actions)
    upgraded=get({'PK':legacy['PK'],'SK':legacy['SK']})
    assert upgraded['admissionState']=='CLOSING' and upgraded['retireAfterEpoch']>NOW
    assert not any(k.startswith(('seal','retirement')) for k in upgraded)
    writer=world[0];monkeypatch.setenv('CAMPAIGN_PERIOD_LIFECYCLE_ENABLED','true')
    engine=Lifecycle(writer.client,now=lambda:NOW)
    with pytest.raises(R.WorkUnavailable):engine.begin_drain(prior)
    assert not engine.period_pass(prior)['sealed']
    assert get({'PK':legacy['PK'],'SK':legacy['SK']})==upgraded
