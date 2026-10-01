"""Isolated Moto transactions; no real store, AWS or purchase calls."""
import os
import sys
from copy import deepcopy
from pathlib import Path

import pytest

if os.environ.get("AMT_AUTHORITY_INTEGRATION") != "1":
    pytest.skip("Run with AMT_AUTHORITY_INTEGRATION=1 in isolated Moto environment", allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import boto3
from moto import mock_aws
from shared_purchase_ownership.service import (
    INVENTORY_KEY, OwnershipError, OwnershipStore, locator_item, owner_key,
    token_hash, verified_lineage,
)

PRODUCT = "trustcheck_radar_pro_monthly"
TOKEN = "synthetic-token-123456789"
HASH = token_hash(TOKEN)
NOW = 1800000000


@pytest.fixture
def world():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        client = boto3.client("dynamodb", region_name="us-east-1")
        for name in ("ownership", "users", "deletion"):
            ddb.create_table(TableName=name, KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}], AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}, {"AttributeName": "SK", "AttributeType": "S"}], BillingMode="PAY_PER_REQUEST")
        for account in ("account-a", "account-b"):
            ddb.Table("users").put_item(Item={"PK": "USER#" + account, "SK": "PROFILE", "sub": account, "status": "ACTIVE", "ageVerified": True})
        table = ddb.Table("ownership")
        table.put_item(Item=INVENTORY_KEY | {"recordType": "PURCHASE_OWNERSHIP_INVENTORY", "schemaVersion": 1, "revision": 1, "coverage": "VERIFIED_COMPLETE", "environment": "dev"})
        store = OwnershipStore(table=table, ledger=ddb.Table("deletion"), users_table_name="users", table_name="ownership", ledger_table_name="deletion", client=client, environment="dev", now=lambda: NOW)
        yield store, ddb


def entitlement(account):
    return {"PK": "USER#" + account, "SK": "ENTITLEMENT#google_play#" + PRODUCT,
            "accountId": account, "platform": "google_play", "productId": PRODUCT,
            "entitlementTier": "PRO", "remainingMonthlyScans": 193}


def claim(store, account="account-a", hashes=(HASH,)):
    expected = store._get({"PK": "USER#" + account, "SK": entitlement(account)["SK"]})
    store.claim(account, hashes, product_id=PRODUCT, entitlement=entitlement(account), expected_entitlement=expected)


def fence(ddb, account="account-a"):
    command = {"PK": "ACCOUNT#" + account, "SK": "ACCOUNT_DELETION", "schemaVersion": 1, "recordVersion": 1,
               "environment": "dev", "eventType": "account.deletion.requested", "accountId": account,
               "operationId": "ee9a078b-ae70-4d6f-8229-e8a34a72c9ec", "status": "REQUESTED",
               "occurredAtEpoch": NOW, "deleteByEpoch": NOW + 86400}
    ddb.Table("deletion").put_item(Item=command)
    return command


def response(linked=None, state="SUBSCRIPTION_STATE_ACTIVE"):
    value = {"subscriptionState": state, "startTime": "2026-01-01T00:00:00Z",
             "lineItems": [{"productId": PRODUCT, "expiryTime": "2028-01-01T00:00:00Z"}]}
    if linked is not None:
        value["linkedPurchaseToken"] = linked
    return value


def test_fresh_lineage_reads_every_alias_without_retaining_raw_tokens():
    predecessor = "synthetic-old-token-12345"
    observed = []
    def fetch(value):
        observed.append(value)
        return response(predecessor) if value == TOKEN else response(state="SUBSCRIPTION_STATE_EXPIRED")
    hashes, verified = verified_lineage(TOKEN, fetch=fetch, product_id=PRODUCT, now_epoch=NOW)
    assert observed == [TOKEN, predecessor]
    assert hashes == (HASH, token_hash(predecessor))
    assert verified["isAccessGranted"] is True
    assert TOKEN not in repr((hashes, verified)) and predecessor not in repr((hashes, verified))


@pytest.mark.parametrize("payload", [None, {}, response(linked=""), response(linked=TOKEN), response(state="SUBSCRIPTION_STATE_EXPIRED"), response() | {"lineItems": [{"productId": "wrong"}]}])
def test_unverified_or_cyclic_purchase_fails_closed(payload):
    with pytest.raises(OwnershipError):
        verified_lineage(TOKEN, fetch=lambda _: payload, product_id=PRODUCT, now_epoch=NOW)


