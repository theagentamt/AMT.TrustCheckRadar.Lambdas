"""Verified access windows never change funded identity or replenish counters."""
import os
import sys
from pathlib import Path
import pytest

if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run isolated authority emulator suite', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).parent))
from test_transactions import world, admit, ACCOUNT, WORKER, PAYLOAD, failure
from test_deletion import setup, finish, receipt
from shared_check_authority import purchase_usage as usage
from shared_check_authority.core import AuthorityError
from shared_check_authority.recovery import Recovery
from shared_check_authority.expiry import Expiry


def state(w):
    a, _, _, row, _, _ = w
    period = row('PERIOD#p1')
    return period, a._get('authority', period['purchaseUsageKey'])


def test_strict_v1_uses_original_funded_deadline(world):
    a, _, _, _, _, clock = world
    period, current = state(world)
    legacy = {k: v for k, v in current.items() if k != 'accessUntilEpoch'} | {'schemaVersion': 1}
    assert set(legacy) == usage.V1_FIELDS
    usage.validate(legacy, period['purchaseUsageKey'], clock[0])
    assert usage.effective_access_end(legacy) == legacy['endEpoch']
    assert usage.usage_deadline(legacy) == legacy['expiresAt']
    a.ddb.Table('authority').put_item(Item=legacy)
    proof, admission = admit(world)
    a.settle(WORKER, proof, admission['executionToken'], 'complete')
    after = a._get('authority', period['purchaseUsageKey'])
    assert after['schemaVersion'] == 1 and 'accessUntilEpoch' not in after
    assert after['usedChecks'] == 1 and after['expiresAt'] == legacy['expiresAt']


@pytest.mark.parametrize('changes', [
    {'schemaVersion': 1}, {'schemaVersion': 3}, {'accessUntilEpoch': True},
    {'accessUntilEpoch': 1}, {'accessUntilEpoch': None}, {'accessUntilEpoch': '2000000000'},
    {'expiresAt': 2000000000}, {'unverifiedAccess': True},
])
def test_unknown_or_invalid_access_shape_remains_untouched(world, changes):
    a, event, _, _, _, _ = world
    period, original = state(world)
    corrupt = original | changes
    a.ddb.Table('authority').put_item(Item=corrupt)
    failure('PURCHASE_USAGE_INVALID', lambda: a.prepare(event, PAYLOAD, 'invalid-access'))
    assert a._get('authority', period['purchaseUsageKey']) == corrupt


def set_access(w, access, *, observed=None):
    """Synthetic trusted proof transaction; provider/source proof belongs to caller."""
    a, _, _, row, _, clock = w
    period, actual = state(w)
    grant = row('ACCESS')
    actions = usage.access_window_actions('authority', period['purchaseUsageKey'],
                                         actual if observed is None else observed, access, now=clock[0])
    updated = period | {'accessUntilEpoch': access}
    projected = grant | {'validUntilEpoch': access}
    a.client.transact_write_items(TransactItems=actions + [
        {'Put': {'TableName': 'authority', 'Item': updated, **usage.exact_condition(period)}},
        {'Put': {'TableName': 'authority', 'Item': projected, **usage.exact_condition(grant)}},
    ])
    return updated


def refresh_jwt(w):
    w[1]['requestContext']['authorizer']['jwt']['claims']['exp'] = str(w[5][0] + 90)


@pytest.mark.parametrize('outcome,charge', [('complete', 1), ('partial', 0), ('failed', 0)])
def test_extended_access_admits_after_funded_end_without_reset(world, outcome, charge):
    a, _, _, row, _, clock = world
    proof, admitted = admit(world, 'before-extension')
    a.settle(WORKER, proof, admitted['executionToken'], 'complete')
    original, global_before = state(world)
    access = int(original['endEpoch']) + 2000
    set_access(world, access)
    clock[0] = int(original['endEpoch']) + 1
    refresh_jwt(world)
    proof, admitted = admit(world, 'during-grace')
    check = row('CHECK#' + proof)
    assert check['accessUntilEpoch'] == access
    result = a.settle(WORKER, proof, admitted['executionToken'], outcome)
    assert a.settle(WORKER, proof, admitted['executionToken'], 'complete') == result
    local, current = state(world)
    assert local['startEpoch'] == original['startEpoch'] and local['endEpoch'] == original['endEpoch']
    assert current['usedChecks'] == local['usedChecks'] == 1 + charge
    assert current['reservedChecks'] == local['reservedChecks'] == 0
    assert current['expiresAt'] == access + usage.RETENTION_SECONDS
    assert current['limit'] == global_before['limit'] == 200


def test_old_pending_receipt_releases_against_extended_global_deadline(world):
    a, _, _, row, _, clock = world
    proof, _ = admit(world)
    original, _ = state(world)
    old_access = row('CHECK#' + proof)['accessUntilEpoch']
    set_access(world, int(original['endEpoch']) + 2000)
    clock[0] += 121
    assert Recovery(a.ddb, 'authority', now=a.now).expire(a._partition(ACCOUNT, 'k1'), proof)
    local, current = state(world)
    assert old_access < local['accessUntilEpoch']
    assert current['reservedChecks'] == local['reservedChecks'] == 0
    assert current['usedChecks'] == 0


