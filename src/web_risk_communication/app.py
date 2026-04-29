import logging
import os
import json
from typing import Any

from service import evaluate_url
from validation import extract_url

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


def lambda_handler(event, _context):
    try:
        url = extract_url(event)
        return _response(200, evaluate_url(url))
    except ValueError as err:
        return _response(400, {"message": str(err)})
    except Exception:
        LOGGER.exception("Failed to evaluate web risk")
        return _response(500, {"message": "Failed to evaluate web risk"})


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
