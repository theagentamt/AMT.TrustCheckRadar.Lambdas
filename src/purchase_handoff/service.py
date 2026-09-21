from datetime import UTC, datetime

import boto3

import config
from errors import AppError
from idempotency import (
    PurchaseReplayConflictError,
    build_idempotency_record,
    hash_purchase_token,
    load_idempotency_record,
)
from shared_entitlements import (
    EntitlementStoreNotConfiguredError,
    apply_google_play_subscription,
    build_entitlement_snapshot,
    build_usage_snapshot,
    load_entitlement,
)
from verification import verify_purchase


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

    now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    token_hash = hash_purchase_token(payload["purchaseToken"])
    existing_idempotency = load_idempotency_record(token_hash)

    if existing_idempotency:
        if existing_idempotency.get("accountId") != account_id:
            return _build_response(
                verification_status="rejected",
                verification_reason="Purchase token is already associated with a different account.",
                entitlement=load_entitlement(account_id, now_iso=now_iso),
                idempotency_replay=True,
            )
        if existing_idempotency.get("verificationStatus") in {"accepted", "rejected"}:
            entitlement = load_entitlement(account_id, platform=payload["platform"], product_id=payload["productId"], now_iso=now_iso)
            return _build_response(
                verification_status=existing_idempotency["verificationStatus"],
                verification_reason="Purchase handoff replay matched a previously processed Google Play purchase token.",
                entitlement=entitlement,
                idempotency_replay=True,
            )

    verification = verify_purchase(payload)
    current = load_entitlement(account_id, platform=payload["platform"], product_id=payload["productId"], now_iso=now_iso)

    if verification["status"] == "accepted":
        updated = apply_google_play_subscription(current, verification, payload, now_iso)
        try:
            idempotency = build_idempotency_record(
                token_hash=token_hash,
                account_id=account_id,
                product_id=payload["productId"],
                verification_status="accepted",
                normalized_status=verification["normalizedStatus"],
                platform=payload["platform"],
                billing_period_start_utc=verification.get("billingPeriodStartUtc"),
                billing_period_end_utc=verification.get("billingPeriodEndUtc"),
            )
            replay = _commit_verified_result(
                account_id, entitlement=updated, idempotency=idempotency,
            )
        except PurchaseReplayConflictError:
            return _build_response(
                verification_status="rejected",
                verification_reason="Purchase token is already associated with a different account.",
                entitlement=load_entitlement(account_id, platform=payload["platform"], product_id=payload["productId"], now_iso=now_iso),
                idempotency_replay=True,
            )
        if replay:
            entitlement = load_entitlement(
                account_id, platform=payload["platform"],
                product_id=payload["productId"], now_iso=now_iso,
            )
            return _build_response(
                verification_status=replay["verificationStatus"],
                verification_reason=(
                    "Purchase handoff replay matched a previously processed "
                    "Google Play purchase token."
                ),
                entitlement=entitlement, idempotency_replay=True,
            )
        return _build_response("accepted", verification["reason"], updated, idempotency_replay=False)

    if verification["status"] == "rejected":
        try:
            idempotency = build_idempotency_record(
                token_hash=token_hash,
                account_id=account_id,
                product_id=payload["productId"],
                verification_status="rejected",
                normalized_status=verification.get("normalizedStatus"),
                platform=payload["platform"],
                billing_period_start_utc=verification.get("billingPeriodStartUtc"),
                billing_period_end_utc=verification.get("billingPeriodEndUtc"),
            )
            replay = _commit_verified_result(
                account_id, entitlement=None, idempotency=idempotency,
            )
        except PurchaseReplayConflictError:
            return _build_response(
                verification_status="rejected",
                verification_reason="Purchase token is already associated with a different account.",
                entitlement=current,
                idempotency_replay=True,
            )
        if replay:
            return _build_response(
                verification_status=replay["verificationStatus"],
                verification_reason=(
                    "Purchase handoff replay matched a previously processed "
                    "Google Play purchase token."
                ),
                entitlement=current, idempotency_replay=True,
            )
        return _build_response("rejected", verification["reason"], current, idempotency_replay=False)

    if verification["status"] == "pending":
        return _build_response("pending", verification["reason"], current, idempotency_replay=False)

    return _build_response("failed_retryable", verification["reason"], current, idempotency_replay=False)


def _process_owned_purchase(account_id, payload):
    # Imported only by the explicit inactive candidate. Legacy deployment keeps
    # its old behavior until the ownership inventory/writer rollout is approved.
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
        store.inventory()
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
        store.claim(account_id, hashes, product_id=config.GOOGLE_PLAY_PRO_PRODUCT_ID, entitlement=updated, expected_entitlement=expected)
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
    try:
        load_entitlement("health-check")
    except EntitlementStoreNotConfiguredError as err:
        raise AppError("SERVER_UNAVAILABLE", str(err), retryable=False) from err
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


def _commit_verified_result(account_id, *, entitlement, idempotency):
    operations = _authority_checks(account_id)
    if entitlement is not None:
        operations.append({"Put": {
            "TableName": config.ENTITLEMENTS_TABLE_NAME,
            "Item": _serialize(entitlement),
        }})
    operations.append({"Put": {
        "TableName": config.ENTITLEMENTS_TABLE_NAME,
        "Item": _serialize(idempotency),
        "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
    }})
    try:
        dynamodb_client.transact_write_items(TransactItems=operations)
        return None
    except Exception as err:
        if getattr(err, "response", {}).get("Error", {}).get("Code") != "TransactionCanceledException":
            raise
        _assert_account_active(account_id)
        replay = load_idempotency_record(
            idempotency["purchaseTokenHash"]
        )
        if replay and replay.get("accountId") != account_id:
            raise PurchaseReplayConflictError(
                "Purchase token is already associated with a different account."
            ) from err
        if replay:
            return replay
        raise AppError(
            "CONFLICT", "Purchase state changed during verification.",
            retryable=True,
        ) from err


def _authority_checks(account_id):
    return [
        {"ConditionCheck": {
            "TableName": config.USERS_TABLE_NAME,
            "Key": _serialize({"PK": f"USER#{account_id}", "SK": "PROFILE"}),
            "ConditionExpression": (
                "#status = :active AND ageVerified = :true AND #sub = :account"
            ),
            "ExpressionAttributeNames": {"#status": "status", "#sub": "sub"},
            "ExpressionAttributeValues": _serialize({
                ":active": "ACTIVE", ":true": True, ":account": account_id,
            }),
        }},
        {"ConditionCheck": {
            "TableName": config.DELETION_LEDGER_TABLE_NAME,
            "Key": _serialize({
                "PK": f"ACCOUNT#{account_id}", "SK": "ACCOUNT_DELETION",
            }),
            "ConditionExpression": "attribute_not_exists(PK)",
        }},
    ]


def _serialize(item):
    return {key: _serialize_value(value) for key, value in item.items()}


def _serialize_value(value):
    if value is None:
        return {"NULL": True}
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, int):
        return {"N": str(value)}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, dict):
        return {"M": _serialize(value)}
    if isinstance(value, list):
        return {"L": [_serialize_value(child) for child in value]}
    raise TypeError(type(value).__name__)
