import logging

import boto3

from config import DEVICE_BINDINGS_TABLE_NAME, DEVICE_BINDING_FINGERPRINT_PATTERN
from errors import AppError

LOGGER = logging.getLogger(__name__)

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DEVICE_BINDINGS_TABLE_NAME) if DEVICE_BINDINGS_TABLE_NAME else None

DEVICE_BINDING_HEADER = "x-device-binding-fingerprint"


def assert_active_device_binding(event: dict, account_id: str):
    binding_fingerprint = _extract_binding_fingerprint(event)
    LOGGER.info(
        "Stage detail: device_binding_header_extracted | accountId=%s fingerprintLength=%s",
        account_id,
        len(binding_fingerprint),
    )
    _require_table()
    LOGGER.info(
        "Stage detail: device_binding_table_ready | accountId=%s tableName=%s",
        account_id,
        DEVICE_BINDINGS_TABLE_NAME,
    )
    active = _get_active_binding(account_id)

    if not active:
        LOGGER.warning("Stage detail: no_active_device_binding | accountId=%s", account_id)
        raise AppError(
            "DEVICE_BINDING_MISMATCH",
            "The active device binding for this account does not match the presented device.",
            retryable=False,
        )

    active_fingerprint = active.get("bindingFingerprint")
    if active_fingerprint != binding_fingerprint:
        LOGGER.warning(
            "Stage detail: device_binding_fingerprint_mismatch | accountId=%s activeFingerprintLength=%s presentedFingerprintLength=%s",
            account_id,
            len(str(active_fingerprint or "")),
            len(binding_fingerprint),
        )
        raise AppError(
            "DEVICE_BINDING_MISMATCH",
            "The active device binding for this account does not match the presented device.",
            retryable=False,
        )

    LOGGER.info("Stage detail: active_device_binding_match | accountId=%s", account_id)


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
    LOGGER.info("Stage detail: device_binding_query_started | accountId=%s", account_id)
    response = table.query(
        IndexName="GSI1",
        KeyConditionExpression="GSI1PK = :gsi1pk",
        ExpressionAttributeValues={":gsi1pk": f"USER#{account_id}#ACTIVE"},
        Limit=1,
        ScanIndexForward=False,
    )
    items = response.get("Items") or []
    LOGGER.info("Stage detail: device_binding_query_completed | accountId=%s resultCount=%s", account_id, len(items))
    return items[0] if items else None


def _require_table():
    if not table:
        LOGGER.warning(
            "Stage detail: device_binding_table_missing | configuredTableName=%s",
            DEVICE_BINDINGS_TABLE_NAME,
        )
        raise AppError("SERVER_UNAVAILABLE", "The device bindings table is not configured.", retryable=False)
