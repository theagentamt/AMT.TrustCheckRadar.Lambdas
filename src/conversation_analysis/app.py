import json
import logging
import os

from abuse_controls import extract_identity
from device_binding import assert_active_device_binding
from errors import AppError
from response_builders import build_error_response
from service import handle_analysis_request
from validation import parse_and_validate_event

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


def lambda_handler(event, _context):
    request_id = None
    try:
        payload = parse_and_validate_event(event)
        request_id = payload["requestId"]
        identity = extract_identity(event)
        assert_active_device_binding(event, identity)
        return _response(200, handle_analysis_request(payload, identity))
    except AppError as err:
        return _response(err.status_code, build_error_response(request_id=request_id, err=err))
    except Exception:
        LOGGER.exception("Unexpected conversation analysis error")
        internal_error = AppError("INTERNAL_ERROR", "An internal error occurred while processing the request.", retryable=False)
        return _response(500, build_error_response(request_id=request_id, err=internal_error))


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
