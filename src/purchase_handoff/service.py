import boto3

import config
from errors import AppError
from shared_entitlements import (
    apply_google_play_subscription,
    build_entitlement_snapshot,
    build_usage_snapshot,
    load_entitlement,
)


resource = boto3.resource("dynamodb")
users_table = (
    resource.Table(config.USERS_TABLE_NAME) if config.USERS_TABLE_NAME else None
)
deletion_ledger_table = (
    resource.Table(config.DELETION_LEDGER_TABLE_NAME)
    if config.DELETION_LEDGER_TABLE_NAME else None
)
dynamodb_client = boto3.client("dynamodb")


def process_purchase_handoff(*, account_id: str, payload: dict) -> dict:
    _require_store()
    _assert_account_active(account_id)
    if config.PURCHASE_OWNERSHIP_CANDIDATE_ENABLED:
        return _process_owned_purchase(account_id, payload)

    # No flag can revive the legacy unconditional entitlement replacement. The
    # independently gated ownership candidate remains unavailable at cutover.
    raise AppError("LEGACY_MIGRATION_REQUIRED", "Purchase verification requires migration qualification.", retryable=False)


def _process_owned_purchase(account_id, payload):
    # Imported only by the explicit inactive candidate after its separate
    # ownership inventory and writer rollout have been qualified.
    import time
    from google_play_client import fetch_subscription_purchase
    from shared_purchase_ownership import OwnershipError, OwnershipStore, verified_lineage

    store = OwnershipStore(
        table=resource.Table(config.ENTITLEMENTS_TABLE_NAME), ledger=deletion_ledger_table,
        users_table_name=config.USERS_TABLE_NAME, table_name=config.ENTITLEMENTS_TABLE_NAME,
        ledger_table_name=config.DELETION_LEDGER_TABLE_NAME, client=dynamodb_client,
        environment=config.APP_ENVIRONMENT, now=lambda: int(time.time()),
    )
    expected = store._get({"PK": "USER#" + account_id, "SK": "ENTITLEMENT#google_play#" + config.GOOGLE_PLAY_PRO_PRODUCT_ID})
    current = load_entitlement(account_id, platform=payload["platform"], product_id=payload["productId"])
    try:
        # Never trust package/product supplied by a direct caller; validation at
        # the public handler is an additional boundary, not this writer's only one.
        if payload.get("platform") != "google_play" or payload.get("packageName") != config.GOOGLE_PLAY_PACKAGE_NAME or payload.get("productId") != config.GOOGLE_PLAY_PRO_PRODUCT_ID:
            raise OwnershipError("PURCHASE_PRODUCT_MISMATCH")
        inventory = store.inventory()
        hashes, verification = verified_lineage(
            payload["purchaseToken"], product_id=config.GOOGLE_PLAY_PRO_PRODUCT_ID,
            now_epoch=int(time.time()),
            fetch=lambda token: fetch_subscription_purchase(package_name=config.GOOGLE_PLAY_PACKAGE_NAME, purchase_token=token),
        )
        updated = apply_google_play_subscription(
            current, verification, {**payload, "purchaseTokenHash": hashes[0]},
        )
        # The ownership candidate has no purpose for raw store order identifiers.
        updated.pop("orderId", None)
        store.claim(account_id, hashes, product_id=config.GOOGLE_PLAY_PRO_PRODUCT_ID, entitlement=updated, expected_entitlement=expected, expected_inventory=inventory)
    except OwnershipError as err:
        rejected = str(err) in {"PURCHASE_OWNERSHIP_CONFLICT", "PURCHASE_NOT_ACTIVE", "PURCHASE_PRODUCT_MISMATCH"}
        return _build_response(
            "rejected" if rejected else "failed_retryable",
            "Purchase could not be assigned to this account." if rejected else "Purchase ownership could not be confirmed.",
            current, idempotency_replay=False,
        )
    return _build_response("accepted", "Google Play verified the subscription purchase.", updated, idempotency_replay=False)


def _build_response(verification_status: str, verification_reason: str, entitlement: dict, *, idempotency_replay: bool) -> dict:
    entitlement_snapshot = build_entitlement_snapshot(entitlement)
    return {
        "verificationStatus": verification_status,
        "verificationReason": verification_reason,
        "idempotencyReplay": idempotency_replay,
        "entitlement": entitlement_snapshot,
        "usage": build_usage_snapshot(entitlement_snapshot),
    }


def _require_store():
    if not config.ENTITLEMENTS_TABLE_NAME:
        raise AppError("SERVER_UNAVAILABLE", "The entitlement store is not configured.", retryable=False)
    if users_table is None or deletion_ledger_table is None:
        raise AppError(
            "SERVER_UNAVAILABLE", "The account authority is not configured.",
            retryable=False,
        )


def _assert_account_active(account_id: str) -> None:
    profile = users_table.get_item(
        Key={"PK": f"USER#{account_id}", "SK": "PROFILE"},
        ConsistentRead=True,
    ).get("Item")
    deletion = deletion_ledger_table.get_item(
        Key={"PK": f"ACCOUNT#{account_id}", "SK": "ACCOUNT_DELETION"},
        ConsistentRead=True,
    ).get("Item")
    if (
        not profile
        or profile.get("sub") != account_id
        or profile.get("status") != "ACTIVE"
        or profile.get("ageVerified") is not True
        or deletion is not None
    ):
        raise AppError(
            "FORBIDDEN", "The account is not active.", retryable=False
        )
