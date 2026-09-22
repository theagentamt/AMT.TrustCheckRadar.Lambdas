"""Purchase-period accounting, erasure and expiry against actual SDK/Moto."""
import os
import sys
from copy import deepcopy
from pathlib import Path
import pytest

if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run isolated authority emulator suite', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).parent))
from test_transactions import world, admit, ACCOUNT, WORKER, PAYLOAD, failure
from test_deletion import setup, finish, receipt
from shared_check_authority.core import AuthorityError
from shared_check_authority.recovery import Recovery
from shared_check_authority.expiry import Expiry
from shared_check_authority import purchase_usage as usage


def global_row(w, sk='PERIOD#p1'):
    a, _, _, row, _, _ = w
    return a._get('authority', row(sk)['purchaseUsageKey'])


@pytest.mark.parametrize('outcome,charge', [('complete', 1), ('partial', 0), ('failed', 0), ('unavailable', 0)])
def test_global_and_local_reserve_settle_once_without_deadline_extension(world, outcome, charge):
    a, _, _, row, _, _ = world
    before = global_row(world)
    proof, admission = admit(world)
    assert global_row(world)['reservedChecks'] == row('PERIOD#p1')['reservedChecks'] == 1
    assert row('CHECK#' + proof)['purchaseUsageKey'] == row('PERIOD#p1')['purchaseUsageKey']
    result = a.settle(WORKER, proof, admission['executionToken'], outcome)
    assert a.settle(WORKER, proof, admission['executionToken'], 'complete') == result
    observed = global_row(world)
    assert observed['usedChecks'] == row('PERIOD#p1')['usedChecks'] == charge
    assert observed['reservedChecks'] == row('PERIOD#p1')['reservedChecks'] == 0
    assert observed['expiresAt'] == before['expiresAt']
    assert set(observed) == usage.FIELDS and ACCOUNT not in repr(observed)


def test_original_purchase_period_charged_after_renewal(world):
    a, _, put, row, change, clock = world
    proof, admission = admit(world)
    change('authority', 'ACCESS', revision=2, periodId='p2', periodRevision=2)
    put('authority', 'PERIOD#p2', recordType='V1_ALLOWANCE_PERIOD', policyVersion=usage.OWNER_POLICY,
        grantRevision=2, limit=200, usedChecks=0, reservedChecks=0, startEpoch=clock[0], endEpoch=clock[0]+2000)
    a.settle(WORKER, proof, admission['executionToken'], 'complete')
    assert global_row(world)['usedChecks'] == 1
    assert global_row(world, 'PERIOD#p2')['usedChecks'] == 0


def test_missing_global_or_legacy_pointer_never_becomes_fresh_allowance(world):
    a, event, _, row, _, _ = world
    pointer = row('PERIOD#p1')['purchaseUsageKey']
    original = global_row(world)
    a.ddb.Table('authority').delete_item(Key=pointer)
    failure('PURCHASE_USAGE_UNAVAILABLE', lambda: a.prepare(event, PAYLOAD, 'missing-global'))
    assert row('PERIOD#p1')['usedChecks'] == 0
    a.ddb.Table('authority').put_item(Item=original)
    local = row('PERIOD#p1'); local.pop('purchaseUsageKey')
    a.ddb.Table('authority').put_item(Item=local)
    failure('PURCHASE_USAGE_INVALID', lambda: a.prepare(event, PAYLOAD, 'legacy'))


@pytest.mark.parametrize('mutation', [{'usedChecks': 201}, {'reservedChecks': True}, {'schemaVersion': True},
                                     {'accountId': ACCOUNT}, {'expiresAt': 9999999999}])
def test_unknown_or_corrupt_global_preserved(world, mutation):
    a, event, *_ = world
    changed = global_row(world) | mutation
    a.ddb.Table('authority').put_item(Item=changed)
    failure('PURCHASE_USAGE_INVALID', lambda: a.prepare(event, PAYLOAD, 'corrupt'))
    assert a._get('authority', {k: changed[k] for k in ('PK', 'SK')}) == changed


