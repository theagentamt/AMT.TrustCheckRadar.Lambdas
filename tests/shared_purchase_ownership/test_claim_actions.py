"""Native DynamoDB transaction composition; no live store or AWS calls."""
import os
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

if os.environ.get("AMT_AUTHORITY_INTEGRATION") != "1":
    pytest.skip("Run isolated ownership emulator suite", allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lifecycle import world, claim, entitlement, fence, HASH, PRODUCT, NOW
from shared_purchase_ownership.service import (
    INVENTORY_KEY, OwnershipError, locator_item, owner_key, token_hash,
)
from botocore.exceptions import ClientError


def observe(store, hashes=(HASH,), account="account-a"):
    return store.observe_claim(account, hashes, product_id=PRODUCT)


def prepare(store, observation):
    return store.prepare_claim_actions(observation, observation.hashes, product_id=PRODUCT)


def commit(store, ddb, actions, account="account-a"):
    # The modern caller contributes these guards once and uses resource/native
    # serialization, exactly as shared_check_authority.Authority._transact does.
    ddb.meta.client.transact_write_items(TransactItems=store._active_guards(account) + actions)


def test_observation_and_builder_are_read_only_copy_isolated_and_redacted(world):
    store, _ = world
    claim(store)
    before = store.table.scan()["Items"]
    observation = observe(store)
    observation.inventory["revision"] = 99
    observation.owners[0]["accountId"] = "other-account"
    observation.locators[0]["schemaVersion"] = 99
    with pytest.raises(FrozenInstanceError):
        observation.hashes = ()
    actions = prepare(store, observation)
    assert observation.inventory["revision"] == 1
    assert observation.owners[0]["accountId"] == "account-a"
    assert HASH not in repr(observation) and "account-a" not in repr(observation)
    assert store.table.scan()["Items"] == before
    assert len(actions) == 3
    assert all(next(iter(a.values()))["TableName"] == "ownership" for a in actions)
    assert all(not a.get("Put", {}).get("Item", {}).get("SK", "").startswith("ENTITLEMENT") for a in actions)


def test_fresh_chain_composes_atomically_without_legacy_grant_or_counter_reset(world):
    store, ddb = world
    older = token_hash("synthetic-older-token-1234")
    store.table.put_item(Item=entitlement("account-a"))
    actions = prepare(store, observe(store, (HASH, older)))
    assert len(actions) == 5
    commit(store, ddb, actions)
    for digest in (HASH, older):
        assert store._get(owner_key(digest))["revision"] == 1
        assert store._get(owner_key(digest))["verifiedAtEpoch"] == NOW
        locator = locator_item("account-a", digest)
        assert store._get({k: locator[k] for k in ("PK", "SK")}) == locator
    assert store._get({"PK": "USER#account-a", "SK": entitlement("account-a")["SK"]}) == entitlement("account-a")


def test_same_owner_fresh_claim_preserves_usage_and_increments_revision(world):
    store, ddb = world
    claim(store)
    commit(store, ddb, prepare(store, observe(store)))
    assert store._get(owner_key(HASH))["revision"] == 2
    assert store._get({"PK": "USER#account-a", "SK": entitlement("account-a")["SK"]})["remainingMonthlyScans"] == 193


@pytest.mark.parametrize("race", ["owner", "inventory", "deleted_pair"])
def test_provider_window_change_requires_fresh_observation(world, race):
    store, _ = world
    claim(store)
    observation = observe(store)
    if race == "owner":
        store.table.put_item(Item=store._get(owner_key(HASH)) | {"revision": 2})
    elif race == "inventory":
        store.table.put_item(Item=store.inventory() | {"revision": 2})
    else:
        store.table.delete_item(Key=owner_key(HASH))
        store.table.delete_item(Key={"PK": "USER#account-a", "SK": "PURCHASE_TOKEN#" + HASH})
    with pytest.raises(OwnershipError, match="OBSERVATION_CHANGED"):
        prepare(store, observation)


@pytest.mark.parametrize("race", ["owner", "locator", "inventory", "deletion"])
def test_changes_after_prepare_abort_whole_composed_transaction(world, race):
    store, ddb = world
    claim(store)
    actions = prepare(store, observe(store))
    before = store._get(owner_key(HASH))
    if race == "owner":
        store.table.put_item(Item=before | {"revision": 2})
    elif race == "locator":
        store.table.put_item(Item=locator_item("account-a", HASH) | {"schemaVersion": 2})
    elif race == "inventory":
        store.table.put_item(Item=store.inventory() | {"revision": 2})
    else:
        fence(ddb)
    with pytest.raises(ClientError):
        commit(store, ddb, actions)
    assert store._get(owner_key(HASH)) == (before | {"revision": 2} if race == "owner" else before)


def test_concurrent_other_account_claim_blocks_all_new_aliases(world):
    store, ddb = world
    older = token_hash("synthetic-older-token-1234")
    actions = prepare(store, observe(store, (HASH, older)))
    claim(store, "account-b", (older,))
    with pytest.raises(ClientError):
        commit(store, ddb, actions)
    assert store._get(owner_key(HASH)) is None
    assert store._get(owner_key(older))["accountId"] == "account-b"
    with pytest.raises(OwnershipError, match="OWNERSHIP_CONFLICT"):
        observe(store, (HASH, older))


@pytest.mark.parametrize("change", ["missing_locator", "missing_owner", "boolean_locator", "unknown_owner"])
def test_incomplete_or_malformed_coverage_is_not_repaired(world, change):
    store, _ = world
    claim(store)
    if change == "missing_locator":
        store.table.delete_item(Key={"PK": "USER#account-a", "SK": "PURCHASE_TOKEN#" + HASH})
    elif change == "missing_owner":
        store.table.delete_item(Key=owner_key(HASH))
    elif change == "boolean_locator":
        store.table.put_item(Item=locator_item("account-a", HASH) | {"schemaVersion": True})
    else:
        store.table.put_item(Item=store._get(owner_key(HASH)) | {"unreviewed": True})
    before = store.table.scan()["Items"]
    with pytest.raises(OwnershipError):
        observe(store)
    assert store.table.scan()["Items"] == before


def test_lineage_order_product_table_environment_must_match_observation(world):
    store, _ = world
    older = token_hash("synthetic-older-token-1234")
    observation = observe(store, (HASH, older))
    for value in ((older, HASH), (HASH,), [HASH, older]):
        with pytest.raises(OwnershipError):
            store.prepare_claim_actions(observation, value, product_id=PRODUCT)
    for modified in (replace(observation, environment="prod"), replace(observation, table_name="other")):
        with pytest.raises(OwnershipError):
            prepare(store, modified)
    with pytest.raises(OwnershipError):
        store.prepare_claim_actions(observation, observation.hashes, product_id="other")


@pytest.mark.parametrize("hashes", [(), ([],), (HASH, HASH), ("invalid",), [HASH]])
def test_invalid_lineage_is_fixed_failure(world, hashes):
    with pytest.raises(OwnershipError, match="LINEAGE_INVALID"):
        observe(world[0], hashes)


def test_unverified_inventory_cannot_create_observation(world):
    store, _ = world
    store.table.delete_item(Key=INVENTORY_KEY)
    with pytest.raises(OwnershipError, match="COVERAGE"):
        observe(store)
