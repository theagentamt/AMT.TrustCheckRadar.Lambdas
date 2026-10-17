"""Only run in the extracted exact artifact; synthetic Moto, no live access."""
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
from copy import deepcopy

import pytest

if os.environ.get('AMT_EXACT_RETENTION_OVERLAY') != '1':
    pytest.skip('Exact retained-overlay qualification only', allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'tests/v1_play_handoff'))
from test_expired_resubscription import migration, handoff, world, ACCOUNT, PRODUCT
from test_transactions import PAYLOAD, WORKER
from shared_check_authority import entitlements as current
from shared_check_authority.core import AuthorityError
from shared_play_verification.proof import PlayVerificationError

YEAR = 365 * 24 * 60 * 60
ROLE = 'arn:aws:iam::123456789012:role/EntitlementOperator'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def legacy():
    path = ROOT/'tests/shared_check_authority/fixtures/dev-live9-retained-writer.py'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == 'd56b519eb333a57b819a285ea34477ef255143b2249c25d0492c836f85757177'
    return load('shared_check_authority.qualified_rollback_writer', path)


def rollback_writer(legacy, authority):
    return legacy.EntitlementWriter(authority, approved_products=frozenset({('google_play', PRODUCT)}),
        operator_principals=frozenset({ROLE}), verification_max_age_seconds=20)


def enable_retention(h):
    assert current.COMPLIMENTARY_AUDIT_RETENTION_SECONDS == YEAR
    h.writer.complimentary_audit_retention_seconds = YEAR
    h.writer.operators = frozenset({ROLE})
    return current.TrustedOperator(ROLE, 'synthetic-operator-session')


def test_default_complimentary_gate_still_denies(migration):
    h, _, _, _ = migration
    with pytest.raises(AuthorityError) as raised:
        h.writer.set_complimentary(current.TrustedOperator(ROLE, 'synthetic-session'), ACCOUNT,
            'forbidden', enabled=True, reason_code='OWNER_GRANT')
    assert raised.value.code == 'COMPLIMENTARY_RETENTION_APPROVAL_REQUIRED'


@pytest.mark.parametrize('finite', [False, True])
def test_migration_preserves_complimentary_pointer_receipt_expiry_and_revoke(migration, finite):
    h, w, p, _ = migration
    a, event, _, row, _, clock = w
    operator = enable_retention(h)
    expiry = clock[0] + 30 if finite else None
    h.writer.set_complimentary(operator, ACCOUNT, 'comp-grant', enabled=True, reason_code='OWNER_GRANT', expires_at=expiry)
    pointer = row('ACCESS')['sources']['complimentary']['auditOperationSK']
    receipt = deepcopy(row(pointer))
    assert receipt.get('expiresAt') == (expiry + YEAR if finite else None)
    assert h.handle(event, p)['status'] == 'committed'
    assert row(pointer) == receipt and row('ACCESS')['sources']['complimentary']['auditOperationSK'] == pointer
    assert row('ACCESS')['basis'] == 'complimentary'
    h.writer.set_complimentary(operator, ACCOUNT, 'comp-revoke', enabled=False, reason_code='REVOKE')
    assert row(pointer)['expiresAt'] == clock[0] + YEAR
    revoked = row('ACCESS')['sources']['complimentary']['auditOperationSK']
    assert row(revoked)['expiresAt'] == clock[0] + YEAR
    assert row('ACCESS')['basis'] == 'paid'
    period = row('PERIOD#'+row('ACCESS')['periodId'])
    assert period['limit'] == 200 and period['usedChecks'] == period['reservedChecks'] == 0


def test_old_writer_reads_migrated_state_and_old_handoff_replays_without_regrant(migration, legacy, monkeypatch):
    h, w, p, _ = migration
    a, event, _, row, _, _ = w
    assert h.handle(event, p)['status'] == 'committed'
    check = a.prepare(event, PAYLOAD, 'spent-after-migration')
    admitted = a.admit(event, PAYLOAD, check)
    a.settle(WORKER, check, admitted['executionToken'], 'complete')
    period = deepcopy(row('PERIOD#'+row('ACCESS')['periodId']))
    writer = rollback_writer(legacy, a)
    assert writer._read(ACCOUNT)[1] == row('ACCESS')
    path = ROOT/'tests/v1_play_handoff/fixtures/dev-live9-handoff.py'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == '8cc95103246de5c8451efaf5e7370ce59ebe8595ca65874f38c7e8da051f7eaf'
    with monkeypatch.context() as patch:
        patch.setattr(current, 'VerifiedMonthlyDecision', legacy.VerifiedMonthlyDecision)
        old_service = load('v1_play_handoff.qualified_rollback_service', path)
    old = old_service.Handoff(writer, h.ownership, h.client, allow_test=True, require_test=True)
    assert old.handle(event, p)['idempotencyReplay'] is True
    retry = {**p, 'requestId': '2bc1284d-e76c-41ca-b277-2d04fe26b323'}
    assert old.handle(event, retry)['status'] == 'committed'
    assert row(period['SK']) == period and period['usedChecks'] == 1


