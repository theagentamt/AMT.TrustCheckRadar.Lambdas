import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

try:
    import phonenumbers
    from phonenumbers import PhoneNumberType
except ImportError:  # pragma: no cover - exercised by the package smoke test
    phonenumbers = None
    PhoneNumberType = None


LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

SCHEMA_VERSION = 1
AGE_POLICY_VERSION = "v1.0"
ATTESTATION_STATUS = "ACKNOWLEDGED"
RECEIPT_TTL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_ALLOWED_REGION_CODES = ("US", "PR", "VI", "GU", "AS", "MP")
ROUTE_KEY = "POST /v1/users/age-attestation"
PROFILE_STATUS_ACTIVE = "ACTIVE"
PROFILE_STATUS_PENDING = "PENDING_AGE_GATE"
RECEIPT_PREFIX = "AGE_ATTESTATION#"

TABLE_NAME = os.environ.get("USERS_TABLE_NAME") or os.environ["TABLE_NAME"]
DELETION_LEDGER_TABLE_NAME = os.environ.get("DELETION_LEDGER_TABLE_NAME", "")
USER_POOL_ID = os.environ.get("AGE_ATTESTATION_USER_POOL_ID", "")
ALLOWED_REGION_CODES = frozenset(
    value.strip().upper()
    for value in os.environ.get(
        "AGE_ATTESTATION_ALLOWED_REGION_CODES",
        ",".join(DEFAULT_ALLOWED_REGION_CODES),
    ).split(",")
    if value.strip()
)

if not ALLOWED_REGION_CODES or any(
    re.fullmatch(r"[A-Z]{2}", value) is None for value in ALLOWED_REGION_CODES
):
    raise RuntimeError("AGE_ATTESTATION_ALLOWED_REGION_CODES is invalid")

dynamodb_client = boto3.client("dynamodb")
cognito_client = boto3.client("cognito-idp")
serializer = TypeSerializer()
deserializer = TypeDeserializer()


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int, *, retryable: bool):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


class LibPhoneNumberMetadata:
    """Thin, injectable boundary around Google's libphonenumber metadata."""

    def classify(self, value: str) -> tuple[str, str]:
        if phonenumbers is None or PhoneNumberType is None:
            raise RuntimeError("PHONE_METADATA_UNAVAILABLE")
        try:
            parsed = phonenumbers.parse(value, None)
        except phonenumbers.NumberParseException as err:
            raise AppError(
                "PHONE_NUMBER_UNSUPPORTED",
                "The verified phone number is not supported for account eligibility.",
                403,
                retryable=False,
            ) from err
        if not phonenumbers.is_valid_number(parsed):
            raise AppError(
                "PHONE_NUMBER_UNSUPPORTED",
                "The verified phone number is not supported for account eligibility.",
                403,
                retryable=False,
            )
        region_code = phonenumbers.region_code_for_number(parsed)
        number_type = phonenumbers.number_type(parsed)
        allowed_types = {
            PhoneNumberType.FIXED_LINE,
            PhoneNumberType.MOBILE,
            PhoneNumberType.FIXED_LINE_OR_MOBILE,
            PhoneNumberType.VOIP,
        }
        if not isinstance(region_code, str) or number_type not in allowed_types:
            raise AppError(
                "PHONE_NUMBER_UNSUPPORTED",
                "The verified phone number is not supported for account eligibility.",
                403,
                retryable=False,
            )
        return region_code.upper(), str(number_type)


phone_metadata = LibPhoneNumberMetadata()


def lambda_handler(event, context):
    request_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    try:
        claims = _require_http_api_v2_claims(event)
        payload = _parse_request(event)
        subject = _require_subject(claims)
        _require_cognito_eligibility(subject)
        result = _process_attestation(subject, payload)
        return _response(200, result)
    except AppError as err:
        return _error_response(err, request_id)
    except Exception:
        LOGGER.error("AGE_ATTESTATION_SERVICE_UNAVAILABLE")
        return _error_response(
            AppError(
                "SERVICE_UNAVAILABLE",
                "Age attestation is temporarily unavailable.",
                503,
                retryable=True,
            ),
            request_id,
        )


