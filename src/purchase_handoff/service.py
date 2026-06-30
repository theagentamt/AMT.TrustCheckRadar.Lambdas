from datetime import UTC, datetime

from errors import AppError
from idempotency import PurchaseReplayConflictError, hash_purchase_token, load_idempotency_record, save_idempotency_record
from shared_entitlements import (
    EntitlementStoreNotConfiguredError,
    apply_google_play_subscription,
    build_entitlement_snapshot,
    build_usage_snapshot,
    load_entitlement,
    save_entitlement,
)
from verification import verify_purchase


def process_purchase_handoff(*, account_id: str, payload: dict) -> dict:
    _require_store()

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
        save_entitlement(updated)
        try:
            save_idempotency_record(
                token_hash=token_hash,
                account_id=account_id,
                product_id=payload["productId"],
                verification_status="accepted",
                normalized_status=verification["normalizedStatus"],
                platform=payload["platform"],
                billing_period_start_utc=verification.get("billingPeriodStartUtc"),
                billing_period_end_utc=verification.get("billingPeriodEndUtc"),
            )
        except PurchaseReplayConflictError:
            return _build_response(
                verification_status="rejected",
                verification_reason="Purchase token is already associated with a different account.",
                entitlement=load_entitlement(account_id, platform=payload["platform"], product_id=payload["productId"], now_iso=now_iso),
                idempotency_replay=True,
            )
        return _build_response("accepted", verification["reason"], updated, idempotency_replay=False)

    if verification["status"] == "rejected":
        try:
            save_idempotency_record(
                token_hash=token_hash,
                account_id=account_id,
                product_id=payload["productId"],
                verification_status="rejected",
                normalized_status=verification.get("normalizedStatus"),
                platform=payload["platform"],
                billing_period_start_utc=verification.get("billingPeriodStartUtc"),
                billing_period_end_utc=verification.get("billingPeriodEndUtc"),
            )
        except PurchaseReplayConflictError:
            return _build_response(
                verification_status="rejected",
                verification_reason="Purchase token is already associated with a different account.",
                entitlement=current,
                idempotency_replay=True,
            )
        return _build_response("rejected", verification["reason"], current, idempotency_replay=False)

    if verification["status"] == "pending":
        return _build_response("pending", verification["reason"], current, idempotency_replay=False)

    return _build_response("failed_retryable", verification["reason"], current, idempotency_replay=False)


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
