import json
import logging
import os
import time

import boto3

import config
from errors import AppError
from service import process_recovery, process_self_recovery
from shared_history import HistoryError
from shared_history.security import assert_authoritative_account_active, jwt_subject
from validation import parse_and_validate_event, parse_and_validate_self_event

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


def lambda_handler(event, _context):
    operator_id = None
    account_id = None
    try:
        route = _route_key(event)
        if route == "POST /v1/users/device-recovery":
            config.validate_self_recovery_config()
            account_id = _self_subject(event)
            payload = parse_and_validate_self_event(event)
            response = process_self_recovery(account_id=account_id, payload=payload)
        elif route == "POST /device-recovery":
            _assert_legacy_route(event)
            operator_id = extract_operator_id(event)
            payload = parse_and_validate_event(event)
            account_id = payload["accountId"]
            response = process_recovery(
                account_id=account_id,
                action=payload["action"],
                binding_fingerprint=payload["bindingFingerprint"],
                operator_id=operator_id,
            )
        else:
            raise AppError("NOT_FOUND", "The requested route was not found.")
        return _response(200, response)
    except AppError as err:
        return _response(err.status_code, _build_error_response(account_id, operator_id, err))
    except Exception:
        LOGGER.exception("Unexpected device recovery error")
        err = AppError("INTERNAL_ERROR", "An internal error occurred while processing the request.", retryable=False)
        return _response(500, _build_error_response(account_id, operator_id, err))


def extract_operator_id(event: dict) -> str:
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}

    if authorizer.get("jwt") or authorizer.get("claims") or authorizer.get("principalId"):
        raise AppError(
            "UNAUTHORIZED", "AWS IAM authorization is required for support recovery.", retryable=False
        )
    iam = authorizer.get("iam")
    user_arn = iam.get("userArn") if isinstance(iam, dict) else None
    if not isinstance(user_arn, str) or not user_arn.startswith(("arn:aws:iam::", "arn:aws:sts::")):
        raise AppError(
            "UNAUTHORIZED", "AWS IAM authorization is required for support recovery.", retryable=False
        )
    try:
        allowed = json.loads(os.environ.get("DEVICE_RECOVERY_ALLOWED_PRINCIPAL_ARNS_JSON", ""))
    except ValueError as err:
        raise AppError("SERVER_UNAVAILABLE", "The support principal policy is invalid.") from err
    if (
        not isinstance(allowed, list) or not allowed
        or any(not isinstance(value, str) or not value.startswith(("arn:aws:iam::", "arn:aws:sts::")) for value in allowed)
    ):
        raise AppError("SERVER_UNAVAILABLE", "The support principal policy is invalid.")
    if user_arn not in allowed:
        raise AppError("FORBIDDEN", "The IAM principal is not allowed to recover devices.")
    return user_arn


def _self_subject(event):
    settings = type("RecoveryAuthSettings", (), {
        "cognito_issuer": config.COGNITO_ISSUER,
        "cognito_app_client_id": config.COGNITO_APP_CLIENT_ID,
        "cognito_required_scope": config.COGNITO_REQUIRED_SCOPE,
    })()
    try:
        subject = jwt_subject(event, settings)
        _assert_recent_reauthentication(event)
        resource = boto3.resource("dynamodb")
        assert_authoritative_account_active(
            subject,
            resource.Table(config.USERS_TABLE_NAME),
            resource.Table(config.DELETION_LEDGER_TABLE_NAME),
        )
        return subject
    except HistoryError as err:
        raise AppError(err.code, err.message, retryable=err.retryable) from err


def _assert_recent_reauthentication(event, *, now=lambda: int(time.time())):
    claims = (((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}).get("claims")
    auth_time = _epoch((claims or {}).get("auth_time"))
    issued_at = _epoch((claims or {}).get("iat"))
    current = now()
    if (
        auth_time is None or issued_at is None
        or auth_time > current + 60 or issued_at > current + 60
        or issued_at < auth_time
        or current - auth_time > config.DEVICE_RECOVERY_MAX_REAUTH_AGE_SECONDS
    ):
        raise AppError(
            "REAUTHENTICATION_REQUIRED",
            "A recent server-verified sign-in is required for device recovery.",
        )


def _epoch(value):
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _route_key(event):
    route = event.get("routeKey")
    if isinstance(route, str):
        return route
    method = (((event.get("requestContext") or {}).get("http") or {}).get("method") or "").upper()
    return f"{method} {event.get('rawPath') or ''}"


def _assert_legacy_route(event):
    route = event.get("routeKey")
    if route is not None and route != "POST /device-recovery":
        raise AppError("NOT_FOUND", "The requested route was not found.")
    query = event.get("queryStringParameters") or {}
    if not isinstance(query, dict) or query:
        raise AppError("INVALID_REQUEST", "Support recovery does not accept query parameters.")


def _build_error_response(account_id: str | None, operator_id: str | None, err: AppError) -> dict:
    body = {
        "error": {
            "code": err.code,
            "message": err.message,
            "retryable": err.retryable,
        },
    }
    if err.details:
        body["error"]["details"] = err.details
    return body


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
