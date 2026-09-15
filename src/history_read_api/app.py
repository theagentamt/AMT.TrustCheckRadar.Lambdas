import json
import logging
import os

import boto3

from shared_history import HistoryError, HistorySettings
from shared_history.contracts import validate_request_id
from shared_history.security import (
    assert_active_device_binding,
    assert_authoritative_account_active,
    jwt_subject,
)
try:
    from .service import CursorStore, HistoryReadService
except ImportError:  # Lambda ZIP imports app.py as a top-level module.
    from service import CursorStore, HistoryReadService

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


def lambda_handler(event, _context):
    operation = _operation(event)
    try:
        settings = HistorySettings.from_env()
        settings.validate_reads()
        account_id = jwt_subject(event, settings)
        dynamodb = boto3.resource("dynamodb")
        assert_authoritative_account_active(
            account_id,
            dynamodb.Table(settings.users_table_name),
            dynamodb.Table(settings.deletion_ledger_table_name),
        )
        assert_active_device_binding(
            event, account_id, dynamodb.Table(settings.device_bindings_table_name)
        )
        service = HistoryReadService(
            settings=settings,
            content_table=dynamodb.Table(settings.content_table_name),
            control_table=dynamodb.Table(settings.control_table_name),
            cursor_store=CursorStore.from_aws(settings, dynamodb.Table(settings.control_table_name)),
        )
        route = _route_key(event)
        _no_body(event)
        if route == "GET /v1/users/history":
            query = _query(event, {"cursor", "limit"})
            result = service.list_history(
                account_id,
                cursor=query.get("cursor"),
                requested_limit=query.get("limit"),
            )
        elif route == "GET /v1/users/history/{requestId}":
            _query(event, set())
            result = service.get_history(account_id, _path_request_id(event))
        elif route == "GET /v1/users/history/export":
            query = _query(event, {"cursor", "limit"})
            result = service.export_history(
                account_id, cursor=query.get("cursor"), requested_limit=query.get("limit")
            )
        elif route == "GET /v1/users/progress":
            _query(event, set())
            settings.validate_recognition()
            result = service.get_progress(account_id)
        else:
            raise HistoryError("NOT_FOUND", "The requested route was not found.")
        LOGGER.info("History read completed | operation=%s", route.split(" ", 1)[0])
        _metric(operation, success=True)
        return _response(200, result)
    except HistoryError as err:
        LOGGER.warning("History read rejected | errorCode=%s retryable=%s", err.code, err.retryable)
        _metric(operation, success=False, authorization=err.code in {"UNAUTHORIZED", "FORBIDDEN", "DEVICE_BINDING_REQUIRED", "DEVICE_BINDING_MISMATCH"})
        return _response(err.status_code, _error_body(err))
    except Exception:
        LOGGER.exception("Unexpected History read failure")
        _metric(operation, success=False)
        return _response(500, _error_body(HistoryError("INTERNAL_ERROR", "An internal error occurred.")))


def _route_key(event):
    route = event.get("routeKey")
    if isinstance(route, str):
        return route
    method = (((event.get("requestContext") or {}).get("http") or {}).get("method") or "").upper()
    path = event.get("rawPath") or ""
    if method == "GET" and path == "/v1/users/history/export":
        return "GET /v1/users/history/export"
    if method == "GET" and path.startswith("/v1/users/history/"):
        path = "/v1/users/history/{requestId}"
    return f"{method} {path}"


def _path_request_id(event):
    parameters = event.get("pathParameters") or {}
    value = parameters.get("requestId") if isinstance(parameters, dict) else None
    if not isinstance(value, str) or not value:
        raise HistoryError("INVALID_REQUEST", "A requestId path parameter is required.")
    try:
        return validate_request_id(value)
    except HistoryError as err:
        raise HistoryError("INVALID_REQUEST", "The requestId path parameter is invalid.") from err


def _query(event, allowed):
    query = event.get("queryStringParameters") or {}
    if not isinstance(query, dict) or any(key not in allowed for key in query):
        raise HistoryError("INVALID_REQUEST", "The query parameters do not match the approved contract.")
    return query


def _no_body(event):
    if event.get("body") not in (None, ""):
        raise HistoryError("INVALID_REQUEST", "History read routes do not accept a request body.")


def _operation(event):
    route = _route_key(event)
    return {
        "GET /v1/users/history": "list",
        "GET /v1/users/history/{requestId}": "detail",
        "GET /v1/users/history/export": "export",
        "GET /v1/users/progress": "progress",
    }.get(route, "unknown")


def _metric(operation, *, success, authorization=False):
    import time
    values = {"HistoryReadSuccess" if success else "HistoryReadFailure": 1}
    if authorization:
        values["HistoryAuthorizationFailure"] = 1
    metrics = [{"Name": name, "Unit": "Count"} for name in values]
    LOGGER.info(json.dumps({
        "_aws": {"Timestamp": int(time.time()) * 1000, "CloudWatchMetrics": [{
            "Namespace": "AMT/TrustCheckRadar/History",
            "Dimensions": [["Environment", "Operation"]], "Metrics": metrics,
        }]},
        "Environment": os.environ.get("APP_ENVIRONMENT", "unknown"),
        "Operation": operation,
        **values,
    }, separators=(",", ":")))


def _error_body(err):
    result = {"error": {"code": err.code, "message": err.message, "retryable": err.retryable}}
    if err.details:
        result["error"]["details"] = err.details
    return result


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
        },
        "body": json.dumps(body, separators=(",", ":"), default=_json_default),
    }


def _json_default(value):
    from decimal import Decimal
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else float(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")
