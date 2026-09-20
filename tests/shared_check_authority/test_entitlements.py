"""Writer transaction tests use synthetic DynamoDB only, no store or AWS calls."""
import os
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run isolated authority emulator suite', allow_module_level=True)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataclasses import replace
from test_transactions import world, ACCOUNT, failure
from shared_check_authority.core import AuthorityError, TRIAL_SECONDS
from shared_check_authority.entitlements import EntitlementWriter, TrustedOperator, VerifiedMonthlyDecision

ROLE = 'arn:aws:iam::123456789012:role/EntitlementOperator'
OPERATOR = TrustedOperator(ROLE, 'synthetic-session')

@pytest.fixture
def writer_world(world):
    a, event, put, row, change, clock = world
    pk = a._partition(ACCOUNT, 'k1')
    for sk in ('ACCESS', 'PERIOD#p1'):
        a.ddb.Table('authority').delete_item(Key={'PK': pk, 'SK': sk})
    decision = [VerifiedMonthlyDecision(ACCOUNT, 'google_play', 'individual-monthly', 'synthetic-subscription', 1,
                                       clock[0], True, 'synthetic-period-1', clock[0] - 100, clock[0] + 1000)]
    calls = []
    def verify(account, reference):
        calls.append((account, reference))
        return decision[0]
    writer = EntitlementWriter(a, approved_products=frozenset({('google_play', 'individual-monthly')}),
                               operator_principals=frozenset({ROLE}), store_verifier=verify,
                               verification_max_age_seconds=60, trial_retention_approved=True)
    return writer, world, decision, calls


def test_explicit_trial_first_and_retry_preserve_clock(writer_world):
    w, world, _, _ = writer_world
    a, e, _, row, _, clock = world
    first = w.activate_trial(e)
    period_id = row('ACCESS')['periodId']
    assert first == {'activatedAtEpoch': clock[0], 'validUntilEpoch': clock[0] + TRIAL_SECONDS, 'alreadyActivated': False}
    assert row('ACCESS')['periodRevision'] == row('PERIOD#' + period_id)['grantRevision']
    assert row('PERIOD#' + period_id)['limit'] == 10
    clock[0] += 20
    assert w.activate_trial(e) == first | {'alreadyActivated': True}
    assert row('ACCESS')['revision'] == 1
    assert row('TRIAL_HISTORY').get('expiresAt') is None


def test_trial_cannot_reset_on_new_device(writer_world):
    w, world, _, _ = writer_world
    _, e, put, row, change, _ = world
    first = w.activate_trial(e)
    period_id = row('ACCESS')['periodId']
    change('authority', 'PERIOD#' + period_id, usedChecks=10)
    change('devices', 'ACTIVE_BINDING', bindingFingerprint='device-two', stateVersion=2)
    put('devices', 'DEVICE#device-two', accountId=ACCOUNT, status='ACTIVE', bindingFingerprint='device-two')
    e['headers']['x-device-binding-fingerprint'] = 'device-two'
    assert w.activate_trial(e) == first | {'alreadyActivated': True}
    assert row('PERIOD#' + period_id)['usedChecks'] == 10


def test_trial_expired_retry_does_not_reactivate(writer_world):
    w, world, _, _ = writer_world
    _, e, _, row, _, clock = world
    first = w.activate_trial(e)
    clock[0] += TRIAL_SECONDS + 1
    e['requestContext']['authorizer']['jwt']['claims']['exp'] = str(clock[0] + 60)
    assert w.activate_trial(e)['validUntilEpoch'] == first['validUntilEpoch']
    assert row('ACCESS')['revision'] == 1


@pytest.mark.parametrize('mutation', [lambda e: e.clear(), lambda e: e['headers'].clear()])
def test_trial_requires_account_and_device(writer_world, mutation):
    w, world, _, _ = writer_world
    _, e, _, row, _, _ = world
    mutation(e)
    with pytest.raises(AuthorityError):
        w.activate_trial(e)
    assert row('ACCESS') is None and row('TRIAL_HISTORY') is None


def test_trial_account_deletion_race_atomic(writer_world, monkeypatch):
    w, world, _, _ = writer_world
    a, e, put, row, _, _ = world
    original = a.client.transact_write_items
    def race(**kwargs):
        put('deletion', 'ACCOUNT_DELETION', state='DELETING')
        return original(**kwargs)
    monkeypatch.setattr(a.client, 'transact_write_items', race)
    failure('TRANSACTION_UNCERTAIN', lambda: w.activate_trial(e))
    assert row('ACCESS') is None and row('TRIAL_HISTORY') is None


