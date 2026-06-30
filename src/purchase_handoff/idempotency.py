from datetime import UTC, datetime
import hashlib

import boto3
from botocore.exceptions import ClientError

from config import ENTITLEMENTS_TABLE_NAME
from errors import AppError


dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(ENTITLEMENTS_TABLE_NAME) if ENTITLEMENTS_TABLE_NAME else None


class PurchaseReplayConflictError(RuntimeError):
    pass


def hash_purchase_token(purchase_token: str) -> str:
    return hashlib.sha256(purchase_token.encode("utf-8")).hexdigest()


def load_idempotency_record(token_hash: str) -> dict | None:
    _require_table()
    response = table.get_item(Key={"PK": f"TOKEN#{token_hash}", "SK": "IDEMPOTENCY"})
    return response.get("Item")


def save_idempotency_record(
    *,
    token_hash: str,
    account_id: str,
    product_id: str,
    verification_status: str,
    normalized_status: str | None,
    platform: str,
    billing_period_start_utc: str | None,
    billing_period_end_utc: str | None,
) -> dict:
    _require_table()
    now_iso = _iso_now()
    item = {
        "PK": f"TOKEN#{token_hash}",
        "SK": "IDEMPOTENCY",
        "purchaseTokenHash": token_hash,
        "accountId": account_id,
        "platform": platform,
        "productId": product_id,
        "verificationStatus": verification_status,
        "normalizedStatus": normalized_status,
        "billingPeriodStartUtc": billing_period_start_utc,
        "billingPeriodEndUtc": billing_period_end_utc,
        "updatedAt": now_iso,
    }
    existing = load_idempotency_record(token_hash)
    if existing:
        if existing.get("accountId") != account_id:
            raise PurchaseReplayConflictError("Purchase token is already associated with a different account.")
        table.put_item(Item=item)
        return item

    try:
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
        return item
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            replay = load_idempotency_record(token_hash)
            if replay and replay.get("accountId") != account_id:
                raise PurchaseReplayConflictError("Purchase token is already associated with a different account.") from err
            return replay or item
        raise


def _require_table():
    if not table:
        raise AppError("SERVER_UNAVAILABLE", "The entitlements table is not configured.", retryable=False)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()
