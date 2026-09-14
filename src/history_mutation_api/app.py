import json
import logging
import os

import boto3

from shared_history import HistoryError, HistorySettings
from shared_history.contracts import validate_request_id
from shared_history.security import assert_active_device_binding, jwt_subject
from service import HistoryMutationService

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


def lambda_handler(event, _context):
    operation = _operation(event)
    try:
        settings = HistorySettings.from_env()
        settings.validate_mutations()
        account_id = jwt_subject(event)
        resource = boto3.resource("dynamodb")
        assert_active_device_binding(
            event, account_id, resource.Table(settings.device_bindings_table_name)
        )
        service = HistoryMutationService(
            settings=settings,
            control_table=resource.Table(settings.control_table_name),
            dynamodb_client=boto3.client("dynamodb"),
        )
        route = _route_key(event)
        body = _body(event)
        if route == "DELETE /v1/users/history/{requestId}":
            result = service.delete_one(account_id, _request_id(event), body["operationId"])
        elif route == "DELETE /v1/users/history":
            result = service.clear_history(account_id, body["operationId"])
        elif route == "POST /v1/users/progress/reset":
            result = service.reset_progress(account_id, body["operationId"])
        else:
            raise HistoryError("NOT_FOUND", "The requested route was not found.")
        LOGGER.info("History mutation completed | operation=%s", result["operation"])
        _metric(operation, success=True)
        return _response(200, result)
    except HistoryError as err:
        LOGGER.warning("History mutation rejected | errorCode=%s retryable=%s", err.code, err.retryable)
        _metric(operation, success=False, authorization=err.code in {"UNAUTHORIZED", "FORBIDDEN", "DEVICE_BINDING_REQUIRED", "DEVICE_BINDING_MISMATCH"})
        return _response(err.status_code, _error_body(err))
    except Exception:
        LOGGER.exception("Unexpected History mutation failure")
        _metric(operation, success=False)
        return _response(500, _error_body(HistoryError("INTERNAL_ERROR", "An internal error occurred.")))


def _body(event):
    raw = event.get("body")
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as err:
        raise HistoryError("INVALID_REQUEST", "The mutation body must be valid JSON.") from err
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "operationId"} or value.get("schemaVersion") != 1:
        raise HistoryError("INVALID_REQUEST", "The mutation body does not match the approved contract.")
    import uuid
    try:
        parsed = uuid.UUID(value.get("operationId"))
    except (TypeError, ValueError, AttributeError) as err:
        raise HistoryError("INVALID_REQUEST", "operationId must be a canonical UUIDv4.") from err
    if parsed.version != 4 or str(parsed) != value["operationId"]:
        raise HistoryError("INVALID_REQUEST", "operationId must be a canonical UUIDv4.")
    try:
        return validate_request_id(value)
    except HistoryError as err:
        raise HistoryError("INVALID_REQUEST", "The requestId path parameter is invalid.") from err


def _route_key(event):
    route = event.get("routeKey")
    if isinstance(route, str):
        return route
    method = (((event.get("requestContext") or {}).get("http") or {}).get("method") or "").upper()
    path = event.get("rawPath") or ""
    if method == "DELETE" and path.startswith("/v1/users/history/"):
        path = "/v1/users/history/{requestId}"
    return f"{method} {path}"


def _request_id(event):
    value = (event.get("pathParameters") or {}).get("requestId")
    if not isinstance(value, str) or not value:
        raise HistoryError("INVALID_REQUEST", "A requestId path parameter is required.")
    return value


def _operation(event):
    return {
        "DELETE /v1/users/history/{requestId}": "delete-one",
        "DELETE /v1/users/history": "clear",
        "POST /v1/users/progress/reset": "reset",
    }.get(_route_key(event), "unknown")


def _metric(operation, *, success, authorization=False):
    import time
    values = {"HistoryMutationSuccess" if success else "HistoryMutationFailure": 1}
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


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "private, no-store", "Pragma": "no-cache"},
        "body": json.dumps(body, separators=(",", ":")),
    }