def test_trial_device_switch_race_atomic(writer_world, monkeypatch):
    w, world, _, _ = writer_world
    a, e, _, row, change, _ = world
    original = a.client.transact_write_items
    def race(**kwargs):
        change('devices', 'ACTIVE_BINDING', stateVersion=2)
        return original(**kwargs)
    monkeypatch.setattr(a.client, 'transact_write_items', race)
    failure('TRANSACTION_UNCERTAIN', lambda: w.activate_trial(e))
    assert row('ACCESS') is None and row('TRIAL_HISTORY') is None


def test_paid_sync_creates_200_period_and_revalidation_preserves_counters(writer_world):
    w, world, decisions, calls = writer_world
    _, e, _, row, change, _ = world
    w.synchronize_paid(e, 'synthetic-purchase-token', 'sync-1')
    period_id = row('ACCESS')['periodId']
    change('authority', 'PERIOD#' + period_id, usedChecks=17, reservedChecks=2)
    decisions[0] = replace(decisions[0], source_revision=2)
    w.synchronize_paid(e, 'synthetic-purchase-token', 'sync-2')
    assert row('ACCESS')['revision'] == 2
    assert row('ACCESS')['periodRevision'] == 1
    assert row('PERIOD#' + period_id)['usedChecks'] == 17
    assert row('PERIOD#' + period_id)['reservedChecks'] == 2
    assert row('PERIOD#' + period_id)['limit'] == 200
    assert calls == [(ACCOUNT, 'synthetic-purchase-token')] * 2


@pytest.mark.parametrize('changes,code', [
    ({'account': 'other-account'}, 'VERIFIED_STORE_AUTHORITY_UNAVAILABLE'),
    ({'product_id': 'annual'}, 'VERIFIED_STORE_AUTHORITY_UNAVAILABLE'),
    ({'billing_period': 'P1Y'}, 'VERIFIED_STORE_AUTHORITY_UNAVAILABLE'),
    ({'source_revision': True}, 'VERIFIED_STORE_AUTHORITY_UNAVAILABLE'),
    ({'verified_at_epoch': 1}, 'VERIFIED_STORE_AUTHORITY_UNAVAILABLE'),
    ({'verified_at_epoch': 9999999999}, 'VERIFIED_STORE_AUTHORITY_UNAVAILABLE'),
    ({'period_start_epoch': None}, 'VERIFIED_MONTHLY_PERIOD_UNAVAILABLE'),
    ({'period_end_epoch': 1}, 'VERIFIED_MONTHLY_PERIOD_UNAVAILABLE'),
    ({'period_id': None}, 'VERIFIED_MONTHLY_PERIOD_UNAVAILABLE'),
])
def test_store_facts_fail_closed(writer_world, changes, code):
    w, world, decisions, _ = writer_world
    _, e, _, row, _, _ = world
    decisions[0] = replace(decisions[0], **changes)
    failure(code, lambda: w.synchronize_paid(e, 'reference', 'sync'))
    assert row('ACCESS') is None


def test_client_dictionary_never_store_authority(writer_world):
    w, world, decisions, _ = writer_world
    _, e, _, row, _, _ = world
    decisions[0] = {'active': True, 'monthlyChecksRemaining': 999, 'isAccessGranted': True}
    failure('VERIFIED_STORE_AUTHORITY_UNAVAILABLE', lambda: w.synchronize_paid(e, 'reference', 'sync'))
    assert row('ACCESS') is None


def test_missing_real_verifier_fails_closed(writer_world):
    w, world, _, _ = writer_world
    w.verify_store = None
    failure('VERIFIED_STORE_AUTHORITY_UNAVAILABLE', lambda: w.synchronize_paid(world[1], {}, 'sync'))


def test_renewal_preserves_old_period_reservation(writer_world):
    w, world, decisions, _ = writer_world
    _, e, _, row, change, clock = world
    w.synchronize_paid(e, 'reference', 'sync-1')
    first = row('ACCESS')['periodId']
    change('authority', 'PERIOD#' + first, usedChecks=190, reservedChecks=1)
    clock[0] += 1000
    e['requestContext']['authorizer']['jwt']['claims']['exp'] = str(clock[0] + 60)
    decisions[0] = replace(decisions[0], source_revision=2, verified_at_epoch=clock[0], period_id='synthetic-period-2',
                           period_start_epoch=clock[0], period_end_epoch=clock[0] + 1000)
    w.synchronize_paid(e, 'reference', 'sync-2')
    second = row('ACCESS')['periodId']
    assert second != first and row('PERIOD#' + second)['usedChecks'] == 0
    assert row('PERIOD#' + first)['reservedChecks'] == 1 and row('PERIOD#' + first)['usedChecks'] == 190


