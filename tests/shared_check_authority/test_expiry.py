"""Synthetic explicit expiry and fair scheduler checks; no real AWS or secrets."""
import os
import sys
from pathlib import Path
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run isolated authority emulator suite', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).parent))
from test_transactions import world, ACCOUNT, admit, WORKER
from shared_check_authority.core import AuthorityError, OWNER_POLICY
from shared_check_authority.expiry import Expiry
from shared_check_authority.inventory import INVENTORY_KEY
from url_lease_recovery.app import run_passes, lambda_handler


@pytest.fixture
def setup(world):
    a, e, put, row, change, clock = world
    pk = a._partition(ACCOUNT, 'k1')
    worker = Expiry(a.ddb, 'authority', now=a.now)
    def record(sk='PREPARE#old', *, expiry=None, **attrs):
        expiry = clock[0] - 1 if expiry is None else expiry
        typ = {'PREPARE': 'V1_PREPARATION', 'ATTEMPT': 'V1_ATTEMPT_COUNTER', 'CHECK': 'V1_CHECK_RECEIPT'}[sk.split('#')[0]]
        item = {'PK': pk, 'SK': sk, 'recordType': typ, 'expiresAt': expiry, 'GSI1PK': 'V1_EXPIRING',
                'GSI1SK': f'{expiry:012d}#{pk}#{sk}', **attrs}
        a.ddb.Table('authority').put_item(Item=item)
        return item
    return worker, world, pk, record


@pytest.mark.parametrize('sk', ['PREPARE#old', 'ATTEMPT#1799999900'])
def test_expired_rows_physically_deleted_once(setup, sk):
    worker, world, pk, record = setup
    record(sk)
    assert worker.expire(pk, sk) is True
    assert world[3](sk) is None
    assert worker.expire(pk, sk) is False
    assert world[3]('ACCESS') is not None and world[3]('PERIOD#p1')['usedChecks'] == 0


@pytest.mark.parametrize('sk', ['PREPARE#orphan', 'ATTEMPT#1799999901'])
def test_legitimate_orphans_expire_without_authority_resurrection(setup, sk):
    worker, world, pk, record = setup
    world[0].ddb.Table('authority').delete_item(Key={'PK': pk, 'SK': 'ACCESS'})
    record(sk)
    assert worker.expire(pk, sk) is True and world[3]('ACCESS') is None


def test_unexpired_rows_not_deleted(setup):
    worker, world, pk, record = setup
    record(expiry=world[5][0] + 1)
    assert worker.expire(pk, 'PREPARE#old') is False
    assert world[3]('PREPARE#old') is not None


def test_completed_receipt_expires_without_mutating_allowance(setup):
    worker, world, pk, record = setup
    proof, admitted = admit(world)
    a, _, _, row, _, clock = world
    a.settle(WORKER, proof, admitted['executionToken'], 'complete')
    clock[0] += a.s.receipt_retention_seconds
    assert worker.expire(pk, 'CHECK#' + proof) is True
    assert row('CHECK#' + proof) is None
    assert row('PERIOD#p1')['usedChecks'] == 1 and row('PERIOD#p1')['reservedChecks'] == 0


def test_pending_receipt_cannot_be_deleted_as_expired(setup):
    worker, world, pk, record = setup
    proof, _ = admit(world)
    record('CHECK#' + proof, state='ADMITTED', policyVersion=OWNER_POLICY)
    with pytest.raises(AuthorityError):
        worker.expire(pk, 'CHECK#' + proof)
    assert world[3]('CHECK#' + proof) is not None and world[3]('PERIOD#p1')['reservedChecks'] == 1


def test_access_deleting_blocks_cleanup(setup):
    worker, world, pk, record = setup
    record()
    world[4]('authority', 'ACCESS', state='DELETING')
    assert worker.expire(pk, 'PREPARE#old') is False
    assert world[3]('PREPARE#old') is not None


def test_renewed_expiry_race_is_conditional_noop(setup, monkeypatch):
    worker, world, pk, record = setup
    record()
    original = worker.client.transact_write_items
    def race(**kwargs):
        record(expiry=world[5][0] + 100)
        return original(**kwargs)
    monkeypatch.setattr(worker.client, 'transact_write_items', race)
    assert worker.expire(pk, 'PREPARE#old') is False
    assert world[3]('PREPARE#old')['expiresAt'] == world[5][0] + 100


def test_deletion_fence_race_is_conditional_noop(setup, monkeypatch):
    worker, world, pk, record = setup
    record()
    original = worker.client.transact_write_items
    def race(**kwargs):
        world[4]('authority', 'ACCESS', state='DELETING')
        return original(**kwargs)
    monkeypatch.setattr(worker.client, 'transact_write_items', race)
    assert worker.expire(pk, 'PREPARE#old') is False and world[3]('PREPARE#old') is not None


def test_inventory_rotation_race_denies_delete(setup, monkeypatch):
    worker, world, pk, record = setup
    record()
    original = worker.client.transact_write_items
    def race(**kwargs):
        item = world[0]._get('authority', INVENTORY_KEY)
        world[0].ddb.Table('authority').put_item(Item=item | {'revision': 2})
        return original(**kwargs)
    monkeypatch.setattr(worker.client, 'transact_write_items', race)
    with pytest.raises(AuthorityError, match='EXPIRY_TRANSACTION_UNCERTAIN'):
        worker.expire(pk, 'PREPARE#old')
    assert world[3]('PREPARE#old') is not None


def test_unknown_issued_key_fails_closed(setup):
    worker, world, pk, record = setup
    item = record()
    unissued = pk.replace('#k1#', '#k9#')
    world[0].ddb.Table('authority').put_item(Item=item | {'PK': unissued, 'GSI1SK': item['GSI1SK'].replace(pk, unissued)})
    with pytest.raises(AuthorityError, match='EXPIRY_INVENTORY_UNAVAILABLE'):
        worker.expire(unissued, 'PREPARE#old')


