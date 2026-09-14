import logging

import boto3

from config import DEVICE_BINDINGS_TABLE_NAME
from errors import AppError
from shared_history.errors import HistoryError
from shared_history.security import (
    assert_active_device_binding as _assert_shared_binding,
    device_binding_fingerprint,
)

LOGGER = logging.getLogger(__name__)

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DEVICE_BINDINGS_TABLE_NAME) if DEVICE_BINDINGS_TABLE_NAME else None

def assert_active_device_binding(event: dict, account_id: str):
    try:
        device_binding_fingerprint(event)
    except HistoryError as err:
        raise AppError(err.code, err.message, retryable=err.retryable, details=err.details) from err
    _require_table()
    LOGGER.info(
        "Stage detail: device_binding_table_ready | tableName=%s",
        DEVICE_BINDINGS_TABLE_NAME,
    )
    try:
        _assert_shared_binding(event, account_id, table)
    except HistoryError as err:
        raise AppError(err.code, err.message, retryable=err.retryable, details=err.details) from err
    LOGGER.info("Stage detail: active_device_binding_match")


def _require_table():
    if not table:
        LOGGER.warning(
            "Stage detail: device_binding_table_missing | configuredTableName=%s",
            DEVICE_BINDINGS_TABLE_NAME,
        )
        raise AppError("SERVER_UNAVAILABLE", "The device bindings table is not configured.", retryable=False)