def test_mirror_mismatch_rejected_without_normalization(world):
    a, event, *_ = world
    changed = global_row(world) | {'usedChecks': 1}
    a.ddb.Table('authority').put_item(Item=changed)
    failure('PURCHASE_USAGE_MISMATCH', lambda: a.prepare(event, PAYLOAD, 'mismatch'))
    assert global_row(world)['usedChecks'] == 1


def test_global_race_rolls_back_entire_admission(world, monkeypatch):
    a, event, _, row, _, _ = world
    proof = a.prepare(event, PAYLOAD, 'race')
    original = a.client.transact_write_items
    fired = False
    def racing(**kwargs):
        nonlocal fired
        if not fired and any(op.get('Put', {}).get('Item', {}).get('recordType') == 'V1_CHECK_RECEIPT' for op in kwargs['TransactItems']):
            fired = True
            a.ddb.Table('authority').put_item(Item=global_row(world) | {'usedChecks': 1})
        return original(**kwargs)
    monkeypatch.setattr(a.client, 'transact_write_items', racing)
    failure('TRANSACTION_UNCERTAIN', lambda: a.admit(event, PAYLOAD, proof))
    assert row('CHECK#' + proof) is None and row('PERIOD#p1')['reservedChecks'] == 0


def test_recovery_releases_both_reservations_without_refund(world):
    a, _, _, row, _, clock = world
    proof, _ = admit(world)
    clock[0] += 121
    recovery = Recovery(a.ddb, 'authority', now=a.now)
    assert recovery.expire(a._partition(ACCOUNT, 'k1'), proof)
    assert not recovery.expire(a._partition(ACCOUNT, 'k1'), proof)
    assert global_row(world)['reservedChecks'] == row('PERIOD#p1')['reservedChecks'] == 0
    assert global_row(world)['usedChecks'] == 0


def test_deletion_groups_pending_releases_preserves_used_and_restore_seed(world, monkeypatch):
    a, _, _, row, _, clock = world
    first, admission = admit(world, 'complete')
    a.settle(WORKER, first, admission['executionToken'], 'complete')
    admit(world, 'pending-one'); admit(world, 'pending-two')
    pointer = row('PERIOD#p1')['purchaseUsageKey']
    original_global = global_row(world)
    bridge, command, _ = setup(world)
    original = bridge._transact
    release_batches = []
    def watching(items):
        matching = [x for x in items if x.get('Update', {}).get('Key') == pointer]
        assert len(matching) <= 1
        if matching: release_batches.append(matching)
        return original(items)
    monkeypatch.setattr(bridge, '_transact', watching)
    assert finish(bridge, command, page_size=20)['complete']
    retained = a._get('authority', pointer)
    assert retained['usedChecks'] == 1 and retained['reservedChecks'] == 0
    assert retained['expiresAt'] == original_global['expiresAt'] and len(release_batches) == 1
    assert row('PERIOD#p1') is None and receipt(a) is not None
    actions, seed = usage.funding_actions('authority', pointer, retained['startEpoch'], retained['endEpoch'], retained, now=clock[0])
    assert seed == {'usedChecks': 1, 'reservedChecks': 0}
    # Model a newly restored local period seeded from the SAME global usage.
    restored = {'PK': 'V1#k1#' + 'a'*64, 'SK': 'PERIOD#restored', 'recordType': 'V1_ALLOWANCE_PERIOD',
                'policyVersion': usage.OWNER_POLICY, 'grantRevision': 1, 'limit': 200,
                'startEpoch': retained['startEpoch'], 'endEpoch': retained['endEpoch'], 'purchaseUsageKey': pointer, **seed}
    a.client.transact_write_items(TransactItems=actions + [{'Put': {'TableName': 'authority', 'Item': restored, 'ConditionExpression': 'attribute_not_exists(PK)'}}])
    assert usage.global_for_period(a.ddb, 'authority', restored, now=clock[0])['usedChecks'] == 1


