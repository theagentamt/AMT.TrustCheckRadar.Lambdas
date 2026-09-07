import json
import logging
import os

import config
from errors import AppError
from service import get_participation, update_participation
from validation import parse_request

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


def lambda_handler(event, _context):
    try:
        config.validate_config()
        identity = _jwt_subject(event)
        route = _route_key(event)
        if route == "GET /v1/users/campaign-participation":
            result = get_participation(identity)
        elif route == "PUT /v1/users/campaign-participation":
            result = update_participation(identity, parse_request(event, notice_version=config.NOTICE_VERSION))
        else:
            raise AppError("NOT_FOUND", "The requested route was not found.")
        LOGGER.info("Campaign participation request completed | route=%s state=%s", route.split(" ", 1)[0], result["state"])
        return _response(200, result)
    except AppError as err:
        LOGGER.warning("Campaign participation request rejected | errorCode=%s retryable=%s", err.code, err.retryable)
        return _response(err.status_code, _error_body(err))
    except Exception:
        LOGGER.exception("Unexpected campaign participation error")
        return _response(500, _error_body(AppError("INTERNAL_ERROR", "An internal error occurred.")))


def _jwt_subject(event: dict) -> str:
    claims = (((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}).get("claims")
    subject = claims.get("sub") if isinstance(claims, dict) else None
    if not isinstance(subject, str) or not subject.strip():
        raise AppError("UNAUTHORIZED", "A Cognito JWT subject is required.")
    return subject.strip()


def _route_key(event: dict) -> str:
    route = event.get("routeKey")
    if isinstance(route, str):
        return route
    method = (((event.get("requestContext") or {}).get("http") or {}).get("method") or "").upper()
    path = event.get("rawPath") or ""
    return f"{method} {path}"


def _error_body(err: AppError) -> dict:
    result = {"error": {"code": err.code, "message": err.message, "retryable": err.retryable}}
    if err.details:
        result["error"]["details"] = err.details
    return result


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, separators=(",", ":")),
    }
