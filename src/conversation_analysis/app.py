"""Retired legacy analysis endpoint: owned replay only, never new dispatch."""
import json
import time
import boto3

from config import ANALYSIS_ABUSE_TABLE_NAME
from errors import AppError
from response_builders import build_error_response
from retired_replay import LegacyReplay
from validation import parse_and_validate_event
from shared_history import HistorySettings, HistoryError
from shared_history.security import jwt_subject


def lambda_handler(event, _context):
    request_id = None
    try:
        settings = HistorySettings.from_env()
        settings.validate_api_auth()
        account = jwt_subject(event, settings)
        payload = parse_and_validate_event(event)
        request_id = payload['requestId']
        response = LegacyReplay(resource=boto3.resource('dynamodb'), settings=settings,
            abuse_table_name=ANALYSIS_ABUSE_TABLE_NAME, now=lambda: int(time.time())).replay(event, account, payload)
        return _response(200, response)
    except (AppError, HistoryError) as err:
        safe = AppError(err.code, err.message, retryable=err.retryable)
        return _response(safe.status_code, build_error_response(request_id=request_id, err=safe))
    except Exception:
        # No raw SDK exception, submitted text, identifiers or tracebacks.
        err = AppError('SERVER_UNAVAILABLE', 'Legacy request evidence is unavailable. No new check was started or charged.', retryable=False)
        return _response(503, build_error_response(request_id=request_id, err=err))


def _response(status_code, body):
    return {'statusCode': status_code, 'headers': {'Content-Type': 'application/json', 'Cache-Control': 'no-store'},
            'body': json.dumps(body, allow_nan=False)}
