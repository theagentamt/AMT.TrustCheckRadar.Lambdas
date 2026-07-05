import json
import logging
import os

from errors import AppError
from service import get_entitlement_snapshot

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


def lambda_handler(event, _context):
    account_id = None
    try:
        account_id = extract_account_id(event)
        return _response(200, get_entitlement_snapshot(account_id))
    except AppError as err:
        return _response(err.status_code, _build_error_response(account_id, err))
    except Exception:
        LOGGER.exception("Unexpected entitlement snapshot error")
        err = AppError("INTERNAL_ERROR", "An internal error occurred while processing the request.", retryable=False)
        return _response(500, _build_error_response(account_id, err))


def extract_account_id(event: dict) -> str:
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}

    jwt_claims = (authorizer.get("jwt") or {}).get("claims")
    if isinstance(jwt_claims, dict):
        sub = jwt_claims.get("sub")
        if isinstance(sub, str) and sub.strip():
            return sub.strip()

    legacy_claims = authorizer.get("claims")
    if isinstance(legacy_claims, dict):
        sub = legacy_claims.get("sub")
        if isinstance(sub, str) and sub.strip():
            return sub.strip()

    principal_id = authorizer.get("principalId")
    if isinstance(principal_id, str) and principal_id.strip():
        return principal_id.strip()

    raise AppError("UNAUTHORIZED", "A trusted caller identity is required for entitlement snapshot.", retryable=False)


def _build_error_response(account_id: str | None, err: AppError) -> dict:
    body = {
        "accountId": account_id,
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