def test_shortening_uses_latest_deadline_and_preserves_used(world):
    a, _, _, row, _, clock = world
    proof, admitted = admit(world)
    a.settle(WORKER, proof, admitted['executionToken'], 'complete')
    original, _ = state(world)
    set_access(world, int(original['endEpoch']) + 1000)
    shortened = clock[0] - 1
    set_access(world, shortened)
    local, current = state(world)
    assert current['expiresAt'] == shortened + usage.RETENTION_SECONDS
    assert current['usedChecks'] == local['usedChecks'] == 1
    assert current['endEpoch'] == original['endEpoch'] > current['accessUntilEpoch']
    failure('EXTERNAL_ACCESS_UNAVAILABLE', lambda: a.prepare(world[1], PAYLOAD, 'revoked'))


def test_shortening_with_pending_reservation_is_reconciliation_required(world):
    a, _, _, row, _, clock = world
    admit(world)
    before, global_before = state(world)
    failure('PURCHASE_USAGE_RECONCILIATION_REQUIRED', lambda: set_access(world, clock[0]))
    assert state(world) == (before, global_before)


def test_due_shortening_exact_delete_never_recreates_global(world):
    a, _, _, row, _, clock = world
    original, global_before = state(world)
    set_access(world, int(original['endEpoch']) + 2000)
    # A genuinely old funded period is required; no fabricated revoke clock.
    clock[0] = int(original['endEpoch']) + usage.RETENTION_SECONDS + 10
    set_access(world, int(original['endEpoch']))
    assert a._get('authority', original['purchaseUsageKey']) is None
    local = row('PERIOD#p1')
    assert usage.usage_deadline(local) <= clock[0]
    bridge, command, _ = setup(world)
    assert finish(bridge, command)['complete']
    assert a._get('authority', original['purchaseUsageKey']) is None


def test_due_shortening_cannot_discard_pending_reservation(world):
    a, _, _, _, _, clock = world
    admit(world)
    period, original = state(world)
    clock[0] = int(original['expiresAt']) + 1
    failure('PURCHASE_USAGE_RECONCILIATION_REQUIRED',
            lambda: usage.access_window_actions('authority', period['purchaseUsageKey'], original,
                                               period['endEpoch'], now=clock[0]))
    assert state(world)[1] == original


def test_missing_or_expired_global_cannot_be_extended(world):
    a, _, _, _, _, clock = world
    period, original = state(world)
    pointer = period['purchaseUsageKey']
    failure('PURCHASE_USAGE_RECONCILIATION_REQUIRED',
            lambda: usage.funding_actions('authority', pointer, period['startEpoch'], period['endEpoch'], None,
                                         now=clock[0], access_until_epoch=period['endEpoch'] + 1000))
    clock[0] = int(original['expiresAt'])
    failure('PURCHASE_USAGE_RECONCILIATION_REQUIRED',
            lambda: usage.access_window_actions('authority', pointer, original,
                                               clock[0] + 1000, now=clock[0]))
    assert state(world)[1] == original


def test_global_change_rolls_back_local_access_extension(world):
    from botocore.exceptions import ClientError
    a, _, _, row, _, _ = world
    period, observed = state(world)
    a.ddb.Table('authority').put_item(Item=observed | {'usedChecks': 1})
    with pytest.raises(ClientError):
        set_access(world, int(period['endEpoch']) + 1000, observed=observed)
    assert row('PERIOD#p1') == period
    assert state(world)[1]['usedChecks'] == 1


def test_schema_one_upgrades_only_in_fresh_funding_transaction(world):
    a, _, _, _, _, clock = world
    period, original = state(world)
    legacy = {k: v for k, v in original.items() if k != 'accessUntilEpoch'} | {'schemaVersion': 1, 'usedChecks': 9}
    a.ddb.Table('authority').put_item(Item=legacy)
    actions, seed = usage.funding_actions('authority', period['purchaseUsageKey'], period['startEpoch'],
                                         period['endEpoch'], legacy, now=clock[0])
    assert state(world)[1] == legacy and seed == {'usedChecks': 9, 'reservedChecks': 0}
    a.client.transact_write_items(TransactItems=actions)
    assert state(world)[1]['schemaVersion'] == 2 and state(world)[1]['usedChecks'] == 9


def test_expiry_and_late_recovery_use_extended_deadline(world):
    a, _, _, row, _, clock = world
    proof, _ = admit(world)
    period, original = state(world)
    set_access(world, int(period['endEpoch']) + 2000)
    expiry = Expiry(a.ddb, 'authority', now=a.now)
    clock[0] = int(original['expiresAt'])
    assert not expiry.expire(period['purchaseUsageKey']['PK'], period['purchaseUsageKey']['SK'])
    assert state(world)[1]['reservedChecks'] == 1
    clock[0] += 2000
    assert expiry.expire(period['purchaseUsageKey']['PK'], period['purchaseUsageKey']['SK'])
    assert Recovery(a.ddb, 'authority', now=a.now).expire(a._partition(ACCOUNT, 'k1'), proof)
    assert row('PERIOD#p1')['reservedChecks'] == 0 and state(world)[1] is None