def test_overlong_lineage_is_not_silently_truncated():
    calls = []
    def fetch(value):
        calls.append(value)
        return response("synthetic-token-" + str(len(calls)))
    with pytest.raises(OwnershipError, match="LINEAGE_LIMIT"):
        verified_lineage(TOKEN, fetch=fetch, product_id=PRODUCT, now_epoch=NOW)
    assert len(calls) == 8


def test_store_failure_has_no_provider_details():
    def fetch(_):
        raise RuntimeError(TOKEN)
    with pytest.raises(OwnershipError) as error:
        verified_lineage(TOKEN, fetch=fetch, product_id=PRODUCT, now_epoch=NOW)
    assert TOKEN not in str(error.value)


def test_atomic_claim_creates_all_locks_locators_and_entitlement(world):
    store, _ = world
    older = token_hash("synthetic-old-token-12345")
    claim(store, hashes=(HASH, older))
    for digest in (HASH, older):
        owner = store._get(owner_key(digest))
        assert owner["accountId"] == "account-a" and owner["revision"] == 1
        locator = locator_item("account-a", digest)
        assert store._get({"PK": locator["PK"], "SK": locator["SK"]}) == locator
        assert "expiresAt" not in owner and TOKEN not in repr(owner)
    assert store._get({"PK": "USER#account-a", "SK": entitlement("account-a")["SK"]})["remainingMonthlyScans"] == 193


def test_same_account_reverify_increments_revision_and_preserves_given_usage(world):
    store, _ = world
    claim(store)
    claim(store)
    assert store._get(owner_key(HASH))["revision"] == 2
    assert store._get({"PK": "USER#account-a", "SK": entitlement("account-a")["SK"]})["remainingMonthlyScans"] == 193


def test_other_owner_conflicts_even_after_deletion_fence_until_cleanup(world):
    store, ddb = world
    claim(store)
    fence(ddb)
    with pytest.raises(OwnershipError, match="OWNERSHIP_CONFLICT"):
        claim(store, "account-b")
    assert store._get({"PK": "USER#account-b", "SK": entitlement("account-b")["SK"]}) is None


def test_linked_other_owner_blocks_entire_new_chain_without_partial_claim(world):
    store, _ = world
    claim(store)
    new_hash = token_hash("synthetic-next-token-12345")
    with pytest.raises(OwnershipError, match="OWNERSHIP_CONFLICT"):
        claim(store, "account-b", hashes=(new_hash, HASH))
    assert store._get(owner_key(new_hash)) is None


def test_deletion_race_atomically_rejects_old_queued_claim(world, monkeypatch):
    store, ddb = world
    original = store._transact
    def racing(operations):
        fence(ddb)
        original(operations)
    monkeypatch.setattr(store, "_transact", racing)
    with pytest.raises(OwnershipError, match="TRANSACTION_UNCONFIRMED"):
        claim(store)
    assert store._get(owner_key(HASH)) is None
    assert store._get({"PK": "USER#account-a", "SK": entitlement("account-a")["SK"]}) is None


def test_slow_reverification_cannot_overwrite_concurrent_usage_debit(world, monkeypatch):
    store, _ = world
    claim(store)
    original = store._transact
    def debit_before_commit(operations):
        store.table.put_item(Item=entitlement("account-a") | {"remainingMonthlyScans": 192})
        original(operations)
    monkeypatch.setattr(store, "_transact", debit_before_commit)
    with pytest.raises(OwnershipError, match="TRANSACTION_UNCONFIRMED"):
        claim(store)
    assert store._get({"PK": "USER#account-a", "SK": entitlement("account-a")["SK"]})["remainingMonthlyScans"] == 192
    assert store._get(owner_key(HASH))["revision"] == 1


def test_concurrent_other_claim_loses_atomically(world, monkeypatch):
    store, _ = world
    original = store._transact
    fired = False
    def racing(operations):
        nonlocal fired
        if not fired:
            fired = True
            claim(store, "account-b")
        original(operations)
    monkeypatch.setattr(store, "_transact", racing)
    with pytest.raises(OwnershipError, match="TRANSACTION_UNCONFIRMED"):
        claim(store)
    assert store._get(owner_key(HASH))["accountId"] == "account-b"
    assert store._get({"PK": "USER#account-a", "SK": entitlement("account-a")["SK"]}) is None


