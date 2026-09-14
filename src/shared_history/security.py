import hashlib
import hmac
import re
import time

from .errors import HistoryError

DEVICE_BINDING_HEADER = "x-device-binding-fingerprint"
FINGERPRINT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
SUBJECT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,199}$")


def jwt_subject(event: dict, settings, *, now=lambda: int(time.time())) -> str:
    claims = (((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}).get("claims")
    if not isinstance(claims, dict):
        raise HistoryError("UNAUTHORIZED", "A verified Cognito access token is required.")
    subject = claims.get("sub")
    expiration = _exact_epoch(claims.get("exp"))
    scopes = claims.get("scope", "").split() if isinstance(claims.get("scope"), str) else []
    if (
        not isinstance(subject, str)
        or not SUBJECT_PATTERN.fullmatch(subject.strip())
        or claims.get("iss") != settings.cognito_issuer
        or claims.get("client_id") != settings.cognito_app_client_id
        or claims.get("token_use") != "access"
        or expiration is None
        or expiration <= now()
        or settings.cognito_required_scope not in scopes
    ):
        raise HistoryError("UNAUTHORIZED", "A verified Cognito access token is required.")
    return subject.strip()


def _exact_epoch(value):
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def assert_authoritative_account_active(account_id: str, users_table, deletion_ledger_table) -> None:
    profile = users_table.get_item(
        Key={"PK": f"USER#{account_id}", "SK": "PROFILE"}, ConsistentRead=True
    ).get("Item")
    deletion = deletion_ledger_table.get_item(
        Key={"PK": f"ACCOUNT#{account_id}", "SK": "ACCOUNT_DELETION"},
        ConsistentRead=True,
    ).get("Item")
    if (
        not profile or profile.get("sub") != account_id
        or profile.get("status") != "ACTIVE" or profile.get("ageVerified") is not True
        or deletion is not None
    ):
        raise HistoryError("FORBIDDEN", "The account is not active.")


def assert_active_device_binding(event: dict, account_id: str, table) -> None:
    value = device_binding_fingerprint(event)
    response = table.query(
        IndexName="GSI1",
        KeyConditionExpression="GSI1PK = :gsi1pk",
        ExpressionAttributeValues={":gsi1pk": f"USER#{account_id}#ACTIVE"},
        Limit=1,
        ScanIndexForward=False,
    )
    items = response.get("Items") or []
    if not items or not hmac.compare_digest(str(items[0].get("bindingFingerprint", "")), value):
        raise HistoryError("DEVICE_BINDING_MISMATCH", "The active device binding does not match.")


def device_binding_fingerprint(event: dict) -> str:
    headers = event.get("headers") or {}
    value = next(
        (candidate for key, candidate in headers.items() if isinstance(key, str) and key.lower() == DEVICE_BINDING_HEADER),
        None,
    ) if isinstance(headers, dict) else None
    if not isinstance(value, str) or not FINGERPRINT_PATTERN.fullmatch(value.strip()):
        raise HistoryError("DEVICE_BINDING_REQUIRED", "A valid device binding fingerprint is required.")
    return value.strip()


def subject_binding(account_id: str, secret: bytes) -> str:
    return hmac.new(secret, account_id.encode("utf-8"), hashlib.sha256).hexdigest()
