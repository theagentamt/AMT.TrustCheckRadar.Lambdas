"""Synthetic branch qualification; no provider calls or live-cause attribution."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests/shared_play_verification'))
from test_proof import TOKEN, NOW, fixture
from shared_play_verification.proof import PlayVerificationError, discover_lineage, verify
from v1_play_handoff.app import _code, ERRORS

PREDECESSOR = 'synthetic-predecessor-token-123456'


def proof(head, order, *, predecessor=None, **options):
    def subscription(token):
        return deepcopy(predecessor if token == PREDECESSOR else head)
    return verify(TOKEN, fetch_subscription=subscription,
                  fetch_order=lambda _: deepcopy(order), now_epoch=NOW, **options)


@pytest.mark.parametrize('field', ['deferredItemReplacement', 'deferredItemRemoval', 'itemReplacement', 'outOfAppPurchaseContext'])
@pytest.mark.parametrize('location', ['head', 'predecessor'])
def test_context_rejection_applies_to_each_lineage_member(field, location):
    head, order = fixture()
    previous = deepcopy(head)
    if location == 'predecessor':
        head['linkedPurchaseToken'] = PREDECESSOR
    target = head if location == 'head' else previous
    (target if field == 'outOfAppPurchaseContext' else target['lineItems'][0])[field] = {}
    with pytest.raises(PlayVerificationError, match='^PLAY_TRANSITION_UNSUPPORTED$'):
        proof(head, order, predecessor=previous)


@pytest.mark.parametrize('state', ['SUBSCRIPTION_STATE_PENDING', 'SUBSCRIPTION_STATE_PENDING_PURCHASE_CANCELED'])
def test_pending_linked_proof_is_unsupported_not_new_funding(state):
    head, order = fixture()
    previous = deepcopy(head)
    head.update(subscriptionState=state, linkedPurchaseToken=PREDECESSOR)
    with pytest.raises(PlayVerificationError, match='^PLAY_TRANSITION_UNSUPPORTED$'):
        proof(head, order, predecessor=previous)


@pytest.mark.parametrize('expiry', ['2026-09-30T00:00:00Z', '2026-10-02T00:00:00Z'])
def test_closed_extension_policy_rejects_both_different_expiry_directions(expiry):
    head, order = fixture()
    head['lineItems'][0]['expiryTime'] = expiry
    with pytest.raises(PlayVerificationError, match='^PLAY_PERIOD_EXTENSION_UNSUPPORTED$'):
        proof(head, order)


def test_normal_single_head_and_same_plan_linked_proof_can_verify():
    head, order = fixture()
    assert proof(head, order).active
    previous = deepcopy(head)
    head['linkedPurchaseToken'] = PREDECESSOR
    linked = proof(head, order, predecessor=previous)
    assert linked.active and len(linked.token_hashes) == 2


def test_explicit_extension_policy_preserves_funded_dates_without_minting_a_period():
    head, order = fixture()
    normal = proof(head, order)
    head['lineItems'][0]['expiryTime'] = '2026-10-02T00:00:00Z'
    extended = proof(head, order, allow_access_extension=True)
    assert extended.period_start_epoch == normal.period_start_epoch
    assert extended.period_end_epoch == normal.period_end_epoch
    assert extended.access_until_epoch > extended.period_end_epoch


@pytest.mark.parametrize('internal', ['PLAY_TRANSITION_UNSUPPORTED', 'PLAY_PERIOD_EXTENSION_UNSUPPORTED', 'RESTORE_POLICY_UNAVAILABLE'])
def test_public_mapping_does_not_identify_the_internal_predicate(internal):
    assert _code(PlayVerificationError(internal)) == 'PURCHASE_TRANSITION_UNSUPPORTED'
    assert ERRORS['PURCHASE_TRANSITION_UNSUPPORTED'] == (409, False)


def test_other_409_and_migration_must_not_be_inferred_from_this_code():
    assert _code(PlayVerificationError('PURCHASE_ACCOUNT_MISMATCH')) == 'PURCHASE_ACCOUNT_MISMATCH'
    assert _code(PlayVerificationError('STORE_AUTHORITY_MIGRATION_REQUIRED')) == 'PURCHASE_RECONCILIATION_REQUIRED'
    assert ERRORS['PURCHASE_RECONCILIATION_REQUIRED'] == (503, True)
