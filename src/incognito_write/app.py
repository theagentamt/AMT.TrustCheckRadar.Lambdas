import json
import logging
import os
from datetime import UTC, datetime

import boto3

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

TABLE_NAME = os.environ.get("USERS_TABLE_NAME") or os.environ["TABLE_NAME"]
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)


def lambda_handler(event, _context):
    try:
        body = event.get("body")
        payload = json.loads(body) if isinstance(body, str) else body
        if not isinstance(payload, dict):
            return _response(400, {"message": "Request body must be a JSON object"})

        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub.strip():
            return _response(400, {"message": "sub is required"})

        timestamp = datetime.now(UTC).isoformat()
        item = {
            "PK": f"USER#{sub.strip()}",
            "SK": "PROFILE",
            "sub": sub.strip(),
            "createdAt": timestamp,
            "updatedAt": timestamp,
            "status": "PENDING_AGE_GATE",
            "ageVerified": False,
            "ageVerifiedAt": None,
            "agePolicyVersion": "v1.0",
        }

        table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)")
        return _response(201, item)
    except Exception:
        LOGGER.exception("Failed to write user profile")
        return _response(500, {"message": "Failed to store user profile"})


def _response(status_code: int, body: dict):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
