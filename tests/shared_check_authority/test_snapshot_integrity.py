"""Snapshot/eligibility corruption and interleaving tests against actual SDK/Moto."""
import os
import sys
from dataclasses import replace
from copy import deepcopy
from pathlib import Path

import pytest

if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run isolated authority emulator suite', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_entitlements import writer_world, world, ACCOUNT, OPERATOR
from test_transactions import failure
from shared_check_authority.core import OWNER_POLICY, TRIAL_SECONDS
from v1_entitlements.service import access_snapshot


def history(w, epoch, key='k1', **extra):
    if key not in w.a.s.hmac_keys:
        w.a.s = replace(w.a.s, hmac_keys=w.a.s.hmac_keys | {key: b'synthetic-retained-history-key-0000'})
    row = {'PK': w.a._partition(ACCOUNT, key), 'SK': 'TRIAL_HISTORY',
           'recordType': 'V1_TRIAL_ELIGIBILITY', 'policyVersion': OWNER_POLICY,
           'activationKind': 'explicit', 'activatedAtEpoch': epoch} | extra
    w.a.ddb.Table('authority').put_item(Item=row)
    return row


@pytest.mark.parametrize('change', [
    {'activatedAtEpoch': True}, {'activatedAtEpoch': -1}, {'activatedAtEpoch': '1'},
    {'activatedAtEpoch': 9999999999}, {'activationKind': 'automatic'},
    {'recordType': 'unrecognized'}, {'policyVersion': 'unreviewed'}, {'expiresAt': 9999999999},
])
def test_malformed_history_preserved_without_new_authority(writer_world, change):
    w, values, _, _ = writer_world
    a, event, _, row, _, clock = values
    original = history(w, clock[0], **change)
    failure('AUTHORITY_STATE_INVALID', lambda: access_snapshot(w, event))
    failure('AUTHORITY_STATE_INVALID', lambda: w.activate_trial(event))
    assert row('TRIAL_HISTORY') == original and row('ACCESS') is None


def test_conflicting_retained_history_clocks_never_normalized(writer_world):
    w, values, _, _ = writer_world
    history(w, values[5][0])
    history(w, values[5][0] - 1, 'old')
    failure('AUTHORITY_STATE_INVALID', lambda: access_snapshot(w, values[1]))
    failure('AUTHORITY_STATE_INVALID', lambda: w.activate_trial(values[1]))
    assert values[3]('ACCESS') is None


def test_identical_retained_history_preserves_original_activation(writer_world):
    w, values, _, _ = writer_world
    epoch = values[5][0] - 100
    history(w, epoch)
    history(w, epoch, 'old')
    snapshot = access_snapshot(w, values[1])
    assert snapshot['trial'] == {'activationAvailable': False, 'activatedAtEpoch': epoch,
                                 'expiresAtEpoch': epoch + TRIAL_SECONDS}
    assert w.activate_trial(values[1])['alreadyActivated'] is True
    assert values[3]('ACCESS') is None


def test_missing_history_with_expired_trial_source_is_not_new_eligibility(writer_world):
    w, values, _, _ = writer_world
    a, event, _, row, _, clock = values
    w.activate_trial(event)
    a.ddb.Table('authority').delete_item(Key={'PK': a._partition(ACCOUNT, 'k1'), 'SK': 'TRIAL_HISTORY'})
    clock[0] += TRIAL_SECONDS + 1
    event['requestContext']['authorizer']['jwt']['claims']['exp'] = str(clock[0] + 60)
    before = row('ACCESS')
    failure('AUTHORITY_STATE_INVALID', lambda: access_snapshot(w, event))
    failure('AUTHORITY_STATE_INVALID', lambda: w.activate_trial(event))
    assert row('ACCESS') == before


@pytest.mark.parametrize('used,reserved', [(11, 0), (10, 1), (1, 10)])
def test_over_limit_trial_is_corruption_not_ordinary_exhaustion(writer_world, used, reserved):
    w, values, _, _ = writer_world
    w.activate_trial(values[1])
    sk = 'PERIOD#' + values[3]('ACCESS')['periodId']
    values[4]('authority', sk, usedChecks=used, reservedChecks=reserved)
    failure('AUTHORITY_STATE_INVALID', lambda: access_snapshot(w, values[1]))
    assert values[3](sk)['usedChecks'] == used and values[3](sk)['reservedChecks'] == reserved


@pytest.mark.parametrize('field', ['startEpoch', 'endEpoch'])
def test_trial_period_must_match_retained_seven_day_activation(writer_world, field):
    w, values, _, _ = writer_world
    w.activate_trial(values[1])
    sk = 'PERIOD#' + values[3]('ACCESS')['periodId']
    original = values[3](sk)
    values[4]('authority', sk, **{field: original[field] - 1})
    failure('AUTHORITY_STATE_INVALID', lambda: access_snapshot(w, values[1]))