def _require_http_api_v2_claims(event: dict) -> dict:
    if not isinstance(event, dict) or event.get("version") != "2.0":
        raise _authentication_required()
    request_context = event.get("requestContext")
    if not isinstance(request_context, dict):
        raise _authentication_required()
    http = request_context.get("http")
    if (
        not isinstance(http, dict)
        or http.get("method") != "POST"
        or event.get("routeKey") != ROUTE_KEY
    ):
        raise _authentication_required()
    authorizer = request_context.get("authorizer")
    jwt = authorizer.get("jwt") if isinstance(authorizer, dict) else None
    claims = jwt.get("claims") if isinstance(jwt, dict) else None
    if not isinstance(claims, dict) or claims.get("token_use") != "access":
        raise _authentication_required()
    return claims


def _require_subject(claims: dict) -> str:
    subject = claims.get("sub")
    if (
        not isinstance(subject, str)
        or not subject
        or len(subject) > 128
        or re.fullmatch(r"[A-Za-z0-9._:@-]+", subject) is None
    ):
        raise _authentication_required()
    return subject


def _parse_request(event: dict) -> dict:
    if event.get("isBase64Encoded") is True:
        raise _invalid_request("The request body must be plain JSON.")
    body = event.get("body")
    if not isinstance(body, str):
        raise _invalid_request("A JSON request body is required.")
    try:
        payload = json.loads(body, object_pairs_hook=_strict_json_object)
    except (TypeError, ValueError, json.JSONDecodeError) as err:
        raise _invalid_request("The request body must be valid JSON.") from err
    expected = {
        "schemaVersion",
        "operationId",
        "over18Acknowledged",
        "agePolicyVersion",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise _invalid_request("The request fields do not match the age-attestation contract.")
    if type(payload["schemaVersion"]) is not int or payload["schemaVersion"] != SCHEMA_VERSION:
        raise _invalid_request("schemaVersion must be 1.")
    operation_id = payload["operationId"]
    try:
        parsed_operation = UUID(operation_id)
    except (TypeError, ValueError, AttributeError) as err:
        raise _invalid_request("operationId must be a canonical UUIDv4.") from err
    if parsed_operation.version != 4 or str(parsed_operation) != operation_id:
        raise _invalid_request("operationId must be a canonical UUIDv4.")
    if payload["over18Acknowledged"] is not True:
        raise _invalid_request("over18Acknowledged must be true for the V1 attestation.")
    if payload["agePolicyVersion"] != AGE_POLICY_VERSION:
        raise _invalid_request("agePolicyVersion is not supported.")
    return payload


def _strict_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _require_cognito_eligibility(subject: str):
    if not USER_POOL_ID:
        raise RuntimeError("AGE_ATTESTATION_USER_POOL_ID is required")
    try:
        response = cognito_client.admin_get_user(
            UserPoolId=USER_POOL_ID,
            Username=subject,
        )
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code")
        if code in {"UserNotFoundException", "NotAuthorizedException"}:
            raise _authentication_required() from None
        if code in {"TooManyRequestsException", "LimitExceededException"}:
            raise AppError(
                "RATE_LIMITED",
                "Age attestation is temporarily rate limited.",
                429,
                retryable=True,
            ) from None
        raise RuntimeError("COGNITO_ELIGIBILITY_UNAVAILABLE") from None

    raw_attributes = response.get("UserAttributes")
    if not isinstance(raw_attributes, list):
        raise RuntimeError("COGNITO_ATTRIBUTES_INVALID")
    attributes = {}
    for item in raw_attributes:
        if not isinstance(item, dict):
            raise RuntimeError("COGNITO_ATTRIBUTES_INVALID")
        name = item.get("Name")
        value = item.get("Value")
        if not isinstance(name, str) or not isinstance(value, str) or name in attributes:
            raise RuntimeError("COGNITO_ATTRIBUTES_INVALID")
        attributes[name] = value

    if attributes.get("sub") != subject:
        raise _authentication_required()
    if attributes.get("phone_number_verified", "").strip().lower() != "true":
        raise AppError(
            "PHONE_NOT_VERIFIED",
            "A verified phone number is required for account eligibility.",
            403,
            retryable=False,
        )
    phone_number = attributes.get("phone_number")
    if not isinstance(phone_number, str) or not phone_number.strip():
        raise AppError(
            "PHONE_NOT_VERIFIED",
            "A verified phone number is required for account eligibility.",
            403,
            retryable=False,
        )
    region_code, _number_type = phone_metadata.classify(phone_number.strip())
    if region_code not in ALLOWED_REGION_CODES:
        raise AppError(
            "PHONE_REGION_NOT_ALLOWED",
            "The verified phone number is outside the supported account regions.",
            403,
            retryable=False,
        )


def _process_attestation(subject: str, payload: dict) -> dict:
    request_hash = _request_hash(payload)
    operation_id = payload["operationId"]
    now_epoch = int(time.time())

    existing_receipt = _get_item(_user_pk(subject), _receipt_sk(operation_id))
    if existing_receipt is not None:
        return _replay_receipt(
            subject,
            operation_id,
            request_hash,
            existing_receipt,
            now_epoch,
        )

    for _attempt in range(3):
        profile = _get_item(_user_pk(subject), "PROFILE")
        _require_mutable_profile(profile, subject)
        if _is_current_attestation(profile):
            attested_at = profile["ageVerifiedAt"]
            update_profile = False
        else:
            attested_at = datetime.now(UTC).isoformat()
            update_profile = True

        result = _success_body(operation_id, attested_at, replayed=False)
        receipt = _receipt_item(
            subject=subject,
            payload=payload,
            request_hash=request_hash,
            result=result,
            now_epoch=now_epoch,
        )
        try:
            _write_new_attestation(
                subject=subject,
                profile=profile,
                receipt=receipt,
                result=result,
                update_profile=update_profile,
            )
            return result
        except ClientError as err:
            if not _is_proven_conditional_cancellation(err, expected_actions=3):
                raise RuntimeError("ATTESTATION_TRANSACTION_UNAVAILABLE") from None
            raced_receipt = _get_item(_user_pk(subject), _receipt_sk(operation_id))
            if raced_receipt is not None:
                return _replay_receipt(
                    subject,
                    operation_id,
                    request_hash,
                    raced_receipt,
                    now_epoch,
                )

    raise AppError(
        "ACCOUNT_STATE_CONFLICT",
        "The account state changed while age attestation was being recorded.",
        409,
        retryable=True,
    )


def _write_new_attestation(*, subject, profile, receipt, result, update_profile):
    profile_names, profile_values, profile_condition = _profile_snapshot_condition(
        profile,
        subject,
    )
    if update_profile:
        profile_values.update(
            _serialize_map(
                {
                    ":age_acknowledged": True,
                    ":attested_at": result["attestedAt"],
                    ":policy_version": AGE_POLICY_VERSION,
                    ":active": PROFILE_STATUS_ACTIVE,
                }
            )
        )
        profile_action = {
            "Update": {
                "TableName": TABLE_NAME,
                "Key": _serialize_map({"PK": _user_pk(subject), "SK": "PROFILE"}),
                "UpdateExpression": (
                    "SET ageVerified = :age_acknowledged, "
                    "ageVerifiedAt = :attested_at, "
                    "agePolicyVersion = :policy_version, "
                    "updatedAt = :attested_at, #status = :active"
                ),
                "ExpressionAttributeNames": profile_names,
                "ExpressionAttributeValues": profile_values,
                "ConditionExpression": profile_condition,
            }
        }
    else:
        profile_action = {
            "ConditionCheck": {
                "TableName": TABLE_NAME,
                "Key": _serialize_map({"PK": _user_pk(subject), "SK": "PROFILE"}),
                "ExpressionAttributeNames": profile_names,
                "ExpressionAttributeValues": profile_values,
                "ConditionExpression": profile_condition,
            }
        }

    dynamodb_client.transact_write_items(
        TransactItems=[
            _absent_deletion_fence(subject),
            profile_action,
            {
                "Put": {
                    "TableName": TABLE_NAME,
                    "Item": _serialize_map(receipt),
                    "ConditionExpression": (
                        "attribute_not_exists(PK) AND attribute_not_exists(SK)"
                    ),
                }
            },
        ]
    )


def _replay_receipt(subject, operation_id, request_hash, receipt, now_epoch):
    if not _valid_receipt(receipt, subject, operation_id):
        raise RuntimeError("AGE_ATTESTATION_RECEIPT_INVALID")
    if receipt["requestHash"] != request_hash or receipt["expiresAt"] < now_epoch:
        raise AppError(
            "IDEMPOTENCY_CONFLICT",
            "operationId is not available for this age-attestation request.",
            409,
            retryable=False,
        )
    result = dict(receipt["result"])
    try:
        dynamodb_client.transact_write_items(
            TransactItems=[
                _absent_deletion_fence(subject),
                {
                    "ConditionCheck": {
                        "TableName": TABLE_NAME,
                        "Key": _serialize_map(
                            {"PK": _user_pk(subject), "SK": "PROFILE"}
                        ),
                        "ExpressionAttributeNames": {"#status": "status", "#sub": "sub"},
                        "ExpressionAttributeValues": _serialize_map(
                            {
                                ":account": subject,
                                ":active": PROFILE_STATUS_ACTIVE,
                                ":true": True,
                                ":policy": AGE_POLICY_VERSION,
                                ":attested_at": result["attestedAt"],
                            }
                        ),
                        "ConditionExpression": (
                            "#sub = :account AND #status = :active AND "
                            "ageVerified = :true AND agePolicyVersion = :policy AND "
                            "ageVerifiedAt = :attested_at"
                        ),
                    }
                },
                {
                    "ConditionCheck": {
                        "TableName": TABLE_NAME,
                        "Key": _serialize_map(
                            {
                                "PK": _user_pk(subject),
                                "SK": _receipt_sk(operation_id),
                            }
                        ),
                        "ExpressionAttributeValues": _serialize_map(
                            {":hash": request_hash, ":now": now_epoch}
                        ),
                        "ConditionExpression": (
                            "requestHash = :hash AND expiresAt >= :now"
                        ),
                    }
                },
            ]
        )
    except ClientError as err:
        if _is_proven_conditional_cancellation(err, expected_actions=3):
            raise AppError(
                "ACCOUNT_STATE_CONFLICT",
                "The account is no longer eligible for this attestation replay.",
                409,
                retryable=False,
            ) from None
        raise RuntimeError("ATTESTATION_REPLAY_UNAVAILABLE") from None
    result["replayed"] = True
    return result


def _require_mutable_profile(profile, subject):
    if profile is None:
        raise AppError(
            "PROFILE_NOT_FOUND",
            "The account profile is not available for age attestation.",
            404,
            retryable=False,
        )
    if profile.get("sub") != subject or profile.get("status") not in {
        PROFILE_STATUS_ACTIVE,
        PROFILE_STATUS_PENDING,
    }:
        raise AppError(
            "ACCOUNT_STATE_CONFLICT",
            "The account state does not allow age attestation.",
            409,
            retryable=False,
        )


def _is_current_attestation(profile: dict) -> bool:
    return (
        profile.get("status") == PROFILE_STATUS_ACTIVE
        and profile.get("ageVerified") is True
        and profile.get("agePolicyVersion") == AGE_POLICY_VERSION
        and isinstance(profile.get("ageVerifiedAt"), str)
        and bool(profile["ageVerifiedAt"])
    )


def _profile_snapshot_condition(profile, subject):
    names = {"#status": "status", "#sub": "sub"}
    values = _serialize_map(
        {":account": subject, ":snapshot_status": profile["status"]}
    )
    conditions = ["#sub = :account", "#status = :snapshot_status"]
    for index, field_name in enumerate(
        ("updatedAt", "ageVerified", "ageVerifiedAt", "agePolicyVersion")
    ):
        name_key = f"#snapshot_{index}"
        names[name_key] = field_name
        if field_name in profile:
            value_key = f":snapshot_{index}"
            values[value_key] = serializer.serialize(profile[field_name])
            conditions.append(f"{name_key} = {value_key}")
        else:
            conditions.append(f"attribute_not_exists({name_key})")
    return names, values, " AND ".join(conditions)


def _receipt_item(*, subject, payload, request_hash, result, now_epoch):
    return {
        "PK": _user_pk(subject),
        "SK": _receipt_sk(payload["operationId"]),
        "recordType": "AGE_ATTESTATION_RECEIPT",
        "schemaVersion": SCHEMA_VERSION,
        "operationId": payload["operationId"],
        "requestHash": request_hash,
        "result": result,
        "createdAtEpoch": now_epoch,
        "expiresAt": now_epoch + RECEIPT_TTL_SECONDS,
    }


def _valid_receipt(receipt, subject, operation_id):
    return (
        set(receipt)
        == {
            "PK",
            "SK",
            "recordType",
            "schemaVersion",
            "operationId",
            "requestHash",
            "result",
            "createdAtEpoch",
            "expiresAt",
        }
        and receipt.get("PK") == _user_pk(subject)
        and receipt.get("SK") == _receipt_sk(operation_id)
        and receipt.get("recordType") == "AGE_ATTESTATION_RECEIPT"
        and receipt.get("schemaVersion") == SCHEMA_VERSION
        and receipt.get("operationId") == operation_id
        and isinstance(receipt.get("requestHash"), str)
        and isinstance(receipt.get("createdAtEpoch"), int)
        and not isinstance(receipt.get("createdAtEpoch"), bool)
        and isinstance(receipt.get("expiresAt"), int)
        and not isinstance(receipt.get("expiresAt"), bool)
        and _valid_stored_result(receipt.get("result"), operation_id)
    )


def _valid_stored_result(result, operation_id):
    return (
        isinstance(result, dict)
        and set(result)
        == {
            "schemaVersion",
            "operationId",
            "attestationStatus",
            "eligibleForSignup",
            "denialReasons",
            "attestedAt",
            "agePolicyVersion",
            "replayed",
        }
        and result.get("schemaVersion") == SCHEMA_VERSION
        and result.get("operationId") == operation_id
        and result.get("attestationStatus") == ATTESTATION_STATUS
        and result.get("eligibleForSignup") is True
        and result.get("denialReasons") == []
        and isinstance(result.get("attestedAt"), str)
        and bool(result["attestedAt"])
        and result.get("agePolicyVersion") == AGE_POLICY_VERSION
        and result.get("replayed") is False
    )


def _success_body(operation_id: str, attested_at: str, *, replayed: bool):
    return {
        "schemaVersion": SCHEMA_VERSION,
        "operationId": operation_id,
        "attestationStatus": ATTESTATION_STATUS,
        "eligibleForSignup": True,
        "denialReasons": [],
        "attestedAt": attested_at,
        "agePolicyVersion": AGE_POLICY_VERSION,
        "replayed": replayed,
    }


def _request_hash(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _get_item(partition_key: str, sort_key: str):
    try:
        response = dynamodb_client.get_item(
            TableName=TABLE_NAME,
            Key=_serialize_map({"PK": partition_key, "SK": sort_key}),
            ConsistentRead=True,
        )
    except ClientError as err:
        raise RuntimeError("ATTESTATION_READ_UNAVAILABLE") from err
    item = response.get("Item")
    if item is None:
        return None
    if not isinstance(item, dict):
        raise RuntimeError("ATTESTATION_ITEM_INVALID")
    return {
        key: _normalize_dynamodb_value(deserializer.deserialize(value))
        for key, value in item.items()
    }


def _normalize_dynamodb_value(value):
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise RuntimeError("ATTESTATION_NUMBER_INVALID")
        return int(value)
    if isinstance(value, list):
        return [_normalize_dynamodb_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_dynamodb_value(item) for key, item in value.items()}
    return value


def _absent_deletion_fence(subject: str):
    if not DELETION_LEDGER_TABLE_NAME:
        raise RuntimeError("DELETION_LEDGER_TABLE_NAME is required")
    return {
        "ConditionCheck": {
            "TableName": DELETION_LEDGER_TABLE_NAME,
            "Key": _serialize_map(
                {"PK": f"ACCOUNT#{subject}", "SK": "ACCOUNT_DELETION"}
            ),
            "ConditionExpression": "attribute_not_exists(PK)",
        }
    }


def _is_proven_conditional_cancellation(err: ClientError, *, expected_actions: int):
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


def _user_pk(subject: str):
    return f"USER#{subject}"


def _receipt_sk(operation_id: str):
    return f"{RECEIPT_PREFIX}{operation_id}"


def _serialize_map(value: dict) -> dict:
    return {key: serializer.serialize(item) for key, item in value.items()}


def _authentication_required():
    return AppError(
        "AUTHENTICATION_REQUIRED",
        "A current Cognito access token is required.",
        401,
        retryable=False,
    )


def _invalid_request(message: str):
    return AppError("INVALID_REQUEST", message, 400, retryable=False)


def _response(status_code: int, body: dict, *, retry_after: str | None = None):
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
    }
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return {
        "statusCode": status_code,
        "headers": headers,
        "body": json.dumps(body, ensure_ascii=True, separators=(",", ":")),
    }


def _error_response(err: AppError, request_id: str):
    return _response(
        err.status_code,
        {
            "schemaVersion": SCHEMA_VERSION,
            "error": {
                "code": err.code,
                "message": err.message,
                "retryable": err.retryable,
            },
            "requestId": request_id,
        },
        retry_after="1" if err.code == "RATE_LIMITED" else None,
    )