def test_owned_cleanup_removes_binding_before_new_account_can_claim(world):
    store, ddb = world
    claim(store)
    command = fence(ddb)
    results = []
    for _ in range(4):
        result = store.delete_owned_batch(command, limit=1)
        results.append(result)
        if result["complete"]:
            break
    assert results[-1]["complete"] and len(results) == 3
    assert store._get(owner_key(HASH)) is None
    claim(store, "account-b")
    assert store._get(owner_key(HASH))["accountId"] == "account-b"
    with pytest.raises(OwnershipError):
        claim(store)
    # Re-running old cleanup cannot remove the newly bound account's row.
    assert store.delete_owned_batch(command)["complete"]
    assert store._get(owner_key(HASH))["accountId"] == "account-b"


def test_forged_or_changed_command_cannot_delete(world):
    store, ddb = world
    claim(store)
    command = fence(ddb)
    with pytest.raises(OwnershipError):
        store.delete_owned_batch(command | {"schemaVersion": True})
    with pytest.raises(OwnershipError):
        store.delete_owned_batch(command | {"operationId": "ee9a078b-ae70-4d6f-8229-e8a34a72c9ed"})
    assert store._get(owner_key(HASH)) is not None


def test_unverified_legacy_coverage_blocks_empty_completion_and_export(world):
    store, ddb = world
    store.table.delete_item(Key=INVENTORY_KEY)
    with pytest.raises(OwnershipError, match="COVERAGE"):
        store.delete_owned_batch(fence(ddb))
    with pytest.raises(OwnershipError, match="COVERAGE"):
        store.owned_page("account-a")
    with pytest.raises(OwnershipError, match="COVERAGE"):
        claim(store)


def test_inventory_revision_race_atomically_prevents_claim(world, monkeypatch):
    store, _ = world
    original = store._transact
    def change_inventory(operations):
        store.table.put_item(Item=store.inventory() | {"revision": 2, "coverage": "PENDING"})
        original(operations)
    monkeypatch.setattr(store, "_transact", change_inventory)
    with pytest.raises(OwnershipError, match="TRANSACTION_UNCONFIRMED"):
        claim(store)
    assert store._get(owner_key(HASH)) is None
    assert store._get({"PK": "USER#account-a", "SK": entitlement("account-a")["SK"]}) is None


def test_inventory_change_during_store_verification_invalidates_prior_inventory(world):
    store, _ = world
    expected = store.inventory()
    store.table.put_item(Item=expected | {"revision": 2})
    with pytest.raises(OwnershipError, match="COVERAGE_CHANGED"):
        store.claim("account-a", (HASH,), product_id=PRODUCT, entitlement=entitlement("account-a"), expected_entitlement=None, expected_inventory=expected)
    assert store._get(owner_key(HASH)) is None


def test_missing_or_foreign_locator_target_is_not_silently_skipped(world):
    store, ddb = world
    claim(store)
    store.table.delete_item(Key=owner_key(HASH))
    with pytest.raises(OwnershipError):
        store.owned_page("account-a")
    with pytest.raises(OwnershipError):
        store.delete_owned_batch(fence(ddb))


def test_export_is_minimized_and_account_cursor_is_bound(world):
    store, _ = world
    claim(store)
    result = store.owned_page("account-a")
    assert result["records"] == [{"platform": "google_play", "productId": PRODUCT, "verifiedAtEpoch": NOW}]
    assert "account-a" not in repr(result["records"]) and HASH not in repr(result["records"])
    with pytest.raises(OwnershipError, match="CURSOR"):
        store.owned_page("account-a", cursor={"PK": "USER#account-b", "SK": "PURCHASE_TOKEN#" + HASH})


def test_legacy_token_rows_are_blocked_until_reviewed_migration(world):
    store, _ = world
    store.table.put_item(Item=owner_key(HASH) | {"accountId": "account-a", "verificationStatus": "accepted"})
    with pytest.raises(OwnershipError, match="UNVERIFIED"):
        claim(store)
