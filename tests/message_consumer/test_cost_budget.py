"""Durable aggregate currency reservations use real Moto conditional writes."""
import sys
from pathlib import Path
import pytest
sys.path[:0]=[str(Path(__file__).resolve().parents[2]/'src'),str(Path(__file__).resolve().parents[2]/'tests/shared_check_authority')]
from test_transactions import world
from message_consumer.budget import ProviderBudget,KEY
from shared_message_contract.provider_cost import CostPolicy
from shared_message_contract.validation import MessageError
from shared_check_authority.core import AuthorityError

COST=CostPolicy('fixture-model-2026-01-01',1024,512,2,4)


def budget(a,**changes):
    return ProviderBudget(a,**({'window_seconds':60,'max_attempts':10,'max_failures':9,
        'circuit_open':False,'cost_policy':COST,'max_reserved_nano':2*COST.reservation_nano}|changes))


def test_currency_limit_and_uncertain_attempt_are_never_refunded(world):
    a,*_=world;b=budget(a)
    window=b.reserve();b.failed(window)
    assert b.reserve()==window and b.reserve() is None
    row=a._get(a.s.authority_table,KEY)
    assert row['reservedNanoUsd']==2*COST.reservation_nano and row['attempts']==2
    assert row['failures']==1 and row['unpricedAttempts']==0


def test_reservation_ack_lost_after_real_commit_stays_consumed(world,monkeypatch):
    a,*_=world;b=budget(a,max_reserved_nano=COST.reservation_nano)
    original=a._transact;calls=[]
    def commit_then_lose_response(items):
        original(items);calls.append(1)
        raise AuthorityError('TRANSACTION_UNCERTAIN')
    monkeypatch.setattr(a,'_transact',commit_then_lose_response)
    with pytest.raises(AuthorityError):b.reserve()
    row=a._get(a.s.authority_table,KEY)
    assert row['attempts']==1 and row['reservedNanoUsd']==COST.reservation_nano
    assert b.reserve() is None and calls==[1]
    assert a._get(a.s.authority_table,KEY)==row


@pytest.mark.parametrize('missing',['reservedNanoUsd','unpricedAttempts'])
def test_incomplete_priced_metadata_cannot_be_erased_by_legacy_call(world,missing):
    a,*_=world;budget(a).reserve()
    a.ddb.Table(a.s.authority_table).update_item(Key=KEY,UpdateExpression='REMOVE '+missing)
    before=a._get(a.s.authority_table,KEY)
    legacy=ProviderBudget(a,window_seconds=60,max_attempts=10,max_failures=9,circuit_open=False)
    with pytest.raises(MessageError):legacy.reserve()
    assert a._get(a.s.authority_table,KEY)==before


def test_rollover_and_config_changes_do_not_reset_live_currency(world):
    a,*_=world;clock=world[-1];b=budget(a)
    start=b.reserve()
    more_expensive=CostPolicy(COST.model,1024,512,4,8)
    assert budget(a,cost_policy=more_expensive).reserve() is None
    with pytest.raises(MessageError):budget(a,window_seconds=30).reserve()
    clock[0]+=60
    assert b.reserve()==start+60
    assert a._get(a.s.authority_table,KEY)['reservedNanoUsd']==COST.reservation_nano


def test_stale_cas_cannot_oversubscribe_currency(world,monkeypatch):
    a,*_=world;b=budget(a,max_reserved_nano=3*COST.reservation_nano)
    b.reserve();stale=a._get(a.s.authority_table,KEY);b.reserve()
    original=a._get
    monkeypatch.setattr(a,'_get',lambda table,key:stale if key==KEY else original(table,key))
    with pytest.raises(AuthorityError):b.reserve()
    monkeypatch.setattr(a,'_get',original)
    assert a._get(a.s.authority_table,KEY)['reservedNanoUsd']==2*COST.reservation_nano


def test_priced_hour_cannot_be_reset_by_unpriced_minute_then_priced_hour(world):
    a,*_=world
    priced=budget(a,window_seconds=3600,max_reserved_nano=COST.reservation_nano)
    priced.reserve()
    before=a._get(a.s.authority_table,KEY)
    legacy=ProviderBudget(a,window_seconds=60,max_attempts=10,max_failures=9,circuit_open=False)
    with pytest.raises(MessageError):legacy.reserve()
    world[-1][0]+=120
    with pytest.raises(MessageError):legacy.reserve()
    assert priced.reserve() is None
    assert a._get(a.s.authority_table,KEY)==before


def test_unpriced_or_legacy_active_records_fail_closed(world):
    a,*_=world
    ProviderBudget(a,window_seconds=60,max_attempts=10,max_failures=9,circuit_open=False).reserve()
    with pytest.raises(MessageError):budget(a).reserve()
    assert a._get(a.s.authority_table,KEY)['unpricedAttempts']==1
    world[-1][0]+=60
    assert budget(a).reserve() is not None


def test_unknown_legacy_window_requires_expiry_and_backwards_clock_stops(world):
    a,*_=world;b=budget(a);b.reserve()
    table=a.ddb.Table(a.s.authority_table)
    table.update_item(Key=KEY,UpdateExpression='REMOVE windowSeconds, reservedNanoUsd, unpricedAttempts')
    world[-1][0]+=60
    with pytest.raises(MessageError):b.reserve()
    world[-1][0]+=86400
    b.reserve()
    world[-1][0]-=120
    with pytest.raises(MessageError):b.reserve()


@pytest.mark.parametrize('field,value',[('contextTokens',True),('maxOutputTokens',513),
    ('inputNanoUsdPerToken',0),('outputNanoUsdPerToken',-1),('model','mutable-alias')])
def test_cost_policy_has_no_unsafe_numeric_or_model_defaults(field,value):
    with pytest.raises(MessageError):CostPolicy.from_dict(COST.as_dict()|{field:value})


def test_cost_policy_and_currency_cap_are_required_together(world,monkeypatch):
    with pytest.raises(MessageError):budget(world[0],max_reserved_nano=COST.reservation_nano-1)
    with pytest.raises(MessageError):budget(world[0],cost_policy=None)
    monkeypatch.delenv('MESSAGE_AI_COST_POLICY',raising=False)
    with pytest.raises(MessageError):CostPolicy.from_env()
    monkeypatch.setenv('MESSAGE_AI_COST_POLICY','{"model":"a","model":"b"}')
    with pytest.raises(MessageError):CostPolicy.from_env()