def test_rollback_retains_comp_source_validation_and_audit_expiry(migration, legacy):
    h, w, p, _ = migration
    a, event, _, row, _, clock = w
    operator = enable_retention(h)
    h.writer.set_complimentary(operator, ACCOUNT, 'retained-comp', enabled=True,
        reason_code='OWNER_GRANT', expires_at=clock[0]+5)
    assert h.handle(event, p)['status'] == 'committed'
    writer = rollback_writer(legacy, a)
    assert writer._read(ACCOUNT)[1] == row('ACCESS')
    clock[0] += 6
    event['requestContext']['authorizer']['jwt']['claims']['exp'] = str(clock[0]+90)
    writer.refresh_for_account(event, 'old-writer-refresh')
    assert row('ACCESS')['basis'] == 'paid'
    assert writer._read(ACCOUNT)[1] == row('ACCESS')
    corrupt = row('ACCESS'); corrupt['sources']['complimentary']['auditOperationSK'] = 'invalid'
    a.ddb.Table('authority').put_item(Item=corrupt)
    for candidate in (writer, h.writer):
        with pytest.raises(AuthorityError) as raised:
            candidate._read(ACCOUNT)
        assert raised.value.code == 'AUTHORITY_STATE_INVALID'


def test_retained_commit_rejects_expired_audit_before_any_write(migration):
    h, w, _, _ = migration
    _, _, _, row, _, clock = w
    pk, old, sources = h.writer._read(ACCOUNT)
    with pytest.raises(AuthorityError) as raised:
        h.writer._commit(ACCOUNT, pk, old, sources, 'bad-audit-expiry', 'SYNC_PAID', {}, audit_expires_at=clock[0])
    assert raised.value.code == 'WRITER_INPUT_REJECTED'
    assert row('ACCESS') == old


def test_rollback_generic_writer_still_refuses_another_root(migration, legacy):
    h, w, p, _ = migration
    a, event, _, row, _, clock = w
    h.handle(event, p)
    before = row('ACCESS')
    writer = rollback_writer(legacy, a)
    decision = legacy.VerifiedMonthlyDecision(ACCOUNT, 'google_play', PRODUCT, 'another-root', 99,
        clock[0], True, 'another-order', clock[0], clock[0]+100)
    with pytest.raises(AuthorityError) as raised:
        writer.commit_verified_paid(event, decision, 'rollback-new-root')
    assert raised.value.code == 'STORE_AUTHORITY_MIGRATION_REQUIRED'
    assert row('ACCESS') == before


def test_exact_access18_reader_handles_new_rows_spending_and_expiry(migration, legacy):
    h, w, p, _ = migration
    a, event, _, row, _, clock = w
    h.handle(event, p)
    path = ROOT/'tests/v1_play_handoff/fixtures/dev-access18-snapshot.py'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == '36ba23e497edb9aa4baee477844f29cf7b6db69e8a90ddd135a280fb06b21e83'
    reader = load('v1_entitlements.qualified_access18_snapshot', path)
    writer = rollback_writer(legacy, a)
    snapshot = reader.access_snapshot(writer, event, trial_activation_permitted=False)
    assert snapshot['access']['basis'] == 'paid' and snapshot['activeDevice'] is True
    assert snapshot['allowance']['limit'] == snapshot['allowance']['remaining'] == 200
    check = a.prepare(event, PAYLOAD, 'access18-spend')
    admit = a.admit(event, PAYLOAD, check)
    a.settle(WORKER, check, admit['executionToken'], 'complete')
    snapshot = reader.access_snapshot(writer, event, trial_activation_permitted=False)
    assert snapshot['allowance']['completedUsed'] == 1 and snapshot['allowance']['remaining'] == 199
    clock[0] = row('ACCESS')['validUntilEpoch']
    event['requestContext']['authorizer']['jwt']['claims']['exp'] = str(clock[0]+90)
    snapshot = reader.access_snapshot(writer, event, trial_activation_permitted=False)
    assert snapshot['access']['basis'] == 'none' and snapshot['access']['externalChecksAllowed'] is False
    assert all(value is None for value in snapshot['allowance'].values())
    assert snapshot['trial']['activationAvailable'] is False