def test_poison_page_advances_cursor_then_deletes_later_rows(setup):
    worker, world, pk, record = setup
    record('PREPARE#a-poison', recordType='UNKNOWN')
    record('PREPARE#z-good')
    first = worker.sweep(page_size=1, max_pages=1)
    assert first['failed'] == 1 and first['deleted'] == 0 and first['cursor']
    second = worker.sweep(cursor=first['cursor'], page_size=1, max_pages=1)
    assert second['deleted'] == 1 and world[3]('PREPARE#z-good') is None
    assert world[3]('PREPARE#a-poison') is not None


def test_deadline_midpage_resumes_last_processed_row(setup):
    worker, world, pk, record = setup
    for name in ('a', 'b', 'c'):
        record('PREPARE#' + name)
    checks = iter([True, True, False])
    first = worker.sweep(page_size=5, max_pages=1, can_continue=lambda: next(checks))
    assert first['deleted'] == 1 and first['cursor']['SK'] == 'PREPARE#a'
    assert world[3]('PREPARE#b') is not None
    second = worker.sweep(cursor=first['cursor'], page_size=5, max_pages=1)
    assert second['deleted'] == 2


class Context:
    def get_remaining_time_in_millis(self):
        return 25000


def test_both_passes_have_independent_durable_cursors(setup):
    worker, world, pk, record = setup
    a, e, _, row, _, clock = world
    proof, _ = admit(world)
    record()
    clock[0] += a.s.worker_settlement_seconds
    counts = run_passes(a.ddb, 'authority', Context(), now=a.now)
    assert counts['recovered'] == 1 and counts['expiredDeleted'] >= 1
    assert counts['leaseExamined'] >= 1 and counts['expiryExamined'] >= 1
    for sk in ('LEASE_SWEEP_CURSOR', 'EXPIRY_SWEEP_CURSOR'):
        cursor = a._get('authority', {'PK': 'V1#CHECKPOINT', 'SK': sk})
        assert cursor['revision'] == 1
    assert row('CHECK#' + proof)['state'] == 'SETTLED'
    assert 'cursor' not in counts and 'checkId' not in counts


def test_oldest_poison_stays_visible_despite_fair_cursor(setup):
    worker, world, pk, record = setup
    a = world[0]
    oldest = record('PREPARE#poison', expiry=world[5][0] - 1000, recordType='UNKNOWN')
    record('PREPARE#later', expiry=world[5][0] - 2)
    a.ddb.Table('authority').put_item(Item={'PK': 'V1#CHECKPOINT', 'SK': 'EXPIRY_SWEEP_CURSOR', 'revision': 1,
                                         'cursor': {k: oldest[k] for k in ('PK', 'SK', 'GSI1PK', 'GSI1SK')}})
    result = run_passes(a.ddb, 'authority', Context(), now=a.now)
    assert result['expiredDeleted'] == 1 and result['oldestOverdueSeconds'] == 1000
    assert world[3]('PREPARE#poison') is not None


def test_one_pass_failure_does_not_starve_other(setup, monkeypatch):
    from shared_check_authority.recovery import Recovery
    worker, world, pk, record = setup
    record()
    def fail(**kwargs):
        raise RuntimeError('synthetic failure')
    monkeypatch.setattr(Recovery, 'sweep', fail)
    result = run_passes(world[0].ddb, 'authority', Context(), now=world[0].now)
    assert result['failed'] == 1 and result['expiredDeleted'] == 1


def test_context_required_before_work(setup):
    with pytest.raises(ValueError):
        run_passes(setup[1][0].ddb, 'authority', None, now=setup[1][0].now)


def test_handler_disabled_never_constructs_resource(monkeypatch):
    monkeypatch.setenv('STAGE', 'dev')
    monkeypatch.delenv('LEASE_SWEEP_ENABLED', raising=False)
    assert lambda_handler({'schemaVersion': 1}, None) == {'enabled': False, 'recovered': 0}


def test_checkpoint_never_reuses_legacy_cursors_or_writes_inventory_partition(setup, monkeypatch):
    worker, world, pk, record = setup
    a = world[0]
    record()
    original_inventory = a._get('authority', {'PK': 'V1#CONTROL', 'SK': 'HMAC_KEY_INVENTORY'})
    legacy = []
    for sk in ('LEASE_SWEEP_CURSOR', 'EXPIRY_SWEEP_CURSOR'):
        value = {'PK': 'V1#CONTROL', 'SK': sk, 'revision': 99, 'cursor': {'invalid': 'legacy cursor'}}
        legacy.append(value)
        a.ddb.Table('authority').put_item(Item=value)
    original = a.ddb.meta.client.transact_write_items
    def guarded(**kwargs):
        for action in kwargs['TransactItems']:
            for kind in ('Put', 'Update', 'Delete'):
                if kind in action:
                    data = action[kind]
                    assert data.get('Key', data.get('Item'))['PK'] != 'V1#CONTROL'
        return original(**kwargs)
    monkeypatch.setattr(a.ddb.meta.client, 'transact_write_items', guarded)
    result = run_passes(a.ddb, 'authority', Context(), now=a.now)
    assert result['failed'] == 0 and result['expiredDeleted'] == 1
    for old in legacy:
        assert a._get('authority', {k: old[k] for k in ('PK','SK')}) == old
        new = a._get('authority', {'PK': 'V1#CHECKPOINT', 'SK': old['SK']})
        assert new['revision'] == 1 and new['cursor'] is None
    assert a._get('authority', {'PK': 'V1#CONTROL', 'SK': 'HMAC_KEY_INVENTORY'}) == original_inventory