def test_different_id_cannot_duplicate_same_period(writer_world):
    w, world, decisions, _ = writer_world
    _, e, _, row, _, _ = world
    w.synchronize_paid(e, 'reference', 'sync-1')
    decisions[0] = replace(decisions[0], source_revision=2, period_id='another-period-id')
    failure('OVERLAPPING_STORE_PERIOD', lambda: w.synchronize_paid(e, 'reference', 'sync-2'))
    assert row('ACCESS')['revision'] == 1


def test_same_period_identity_cannot_change_dates(writer_world):
    w, world, decisions, _ = writer_world
    _, e, _, row, _, _ = world
    w.synchronize_paid(e, 'reference', 'sync-1')
    decisions[0] = replace(decisions[0], source_revision=2, period_end_epoch=decisions[0].period_end_epoch + 30)
    failure('IMMUTABLE_PERIOD_CONFLICT', lambda: w.synchronize_paid(e, 'reference', 'sync-2'))
    assert row('ACCESS')['revision'] == 1


def test_store_revoke_and_restore_same_period_preserves_usage(writer_world):
    w, world, decisions, _ = writer_world
    _, e, _, row, change, _ = world
    original = decisions[0]
    w.synchronize_paid(e, 'reference', 'sync-1')
    period_id = row('ACCESS')['periodId']
    change('authority', 'PERIOD#' + period_id, usedChecks=50, reservedChecks=1)
    decisions[0] = replace(original, source_revision=2, active=False)
    w.synchronize_paid(e, 'reference', 'sync-2')
    assert row('ACCESS')['state'] == 'INACTIVE'
    decisions[0] = replace(original, source_revision=3)
    w.synchronize_paid(e, 'reference', 'sync-3')
    assert row('ACCESS')['basis'] == 'paid' and row('ACCESS')['periodRevision'] == 1
    assert row('PERIOD#' + period_id)['usedChecks'] == 50
    assert row('PERIOD#' + period_id)['reservedChecks'] == 1


def test_stale_store_event_cannot_restore_revoked_access(writer_world):
    w, world, decisions, _ = writer_world
    _, e, _, row, _, _ = world
    original = decisions[0]
    w.synchronize_paid(e, 'reference', 'sync-1')
    decisions[0] = replace(original, source_revision=2, active=False)
    w.synchronize_paid(e, 'reference', 'sync-2')
    decisions[0] = original
    failure('STORE_AUTHORITY_MIGRATION_REQUIRED', lambda: w.synchronize_paid(e, 'reference', 'sync-3'))
    assert row('ACCESS')['state'] == 'INACTIVE'


def test_complimentary_unlimited_no_fake_expiry_and_restore_paid(writer_world):
    w, world, _, _ = writer_world
    _, e, _, row, change, _ = world
    w.synchronize_paid(e, 'reference', 'paid')
    period_id = row('ACCESS')['periodId']
    change('authority', 'PERIOD#' + period_id, usedChecks=199, reservedChecks=1)
    grant = w.set_complimentary(OPERATOR, ACCOUNT, 'grant', enabled=True, reason_code='OWNER_GRANT')
    assert grant['basis'] == 'complimentary' and row('ACCESS')['validUntilEpoch'] is None
    assert w.set_complimentary(OPERATOR, ACCOUNT, 'grant', enabled=True, reason_code='OWNER_GRANT') == grant
    assert row('ACCESS')['revision'] == 2
    w.set_complimentary(OPERATOR, ACCOUNT, 'revoke', enabled=False, reason_code='REVOKE')
    assert row('ACCESS')['basis'] == 'paid' and row('ACCESS')['periodRevision'] == 1
    assert row('PERIOD#' + period_id)['usedChecks'] == 199
    assert row('PERIOD#' + period_id)['reservedChecks'] == 1