def test_missing_live_global_blocks_deletion_receipt_instead_of_losing_reservation(world):
    a, _, _, row, _, _ = world
    proof, _ = admit(world)
    a.ddb.Table('authority').delete_item(Key=row('PERIOD#p1')['purchaseUsageKey'])
    bridge, command, _ = setup(world)
    with pytest.raises(AuthorityError, match='PURCHASE_USAGE_UNAVAILABLE'):
        finish(bridge, command)
    assert row('CHECK#' + proof) is not None and receipt(a) is None


def test_orphan_reserved_count_cannot_be_deleted_as_completed_cleanup(world):
    a, _, _, row, _, _ = world
    proof, _ = admit(world)
    a.ddb.Table('authority').delete_item(Key={'PK': a._partition(ACCOUNT, 'k1'), 'SK': 'CHECK#' + proof})
    bridge, command, _ = setup(world)
    with pytest.raises(AuthorityError, match='PURCHASE_USAGE_MISMATCH'):
        finish(bridge, command)
    assert row('PERIOD#p1')['reservedChecks'] == 1 and receipt(a) is None



def test_new_owner_usage_can_advance_before_old_zero_reserved_period_deleted(world, monkeypatch):
    a, _, _, row, _, _ = world
    proof, admission = admit(world)
    a.settle(WORKER, proof, admission['executionToken'], 'complete')
    pointer = row('PERIOD#p1')['purchaseUsageKey']
    observed = global_row(world)
    # A fresh verified restore can occur after exact ownership cleanup; old
    # account deletion may still be waiting to erase its zero-reservation row.
    a.ddb.Table('authority').put_item(Item=observed | {'usedChecks': 2, 'reservedChecks': 1})
    bridge, command, _ = setup(world)
    original = bridge._transact
    raced = False
    def advancing(items):
        nonlocal raced
        if any(op.get('Delete', {}).get('Key', {}).get('SK') == 'PERIOD#p1' for op in items):
            raced = True
            a.ddb.Table('authority').put_item(Item=observed | {'usedChecks': 3, 'reservedChecks': 0})
        return original(items)
    monkeypatch.setattr(bridge, '_transact', advancing)
    assert finish(bridge, command)['complete']
    assert raced and row('PERIOD#p1') is None
    assert a._get('authority', pointer)['usedChecks'] == 3
    assert receipt(a) is not None

def test_global_expiry_uses_original_deadline_and_late_cleanup_never_recreates(world):
    a, _, _, row, _, clock = world
    proof, _ = admit(world)
    global_before = global_row(world)
    pointer = row('PERIOD#p1')['purchaseUsageKey']
    expiry = Expiry(a.ddb, 'authority', now=a.now)
    clock[0] = int(global_before['expiresAt']) - 1
    assert not expiry.expire(pointer['PK'], pointer['SK'])
    clock[0] += 1
    assert expiry.expire(pointer['PK'], pointer['SK'])
    assert not expiry.expire(pointer['PK'], pointer['SK'])
    recovery = Recovery(a.ddb, 'authority', now=a.now)
    assert recovery.expire(a._partition(ACCOUNT, 'k1'), proof)
    assert row('PERIOD#p1')['reservedChecks'] == 0 and a._get('authority', pointer) is None
    bridge, command, _ = setup(world)
    assert finish(bridge, command)['complete']
    assert a._get('authority', pointer) is None


def test_funding_condition_rejects_changed_counts_and_never_resets_them(world):
    a, _, _, _, _, clock = world
    observed = global_row(world); pointer = {k: observed[k] for k in ('PK', 'SK')}
    actions, _ = usage.funding_actions('authority', pointer, observed['startEpoch'], observed['endEpoch'], observed, now=clock[0])
    a.ddb.Table('authority').put_item(Item=observed | {'usedChecks': 5})
    from botocore.exceptions import ClientError
    with pytest.raises(ClientError):a.client.transact_write_items(TransactItems=actions)
    assert a._get('authority', pointer)['usedChecks'] == 5