def test_stale_expiry_delete_cannot_remove_concurrent_extended_row(world, monkeypatch):
    a, _, _, _, _, clock = world
    period, original = state(world)
    # Simulate an expiry read interleaving with a proof transaction committed
    # immediately before the old deadline; expiry must CAS the observed row.
    extended = usage.new_period_usage(period['purchaseUsageKey'], period['startEpoch'], period['endEpoch'],
                                     access_until_epoch=period['endEpoch'] + 1000)
    clock[0] = int(original['expiresAt'])
    transact = a.client.transact_write_items
    def race(**kwargs):
        a.ddb.Table('authority').put_item(Item=extended)
        return transact(**kwargs)
    monkeypatch.setattr(a.client, 'transact_write_items', race)
    failure('EXPIRY_TRANSACTION_UNCERTAIN', lambda: Expiry(a.ddb, 'authority', now=a.now).expire(
        period['purchaseUsageKey']['PK'], period['purchaseUsageKey']['SK']))
    assert state(world)[1] == extended


def test_missing_shortened_global_does_not_silently_complete_old_account_erasure(world):
    a, _, _, _, _, clock = world
    period, original = state(world)
    # Another owner shortened/expired the global after ownership release. The
    # old local deadline alone cannot prove why its global disappeared early.
    a.ddb.Table('authority').delete_item(Key=period['purchaseUsageKey'])
    bridge, command, _ = setup(world)
    with pytest.raises(AuthorityError, match='PURCHASE_USAGE_UNAVAILABLE'):
        finish(bridge, command)
    assert receipt(a) is None and state(world)[0] is not None


def test_snapshot_uses_verified_access_end_without_public_schema_change(world):
    from v1_entitlements.service import access_snapshot
    a, event, _, row, _, clock = world
    original, _ = state(world)
    access = int(original['endEpoch']) + 1000
    set_access(world, access)
    clock[0] = int(original['endEpoch']) + 1
    refresh_jwt(world)
    # Isolate unchanged snapshot wire/projection from the provider writer,
    # whose trusted proof/source integration is independently owned/tested.
    class SnapshotWriter:
        trial_retention_approved = True
        def __init__(self): self.a = a
        def _read(self, account): return a._partition(account, 'k1'), row('ACCESS'), {}
        def trial_history(self, account, sources): return None, {}
        def refresh_for_account(self, event, operation): pass
    snapshot = access_snapshot(SnapshotWriter(), event)
    assert set(snapshot) == {'schemaVersion', 'policyVersion', 'activeDevice', 'access', 'allowance', 'trial'}
    assert snapshot['schemaVersion'] == 1
    assert snapshot['allowance'] == {'limit': 200, 'completedUsed': 0, 'reserved': 0,
                                     'remaining': 200, 'periodEndsAtEpoch': access}
    assert snapshot['access']['externalChecksAllowed'] is True
    assert row('PERIOD#p1')['endEpoch'] == original['endEpoch']


def test_extended_period_does_not_make_exhausted_allowance_available(world):
    a, _, _, row, _, _ = world
    period, original = state(world)
    a.ddb.Table('authority').put_item(Item=original | {'usedChecks': 200})
    a.ddb.Table('authority').put_item(Item=period | {'usedChecks': 200})
    set_access(world, int(period['endEpoch']) + 1000)
    failure('ALLOWANCE_EXHAUSTED', lambda: a.prepare(world[1], PAYLOAD, 'no-bonus'))
    assert state(world)[1]['usedChecks'] == row('PERIOD#p1')['usedChecks'] == 200


def test_stale_receipt_cannot_override_shorter_local_or_unknown_access_evidence(world):
    a, _, _, row, _, _ = world
    proof, _ = admit(world)
    period, _ = state(world)
    observed = row('CHECK#' + proof)
    partition = a._partition(ACCOUNT, 'k1')
    for invalid in (True, None, int(period['endEpoch']) + 1):
        failure('PURCHASE_USAGE_MISMATCH', lambda: usage.period_for_receipt(
            a.ddb, 'authority', partition, observed | {'accessUntilEpoch': invalid}))


@pytest.mark.parametrize('repair', [False, True])
def test_admission_after_future_shortening_can_settle_or_recover(world, repair):
    a, _, _, row, _, clock = world
    period, _ = state(world)
    shortened = clock[0] + 500
    assert shortened < period['endEpoch']
    set_access(world, shortened)
    proof, admitted = admit(world)
    assert row('CHECK#' + proof)['accessUntilEpoch'] == shortened
    if repair:
        clock[0] += 121
        assert Recovery(a.ddb, 'authority', now=a.now).expire(a._partition(ACCOUNT, 'k1'), proof)
    else:
        assert a.settle(WORKER, proof, admitted['executionToken'], 'complete')['chargedChecks'] == 1
    local, current = state(world)
    assert local['reservedChecks'] == current['reservedChecks'] == 0
    assert local['usedChecks'] == current['usedChecks'] == (0 if repair else 1)
    assert current['expiresAt'] == shortened + usage.RETENTION_SECONDS
