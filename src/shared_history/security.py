import hashlib
import hmac
import re

from .errors import HistoryError

DEVICE_BINDING_HEADER = "x-device-binding-fingerprint"
FINGERPRINT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
SUBJECT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,199}$")


def jwt_subject(event: dict) -> str:
    claims = (((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}).get("claims")
    subject = claims.get("sub") if isinstance(claims, dict) else None
    if not isinstance(subject, str) or not SUBJECT_PATTERN.fullmatch(subject.strip()):
        raise HistoryError("UNAUTHORIZED", "A verified Cognito JWT subject is required.")
    return subject.strip()


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
