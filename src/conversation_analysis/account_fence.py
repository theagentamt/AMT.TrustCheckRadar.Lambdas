from decimal import Decimal

import boto3

from config import DELETION_LEDGER_TABLE_NAME, USERS_TABLE_NAME
from errors import AppError


dynamodb = boto3.resource("dynamodb")


def account_authority_checks(account_id: str) -> list[dict]:
    if not USERS_TABLE_NAME or not DELETION_LEDGER_TABLE_NAME:
        raise AppError(
            "SERVER_UNAVAILABLE",
            "The account authority is unavailable.",
            retryable=False,
        )
    return [
        {"ConditionCheck": {
            "TableName": USERS_TABLE_NAME,
            "Key": serialize_item({"PK": f"USER#{account_id}", "SK": "PROFILE"}),
            "ConditionExpression": (
                "#status = :active AND ageVerified = :true AND #sub = :account_id"
            ),
            "ExpressionAttributeNames": {"#status": "status", "#sub": "sub"},
            "ExpressionAttributeValues": serialize_item({
                ":active": "ACTIVE", ":true": True, ":account_id": account_id,
            }),
        }},
        {"ConditionCheck": {
            "TableName": DELETION_LEDGER_TABLE_NAME,
            "Key": serialize_item({
                "PK": f"ACCOUNT#{account_id}", "SK": "ACCOUNT_DELETION",
            }),
            "ConditionExpression": "attribute_not_exists(PK)",
        }},
    ]


def append_account_authority_checks(transaction: list[dict], account_id: str) -> None:
    for check in account_authority_checks(account_id):
        if check not in transaction:
            transaction.append(check)


def assert_account_active(account_id: str) -> None:
    if not USERS_TABLE_NAME or not DELETION_LEDGER_TABLE_NAME:
        raise AppError(
            "SERVER_UNAVAILABLE",
            "The account authority is unavailable.",
            retryable=False,
        )
    profile = dynamodb.Table(USERS_TABLE_NAME).get_item(
        Key={"PK": f"USER#{account_id}", "SK": "PROFILE"},
        ConsistentRead=True,
    ).get("Item")
    deletion = dynamodb.Table(DELETION_LEDGER_TABLE_NAME).get_item(
        Key={"PK": f"ACCOUNT#{account_id}", "SK": "ACCOUNT_DELETION"},
        ConsistentRead=True,
    ).get("Item")
    if (
        not profile
        or profile.get("sub") != account_id
        or profile.get("status") != "ACTIVE"
        or profile.get("ageVerified") is not True
        or deletion is not None
    ):
        raise AppError("FORBIDDEN", "The account is not active.", retryable=False)


def serialize_item(value: dict) -> dict:
    return {key: _serialize_value(item) for key, item in value.items()}


def _serialize_value(value):
    if value is None:
        return {"NULL": True}
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, (int, float, Decimal)):
        return {"N": str(value)}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, list):
        return {"L": [_serialize_value(item) for item in value]}
    if isinstance(value, dict):
        return {"M": serialize_item(value)}
    raise TypeError(f"Unsupported DynamoDB value type: {type(value).__name__}")