def test_complimentary_revoke_restores_original_trial_clock(writer_world):
    w, world, _, _ = writer_world
    _, e, _, row, _, clock = world
    trial = w.activate_trial(e)
    w.set_complimentary(OPERATOR, ACCOUNT, 'grant', enabled=True, reason_code='OWNER_GRANT')
    clock[0] += 10
    w.set_complimentary(OPERATOR, ACCOUNT, 'revoke', enabled=False, reason_code='REVOKE')
    assert row('ACCESS')['basis'] == 'trial'
    assert row('ACCESS')['activatedAtEpoch'] == trial['activatedAtEpoch']
    assert row('ACCESS')['validUntilEpoch'] == trial['validUntilEpoch']


def test_complimentary_expiry_refresh_uses_real_underlying_state(writer_world):
    w, world, _, _ = writer_world
    _, e, _, row, _, clock = world
    w.synchronize_paid(e, 'reference', 'paid')
    w.set_complimentary(OPERATOR, ACCOUNT, 'grant', enabled=True, reason_code='OWNER_GRANT', expires_at=clock[0] + 5)
    clock[0] += 6
    assert w.refresh_for_account(e, 'refresh')['basis'] == 'paid'
    assert row('ACCESS')['periodRevision'] == 1


@pytest.mark.parametrize('operator', [None, {'principal_arn': ROLE}, TrustedOperator('arn:aws:iam::123456789012:role/Other', 's')])
def test_operator_identity_must_be_trusted_and_allowlisted(writer_world, operator):
    w, world, _, _ = writer_world
    failure('OPERATOR_AUTHORIZATION_REQUIRED', lambda: w.set_complimentary(operator, ACCOUNT, 'grant', enabled=True, reason_code='OWNER_GRANT'))
    assert world[3]('ACCESS') is None


def test_operator_audit_is_transactional_and_avoids_tokens(writer_world):
    w, world, _, _ = writer_world
    a, _, _, _, _, _ = world
    w.set_complimentary(OPERATOR, ACCOUNT, 'grant', enabled=True, reason_code='OWNER_GRANT')
    rows = a.ddb.Table('authority').scan()['Items']
    audits = [r for r in rows if r['recordType'] == 'V1_AUTHORITY_AUDIT']
    assert len(audits) == 1 and audits[0]['operatorPrincipalArn'] == ROLE and audits[0]['reasonCode'] == 'OWNER_GRANT'
    assert ACCOUNT not in str(rows) and 'synthetic-session' not in str(rows)


def test_operation_id_reuse_with_changed_request_rejected(writer_world):
    w, world, _, _ = writer_world
    w.set_complimentary(OPERATOR, ACCOUNT, 'grant', enabled=True, reason_code='OWNER_GRANT')
    failure('AUTHORITY_OPERATION_CONFLICT', lambda: w.set_complimentary(OPERATOR, ACCOUNT, 'grant', enabled=False, reason_code='REVOKE'))
    assert world[3]('ACCESS')['basis'] == 'complimentary'


def test_lost_successful_write_reconciles_same_operation(writer_world, monkeypatch):
    w, world, _, _ = writer_world
    a, _, _, row, _, _ = world
    original = a.client.transact_write_items
    def lost(**kwargs):
        original(**kwargs)
        raise TimeoutError('synthetic lost response')
    monkeypatch.setattr(a.client, 'transact_write_items', lost)
    assert w.set_complimentary(OPERATOR, ACCOUNT, 'grant', enabled=True, reason_code='OWNER_GRANT')['revision'] == 1
    assert row('ACCESS')['revision'] == 1


def test_concurrent_mutation_loses_cas_without_overwriting(writer_world, monkeypatch):
    w, world, _, _ = writer_world
    a, _, _, row, change, _ = world
    w.set_complimentary(OPERATOR, ACCOUNT, 'grant', enabled=True, reason_code='OWNER_GRANT')
    original = a.client.transact_write_items
    def race(**kwargs):
        change('authority', 'ACCESS', revision=2)
        return original(**kwargs)
    monkeypatch.setattr(a.client, 'transact_write_items', race)
    failure('TRANSACTION_UNCERTAIN', lambda: w.set_complimentary(OPERATOR, ACCOUNT, 'revoke', enabled=False, reason_code='REVOKE'))
    assert row('ACCESS')['basis'] == 'complimentary'


def test_other_hmac_namespace_requires_migration(writer_world):
    w, world, _, _ = writer_world
    a, e, _, row, _, _ = world
    w.activate_trial(e)
    a.s = replace(a.s, active_key_id='k2', hmac_keys=a.s.hmac_keys | {'k2': b'another-synthetic-test-key-0000000'})
    failure('AUTHORITY_MIGRATION_REQUIRED', lambda: w.activate_trial(e))
    assert row('ACCESS')['revision'] == 1


