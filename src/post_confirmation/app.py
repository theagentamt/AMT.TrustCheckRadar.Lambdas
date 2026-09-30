from datetime import UTC, datetime
import logging
import os

import boto3
from boto3.dynamodb.types import TypeSerializer
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


def _ledger_table_name_from_environment():
    table_name = os.environ.get("DELETION_LEDGER_TABLE_NAME")
    if table_name:
        return table_name
    table_arn = os.environ.get("DELETION_LEDGER_TABLE_ARN")
    if table_arn and "/" in table_arn:
        return table_arn.rsplit("/", 1)[-1]
    raise RuntimeError(
        "DELETION_LEDGER_TABLE_NAME or DELETION_LEDGER_TABLE_ARN is required"
    )


TABLE_NAME = _table_name_from_environment()
DELETION_LEDGER_TABLE_NAME = _ledger_table_name_from_environment()
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)
dynamodb_client = boto3.client("dynamodb")
serializer = TypeSerializer()


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

        try:
            dynamodb_client.transact_write_items(
                TransactItems=[
                    _absent_deletion_fence(sub),
                    {
                        "Put": {
                            "TableName": TABLE_NAME,
                            "Item": _serialize_map(item),
                            "ConditionExpression": (
                                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
                            ),
                        }
                    },
                ]
            )
        except ClientError as err:
            if not _is_proven_conditional_cancellation(err, expected_actions=2):
                raise
            _confirm_safe_duplicate(sub)
        return event
    except (KeyError, TypeError, AttributeError):
        LOGGER.warning("POST_CONFIRMATION_INVALID_EVENT")
        raise RuntimeError("POST_CONFIRMATION_INVALID_EVENT") from None
    except Exception:
        # SDK messages and exception chains can contain identifiers. Lambda also
        # logs uncaught exceptions, so replacing the application log alone is
        # insufficient; only this fixed diagnostic may reach the trigger caller.
        LOGGER.error("POST_CONFIRMATION_FAILED")
        raise RuntimeError("POST_CONFIRMATION_FAILED") from None


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


def _serialize_map(value):
    return {key: serializer.serialize(item) for key, item in value.items()}


def _absent_deletion_fence(sub):
    return {
        "ConditionCheck": {
            "TableName": DELETION_LEDGER_TABLE_NAME,
            "Key": _serialize_map(
                {"PK": f"ACCOUNT#{sub}", "SK": "ACCOUNT_DELETION"}
            ),
            "ConditionExpression": "attribute_not_exists(PK)",
        }
    }


def _confirm_safe_duplicate(sub):
    dynamodb_client.transact_write_items(
        TransactItems=[
            _absent_deletion_fence(sub),
            {
                "ConditionCheck": {
                    "TableName": TABLE_NAME,
                    "Key": _serialize_map({"PK": f"USER#{sub}", "SK": "PROFILE"}),
                    "ExpressionAttributeNames": {"#status": "status", "#sub": "sub"},
                    "ExpressionAttributeValues": _serialize_map(
                        {
                            ":account": sub,
                            ":pending": "PENDING_AGE_GATE",
                            ":active": "ACTIVE",
                        }
                    ),
                    "ConditionExpression": (
                        "#sub = :account AND "
                        "(#status = :pending OR #status = :active)"
                    ),
                }
            },
        ]
    )


def _is_proven_conditional_cancellation(err, *, expected_actions):
    if err.response.get("Error", {}).get("Code") != "TransactionCanceledException":
        return False
    reasons = err.response.get("CancellationReasons")
    return (
        isinstance(reasons, list)
        and len(reasons) == expected_actions
        and all(isinstance(reason, dict) for reason in reasons)
        and any(reason.get("Code") == "ConditionalCheckFailed" for reason in reasons)
        and all(
            reason.get("Code") in {"None", "ConditionalCheckFailed"}
            for reason in reasons
        )
    )
