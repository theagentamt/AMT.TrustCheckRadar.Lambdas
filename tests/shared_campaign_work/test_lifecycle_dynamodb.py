"""Authoritative work-index drain, never a marker/legacy migration test."""
import pytest
from copy import deepcopy
from tests.shared_campaign_work.test_transactions_dynamodb import world,create,PERIOD,NOW,TARGET
from shared_campaign_work import records as R,configuration as C
from shared_campaign_work.lifecycle import Lifecycle
from shared_campaign_work.transactions import TrackedClient
from shared_campaign_locators import period as P


def engine(world,monkeypatch,now=None):
    writer,*_=world;monkeypatch.setenv('CAMPAIGN_PERIOD_LIFECYCLE_ENABLED','true')
    return Lifecycle(writer.client,now=lambda:now or (PERIOD+1)*P.PERIOD_SECONDS+P.RECOVERY_SECONDS)


def test_real_index_drain_removes_target_pair_and_seals_without_retiring(world,monkeypatch):
    writer,d,put,get,*_=world;create(writer)
    e=engine(world,monkeypatch);result=e.period_pass(PERIOD)
    assert result['sealed'] and result['backlog']==0 and result['attempted']==1
    assert get({'PK':TARGET['PK'],'SK':TARGET['SK']}) is None
    assert get(R.work_key(PERIOD,1)) is None
    registry=get({'PK':f'PERIOD#{PERIOD}','SK':'HMAC_KEY'})
    assert registry['admissionState']=='SEALED' and registry['status']=='ENABLED'
    assert registry['retireAfterEpoch']==(PERIOD+1)*P.PERIOD_SECONDS+P.RECOVERY_SECONDS


def test_poison_first_work_does_not_starve_later_valid_work_or_seal(world,monkeypatch):
    writer,d,put,get,*_=world;create(writer)
    other=TARGET|{'PK':'EVENT#68d3017c-4210-4b19-a08b-2921b1e6999a'};create(writer,other)
    first=get(R.work_key(PERIOD,1));put(first|{'recordType':'UNKNOWN'})
    result=engine(world,monkeypatch).period_pass(PERIOD)
    assert result['unverified']==1 and result['attempted']==1 and result['backlog']==1 and not result['sealed']
    assert get({'PK':other['PK'],'SK':other['SK']}) is None
    assert get({'PK':TARGET['PK'],'SK':TARGET['SK']})==TARGET


def test_fixed_high_water_and_eight_attempt_bound_wrap_pending_work(world,monkeypatch):
    from uuid import UUID
    writer,d,put,get,*_=world
    for i in range(12):create(writer,TARGET|{'PK':'EVENT#'+str(UUID(int=(4<<76)|(2<<62)|i))})
    e=engine(world,monkeypatch);first=e.period_pass(PERIOD)
    assert first['attempted']==8 and not first['fullPass'] and first['backlog']==4
    second=e.period_pass(PERIOD)
    assert second['attempted']==4 and second['fullPass'] and second['sealed']


def test_missing_control_unknown_target_and_binding_do_not_claim_empty(world,monkeypatch):
    writer,d,put,get,*_=world;create(writer)
    d.delete_item(TableName='pipeline',Key=C.wire(R.control_key(PERIOD)))
    with pytest.raises(R.WorkUnavailable):engine(world,monkeypatch).period_pass(PERIOD)
    assert get({'PK':TARGET['PK'],'SK':TARGET['SK']})==TARGET


def test_closing_does_not_erase_unexpired_pipeline_rows(world,monkeypatch):
    writer,d,put,get,*_=world;create(writer)
    result=engine(world,monkeypatch,NOW).period_pass(PERIOD)
    assert result['attempted']==0 and not result['sealed'] and result['backlog']==1
    assert get({'PK':TARGET['PK'],'SK':TARGET['SK']})==TARGET


def test_drain_stops_new_tombstone_and_event_allocations(world,monkeypatch):
    writer,d,put,get,registry,*_=world;create(writer)
    e=engine(world,monkeypatch);changed=e.begin_drain(PERIOD)
    tracked=TrackedClient(writer.client,registries=[changed],now=e.now)
    with pytest.raises(R.WorkUnavailable):create(tracked,TARGET|{'PK':'EVENT#68d3017c-4210-4b19-a08b-2921b1e6999a','expiresAt':e.now()+50,'GSI3SK':e.now()+50})
    assert get(R.control_key(PERIOD))['pendingCount']==1


def test_mid_candidate_budget_stops_before_next_write(world,monkeypatch):
    writer,d,put,get,*_=world;create(writer)
    e=engine(world,monkeypatch);calls=[]
    def remaining():calls.append(1);return 30000 if len(calls)<3 else 0
    e.remaining=remaining
    with pytest.raises(R.WorkUnavailable):e.period_pass(PERIOD)
    assert get({'PK':TARGET['PK'],'SK':TARGET['SK']})==TARGET


@pytest.mark.parametrize('bad',[None,False,'unknown'])
def test_canonical_work_key_advances_missing_invalid_payload_ordinal(world,monkeypatch,bad):
    writer,d,put,get,*_=world;create(writer)
    create(writer,TARGET|{'PK':'EVENT#68d3017c-4210-4b19-a08b-2921b1e6999a'})
    poisoned=get(R.work_key(PERIOD,1))
    if bad is None:poisoned.pop('ordinal')
    else:poisoned['ordinal']=bad
    put(poisoned)
    result=engine(world,monkeypatch).period_pass(PERIOD)
    assert result['unverified']==1 and result['attempted']==1 and result['backlog']==1
    assert get(R.control_key(PERIOD))['passCursor']==2 and not result['sealed']


def test_pipeline_expiry_is_enforced_before_whole_period_drain(world,monkeypatch):
    writer,d,put,get,*_=world;create(writer)
    result=engine(world,monkeypatch,NOW+101).period_pass(PERIOD)
    assert result['attempted']==1 and result['backlog']==0 and not result['sealed']
    assert get({'PK':TARGET['PK'],'SK':TARGET['SK']}) is None
    assert get(R.work_key(PERIOD,1)) is None


def test_malformed_locator_cannot_delete_other_period_target(world,monkeypatch):
    from shared_campaign_locators.core import locator_for_target
    writer,d,put,get,registry,marker,control=world
    token='A'*43;event='68d3017c-4210-4b19-a08b-2921b1e6999a'
    feature={'PK':'EVENT#'+event,'SK':'FEATURE','periodId':PERIOD,'GSI1PK':f'CONTRIB#{PERIOD}#{token}',
             'expiresAt':NOW+100,'GSI3PK':'EXPIRY#dev','GSI3SK':NOW+100}
    locator=locator_for_target(feature,'dev')
    create(writer,locator)
    # Exact authoritative index survives corruption of only the target payload.
    put(locator|{'targetSK':'DEDUPE'})
    other=TARGET|{'PK':'EVENT#'+event,'expiresAt':NOW+999999,'GSI3SK':NOW+999999}
    put(other)
    result=engine(world,monkeypatch).period_pass(PERIOD)
    assert result['unverified']==1 and not result['sealed']
    assert get({'PK':other['PK'],'SK':other['SK']})==other
    assert get(R.control_key(PERIOD))['pendingCount']==1
