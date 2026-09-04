from datetime import UTC, datetime
import logging
import os

import boto3
from botocore.exceptions import ClientError

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


def _table_name_from_environment():
    table_name = os.environ.get("USERS_TABLE_NAME") or os.environ.get("TABLE_NAME")
    if table_name:
        return table_name

    table_arn = os.environ.get("USERS_TABLE_ARN")
    if table_arn and "/" in table_arn:
        return table_arn.rsplit("/", 1)[-1]

    raise RuntimeError("USERS_TABLE_NAME, TABLE_NAME, or USERS_TABLE_ARN is required")


TABLE_NAME = _table_name_from_environment()
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)


def lambda_handler(event, _context):
    try:
        attrs = event["request"]["userAttributes"]
        sub = _get_required_attribute(attrs, "sub")
        now = datetime.now(UTC).isoformat()

        item = {
            "PK": f"USER#{sub}",
            "SK": "PROFILE",
            "sub": sub,
            "email": _optional_attribute(attrs, "email"),
            "given_name": _optional_attribute(attrs, "given_name"),
            "family_name": _optional_attribute(attrs, "family_name"),
            "phone_number": _optional_attribute(attrs, "phone_number"),
            "over_18": _optional_attribute(attrs, "custom:over_18"),
            "status": "PENDING_AGE_GATE",
            "ageVerified": False,
            "ageVerifiedAt": None,
            "agePolicyVersion": "v1.0",
            "createdAt": now,
            "updatedAt": now,
        }

        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
        return event
    except KeyError as err:
        LOGGER.warning("Post confirmation event missing required attribute: %s", err)
        raise
    except ClientError:
        LOGGER.exception("Failed to create user profile during post confirmation")
        raise


def _get_required_attribute(attributes, key):
    value = attributes.get(key)
    if not isinstance(value, str) or not value.strip():
        raise KeyError(key)
    return value.strip()


def _optional_attribute(attributes, key):
    value = attributes.get(key)
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None
