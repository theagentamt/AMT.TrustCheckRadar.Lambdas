import boto3

from config import DEVICE_BINDINGS_TABLE_NAME, DEVICE_BINDING_FINGERPRINT_PATTERN
from errors import AppError

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DEVICE_BINDINGS_TABLE_NAME) if DEVICE_BINDINGS_TABLE_NAME else None

DEVICE_BINDING_HEADER = "x-device-binding-fingerprint"


def assert_active_device_binding(event: dict, account_id: str):
    _require_table()
    binding_fingerprint = _extract_binding_fingerprint(event)
    active = _get_active_binding(account_id)

    if not active or active.get("bindingFingerprint") != binding_fingerprint:
        raise AppError(
            "DEVICE_BINDING_MISMATCH",
            "The active device binding for this account does not match the presented device.",
            retryable=False,
        )


def _extract_binding_fingerprint(event: dict) -> str:
    headers = event.get("headers") or {}
    value = None
    if isinstance(headers, dict):
        for key, candidate in headers.items():
            if isinstance(key, str) and key.lower() == DEVICE_BINDING_HEADER:
                value = candidate
                break

    if not isinstance(value, str) or not value.strip():
        raise AppError(
            "DEVICE_BINDING_REQUIRED",
            "A device binding fingerprint header is required for this request.",
            retryable=False,
            details=[{"field": "headers.x-device-binding-fingerprint", "issue": "Header is required."}],
        )

    binding_fingerprint = value.strip()
    if not DEVICE_BINDING_FINGERPRINT_PATTERN.fullmatch(binding_fingerprint):
        raise AppError(
            "DEVICE_BINDING_REQUIRED",
            "The device binding fingerprint header is invalid.",
            retryable=False,
            details=[{"field": "headers.x-device-binding-fingerprint", "issue": "Header contains unsupported characters or is too long."}],
        )

    return binding_fingerprint


def _get_active_binding(account_id: str):
    response = table.query(
        IndexName="GSI1",
        KeyConditionExpression="GSI1PK = :gsi1pk",
        ExpressionAttributeValues={":gsi1pk": f"USER#{account_id}#ACTIVE"},
        Limit=1,
        ScanIndexForward=False,
    )
    items = response.get("Items") or []
    return items[0] if items else None


def _require_table():
    if not table:
        raise AppError("SERVER_UNAVAILABLE", "The device bindings table is not configured.", retryable=False)