def test_paid_trial_eligibility_and_no_legacy_authority(writer_world):
    w, world, _, _ = writer_world
    _, e, _, row, _, _ = world
    w.synchronize_paid(e, 'reference', 'paid')
    failure('TRIAL_NOT_ELIGIBLE', lambda: w.activate_trial(e))
    assert row('TRIAL_HISTORY') is None


def test_complimentary_account_deletion_race_atomic(writer_world, monkeypatch):
    w, world, _, _ = writer_world
    a, _, put, row, _, _ = world
    original = a.client.transact_write_items
    def race(**kwargs):
        put('deletion', 'ACCOUNT_DELETION', state='DELETING')
        return original(**kwargs)
    monkeypatch.setattr(a.client, 'transact_write_items', race)
    failure('TRANSACTION_UNCERTAIN', lambda: w.set_complimentary(OPERATOR, ACCOUNT, 'grant', enabled=True, reason_code='OWNER_GRANT'))
    assert row('ACCESS') is None


def test_same_store_revision_conflict_rejected(writer_world):
    w, world, decisions, _ = writer_world
    _, e, _, row, _, _ = world
    w.synchronize_paid(e, 'reference', 'paid')
    decisions[0] = replace(decisions[0], active=False)
    failure('STORE_REVISION_CONFLICT', lambda: w.synchronize_paid(e, 'reference', 'revoke'))
    assert row('ACCESS')['state'] == 'ACTIVE'


def test_no_tokens_or_raw_account_persisted(writer_world):
    w, world, _, _ = writer_world
    a, e, _, _, _, _ = world
    w.synchronize_paid(e, 'synthetic-secret-token', 'paid')
    rows = str(a.ddb.Table('authority').scan()['Items'])
    assert 'synthetic-secret-token' not in rows and 'synthetic-subscription' not in rows
    assert 'synthetic-period-1' not in rows and ACCOUNT not in rows


def test_existing_trial_receipt_settles_after_complimentary_overlay_and_revoke(writer_world):
    from test_transactions import PAYLOAD, WORKER
    w, world, _, _ = writer_world
    a, e, _, row, _, _ = world
    w.activate_trial(e)
    period_id = row('ACCESS')['periodId']
    proof = a.prepare(e, PAYLOAD, 'trial-check')
    admitted = a.admit(e, PAYLOAD, proof)
    w.set_complimentary(OPERATOR, ACCOUNT, 'grant', enabled=True, reason_code='OWNER_GRANT')
    w.set_complimentary(OPERATOR, ACCOUNT, 'revoke', enabled=False, reason_code='REVOKE')
    assert row('ACCESS')['revision'] == 3 and row('ACCESS')['periodRevision'] == 1
    assert a.settle(WORKER, proof, admitted['executionToken'], 'complete')['chargedChecks'] == 1
    assert row('PERIOD#' + period_id)['usedChecks'] == 1
    assert row('PERIOD#' + period_id)['reservedChecks'] == 0
    next_proof = a.prepare(e, PAYLOAD, 'next-trial-check')
    assert a.admit(e, PAYLOAD, next_proof)['admitted'] is True


def test_unlimited_complimentary_admission_preserves_abuse_caps(writer_world):
    from test_transactions import PAYLOAD, WORKER
    w, world, _, _ = writer_world
    a, e, _, row, _, _ = world
    w.set_complimentary(OPERATOR, ACCOUNT, 'grant', enabled=True, reason_code='OWNER_GRANT')
    proof = a.prepare(e, PAYLOAD, 'comp-check')
    admitted = a.admit(e, PAYLOAD, proof)
    assert a.settle(WORKER, proof, admitted['executionToken'], 'complete')['chargedChecks'] == 0
    assert row('ACCESS')['validUntilEpoch'] is None
    assert row('INFLIGHT')['activeCount'] == 0
    a.s = replace(a.s, attempts_per_window=1)
    failure('RATE_LIMITED', lambda: a.prepare(e, PAYLOAD, 'abuse-cap'))


def test_trial_retention_gate_fails_closed_without_owner_approval(writer_world):
    w, world, _, _ = writer_world
    w.trial_retention_approved = False
    failure('TRIAL_RETENTION_APPROVAL_REQUIRED', lambda: w.activate_trial(world[1]))
    assert world[3]('TRIAL_HISTORY') is None and world[3]('ACCESS') is None