@pytest.mark.parametrize('race', ['version', 'removed', 'revoked'])
def test_device_change_during_snapshot_returns_existing_retryable_conflict(writer_world, monkeypatch, race):
    w, values, _, _ = writer_world
    a, event, _, _, change, _ = values
    w.activate_trial(event)
    original = a._grant
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if race == 'version': change('devices', 'ACTIVE_BINDING', stateVersion=2)
        elif race == 'revoked': change('devices', 'DEVICE#device-one', status='REVOKED')
        else: a.ddb.Table('devices').delete_item(Key={'PK': 'USER#'+ACCOUNT, 'SK': 'ACTIVE_BINDING'})
        return result
    monkeypatch.setattr(a, '_grant', changed)
    failure('AUTHORITY_SNAPSHOT_CHANGED', lambda: access_snapshot(w, event))


def test_no_access_snapshot_cannot_miss_concurrent_trial_activation(writer_world, monkeypatch):
    w, values, _, _ = writer_world
    original = w.a._grant
    def changed(*args, **kwargs):
        try: return original(*args, **kwargs)
        finally: w.activate_trial(values[1])
    monkeypatch.setattr(w.a, '_grant', changed)
    failure('AUTHORITY_SNAPSHOT_CHANGED', lambda: access_snapshot(w, values[1]))
    assert values[3]('TRIAL_HISTORY') is not None


def test_trial_history_change_during_snapshot_is_not_new_eligibility(writer_world, monkeypatch):
    w, values, _, _ = writer_world
    original = w.a._grant
    def changed(*args, **kwargs):
        try: return original(*args, **kwargs)
        finally: history(w, values[5][0])
    monkeypatch.setattr(w.a, '_grant', changed)
    failure('AUTHORITY_SNAPSHOT_CHANGED', lambda: access_snapshot(w, values[1]))


def test_snapshot_crossing_expiry_is_not_available(writer_world, monkeypatch):
    w, values, _, _ = writer_world
    w.activate_trial(values[1])
    original = w.a._grant
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        values[5][0] += TRIAL_SECONDS
        return result
    monkeypatch.setattr(w.a, '_grant', changed)
    failure('AUTHORITY_SNAPSHOT_CHANGED', lambda: access_snapshot(w, values[1]))


@pytest.mark.parametrize('basis', ['trial', 'paid', 'complimentary'])
@pytest.mark.parametrize('mutation', ['unknown_state', 'missing_state', 'extra_field', 'invalid_clock'])
def test_unknown_source_never_rewrites_access_or_creates_eligibility(writer_world, basis, mutation):
    w, values, _, _ = writer_world
    a, event, _, row, _, _ = values
    if basis == 'trial': w.activate_trial(event)
    elif basis == 'paid': w.synchronize_paid(event, 'synthetic-proof', 'source-test')
    else: w.set_complimentary(OPERATOR, ACCOUNT, 'source-test', enabled=True, reason_code='OWNER_GRANT')
    changed = deepcopy(row('ACCESS'))
    source = changed['sources'][basis]
    if mutation == 'unknown_state': source['state'] = 'UNKNOWN'
    elif mutation == 'missing_state': source.pop('state')
    elif mutation == 'extra_field': source['unreviewed'] = True
    else: source['validFromEpoch'] = True
    a.ddb.Table('authority').put_item(Item=changed)
    before_history = row('TRIAL_HISTORY')
    failure('AUTHORITY_STATE_INVALID', lambda: access_snapshot(w, event))
    failure('AUTHORITY_STATE_INVALID', lambda: w.activate_trial(event))
    assert row('ACCESS') == changed and row('TRIAL_HISTORY') == before_history


@pytest.mark.parametrize('change', [{'state': 'UNKNOWN'}, {'schemaVersion': True}, {'periodRevision': True}, {'periodId': 'different-period'}])
def test_invalid_outer_authority_preserved_before_refresh(writer_world, change):
    w, values, _, _ = writer_world
    w.synchronize_paid(values[1], 'synthetic-proof', 'outer-test')
    values[4]('authority', 'ACCESS', **change)
    before = values[3]('ACCESS')
    failure('AUTHORITY_STATE_INVALID', lambda: access_snapshot(w, values[1]))
    failure('AUTHORITY_STATE_INVALID', lambda: w.activate_trial(values[1]))
    assert values[3]('ACCESS') == before and values[3]('TRIAL_HISTORY') is None


def test_verified_inactive_paid_source_without_a_period_stays_valid(writer_world):
    w, values, decisions, _ = writer_world
    decisions[0] = replace(decisions[0], active=False)
    w.synchronize_paid(values[1], 'synthetic-proof', 'never-funded')
    before = values[3]('ACCESS')
    snapshot = access_snapshot(w, values[1])
    assert snapshot['access']['basis'] == 'none' and snapshot['trial']['activationAvailable'] is True
    assert values[3]('ACCESS') == before and values[3]('TRIAL_HISTORY') is None
