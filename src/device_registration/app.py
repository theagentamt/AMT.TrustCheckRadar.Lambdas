import json
import logging
import os

import boto3

import config
from errors import AppError
from service import register_device
from shared_history import HistoryError
from shared_history.security import assert_authoritative_account_active, jwt_subject
from validation import parse_and_validate_event

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


def lambda_handler(event, _context):
    account_id = None
    try:
        payload = parse_and_validate_event(event)
        account_id = extract_account_id(event)
        response = register_device(
            account_id=account_id,
            binding_fingerprint=payload["bindingFingerprint"],
            platform=payload["platform"],
            os_version=payload["osVersion"],
        )
        return _response(200, response)
    except AppError as err:
        return _response(err.status_code, _build_error_response(account_id, err))
    except Exception:
        LOGGER.exception("Unexpected device registration error")
        err = AppError("INTERNAL_ERROR", "An internal error occurred while processing the request.", retryable=False)
        return _response(500, _build_error_response(account_id, err))


def extract_account_id(event: dict) -> str:
    settings = type("RegistrationAuthSettings", (), {
        "cognito_issuer": config.COGNITO_ISSUER,
        "cognito_app_client_id": config.COGNITO_APP_CLIENT_ID,
        "cognito_required_scope": config.COGNITO_REQUIRED_SCOPE,
    })()
    if not config.USERS_TABLE_NAME or not config.DELETION_LEDGER_TABLE_NAME:
        raise AppError("SERVER_UNAVAILABLE", "The account authority is unavailable.", retryable=False)
    try:
        account_id = jwt_subject(event, settings)
        resource = boto3.resource("dynamodb")
        assert_authoritative_account_active(
            account_id,
            resource.Table(config.USERS_TABLE_NAME),
            resource.Table(config.DELETION_LEDGER_TABLE_NAME),
        )
        return account_id
    except HistoryError as err:
        raise AppError(err.code, err.message, retryable=err.retryable) from err


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
