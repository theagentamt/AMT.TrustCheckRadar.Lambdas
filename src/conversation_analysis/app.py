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
        LOGGER.info("Stage started: request_received")
        payload = parse_and_validate_event(event)
        request_id = payload["requestId"]
        LOGGER.info("Stage completed: request_validated | requestId=%s sourceType=%s entityCount=%s", request_id, payload["sourceType"], len(payload["entities"]))

        LOGGER.info("Stage started: identity_extraction | requestId=%s", request_id)
        identity = extract_identity(event)
        LOGGER.info("Stage completed: identity_extracted | requestId=%s", request_id)

        LOGGER.info("Stage started: device_binding_validation | requestId=%s", request_id)
        assert_active_device_binding(event, identity)
        LOGGER.info("Stage completed: device_binding_validated | requestId=%s", request_id)

        LOGGER.info("Stage started: analysis_flow | requestId=%s", request_id)
        response_body = handle_analysis_request(payload, identity)
        LOGGER.info("Stage completed: analysis_flow | requestId=%s riskLevel=%s scamScore=%s", request_id, response_body.get("riskLevel"), response_body.get("scamScore"))
        return _response(200, response_body)
    except AppError as err:
        LOGGER.warning(
            "Stage failed: handled_error | requestId=%s errorCode=%s retryable=%s message=%s details=%s",
            request_id,
            err.code,
            err.retryable,
            err.message,
            err.details,
        )
        return _response(err.status_code, build_error_response(request_id=request_id, err=err))
    except Exception:
        LOGGER.exception("Stage failed: unhandled_error | requestId=%s", request_id)
        internal_error = AppError("INTERNAL_ERROR", "An internal error occurred while processing the request.", retryable=False)
        return _response(500, build_error_response(request_id=request_id, err=internal_error))


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
