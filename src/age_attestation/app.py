import json
import logging
import os
import uuid
from datetime import UTC, datetime

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

TABLE_NAME = os.environ.get("USERS_TABLE_NAME") or os.environ["TABLE_NAME"]
dynamodb_client = boto3.client("dynamodb")
serializer = TypeSerializer()


def lambda_handler(event, context):
    if _is_cognito_trigger_event(event):
        return _handle_cognito_trigger(event, context)
    return _handle_api_request(event, context)


def _handle_api_request(event, context):
    sub = None
    try:
        payload = _parse_body(event)

        claims = _extract_jwt_claims(event)
        sub = _extract_api_sub(claims, payload)
        over_18_acknowledged = _extract_api_over_18(claims, payload)
        age_policy_version = _extract_api_age_policy_version(claims, payload)

        result = _process_attestation(
            sub=sub,
            over_18_acknowledged=over_18_acknowledged,
            age_policy_version=age_policy_version,
        )

        return _response(200, result)
    except ValueError as err:
        if str(err) == "User profile not found for sub":
            return _response(404, {"message": "No user profile was found for the provided sub"})
        return _response(400, {"message": str(err)})
    except Exception as err:
        return _internal_error_response(
            context=context,
            err=err,
            sub=sub,
        )


def _handle_cognito_trigger(event, context):
    sub = None
    try:
        request = event.get("request") or {}
        attributes = request.get("userAttributes") or {}
        client_metadata = request.get("clientMetadata") or {}
        sub = (
            _optional_safe_identifier(attributes.get("sub"))
            or _optional_safe_identifier(event.get("userName"))
            or _optional_safe_identifier(attributes.get("email"))
        )

        if not sub:
            raise ValueError("sub is required in Cognito attributes")

        over_18_acknowledged = _parse_cognito_boolean(
            attributes.get("custom:over_18")
            or attributes.get("custom:over18Acknowledged")
            or client_metadata.get("over18Acknowledged")
        )
        age_policy_version = (
            _optional_safe_identifier(attributes.get("custom:agePolicyVersion"))
            or _optional_safe_identifier(client_metadata.get("agePolicyVersion"))
            or "v1.0"
        )

        _process_attestation(
            sub=sub,
            over_18_acknowledged=over_18_acknowledged,
            age_policy_version=age_policy_version,
        )

        return event
    except Exception as err:
        LOGGER.exception(
            "Cognito age attestation trigger failed | request_id=%s sub=%s",
            getattr(context, "aws_request_id", None),
            sub,
            exc_info=err,
        )
        raise


def _process_attestation(*, sub: str, over_18_acknowledged: bool, age_policy_version: str):
    timestamp = datetime.now(UTC).isoformat()
    _update_user_attestation(
        sub=sub,
        over_18_acknowledged=over_18_acknowledged,
        attested_at=timestamp,
        age_policy_version=age_policy_version,
    )

    return {
        "sub": sub,
        "eligibleForSignup": over_18_acknowledged,
        "denialReasons": [] if over_18_acknowledged else ["over_18_not_acknowledged"],
        "attestedAt": timestamp,
        "over18Acknowledged": over_18_acknowledged,
        "agePolicyVersion": age_policy_version,
    }


def _parse_body(event):
    body = event.get("body")
    if body is None:
        raise ValueError("Request body is required")

    payload = json.loads(body) if isinstance(body, str) else body
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")

    return payload


def _get_required_str(payload, field_name):
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


def _get_required_bool(payload, field_name):
    value = payload.get(field_name)
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _optional_str(value):
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _extract_jwt_claims(event):
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}

    jwt_claims = (authorizer.get("jwt") or {}).get("claims")
    if isinstance(jwt_claims, dict):
        return jwt_claims

    legacy_claims = authorizer.get("claims")
    if isinstance(legacy_claims, dict):
        return legacy_claims

    return {}


def _extract_api_sub(claims, payload):
    claim_sub = claims.get("sub")
    if claim_sub is not None:
        return _validate_safe_identifier(str(claim_sub).strip(), "sub")
    return _get_required_safe_identifier(payload, "sub")


def _extract_api_over_18(claims, payload):
    claim_value = claims.get("custom:over_18")
    if claim_value is not None:
        return _parse_cognito_boolean(claim_value)
    return _get_required_bool(payload, "over18Acknowledged")


def _extract_api_age_policy_version(claims, payload):
    claim_value = claims.get("custom:agePolicyVersion")
    if isinstance(claim_value, str) and claim_value.strip():
        return _validate_safe_identifier(claim_value.strip(), "agePolicyVersion")
    return _get_optional_safe_identifier(payload, "agePolicyVersion") or "v1.0"


def _get_required_safe_identifier(payload, field_name):
    value = _get_required_str(payload, field_name)
    return _validate_safe_identifier(value, field_name)


def _get_optional_safe_identifier(payload, field_name):
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string when provided")
    return _validate_safe_identifier(value.strip(), field_name)


def _optional_safe_identifier(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return _validate_safe_identifier(value.strip(), "value")


def _validate_safe_identifier(value, field_name):
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:@")
    if len(value) > 128:
        raise ValueError(f"{field_name} is too long")
    if any(char not in allowed for char in value):
        raise ValueError(f"{field_name} contains unsupported characters")
    return value


def _parse_cognito_boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    raise ValueError("over18Acknowledged is required in Cognito attributes or client metadata")


def _extract_source_ip(event):
    request_context = event.get("requestContext") or {}
    identity = request_context.get("identity") or {}
    return identity.get("sourceIp")


def _extract_user_agent(event):
    headers = event.get("headers") or {}
    return headers.get("User-Agent") or headers.get("user-agent")


def _serialize_map(value: dict) -> dict:
    return {k: serializer.serialize(v) for k, v in value.items()}


def _update_user_attestation(
    *,
    sub: str,
    over_18_acknowledged: bool,
    attested_at: str,
    age_policy_version: str,
):
    try:
        dynamodb_client.update_item(
            TableName=TABLE_NAME,
            Key=_serialize_map({"PK": f"USER#{sub}", "SK": "PROFILE"}),
            UpdateExpression=(
                "SET ageVerified = :age_verified, "
                "ageVerifiedAt = :age_verified_at, "
                "agePolicyVersion = :age_policy_version, "
                "updatedAt = :updated_at, "
                "#status = :status"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=_serialize_map(
                {
                    ":age_verified": over_18_acknowledged,
                    ":age_verified_at": attested_at if over_18_acknowledged else None,
                    ":age_policy_version": age_policy_version,
                    ":updated_at": attested_at,
                    ":status": "ACTIVE" if over_18_acknowledged else "PENDING_AGE_GATE",
                }
            ),
            ConditionExpression="attribute_exists(PK) AND attribute_exists(SK)",
            ReturnValues="ALL_NEW",
        )
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise ValueError("User profile not found for sub") from err
        raise


def _is_cognito_trigger_event(event):
    return isinstance(event, dict) and isinstance(event.get("triggerSource"), str)


def _response(status_code: int, body: dict):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _internal_error_response(*, context, err: Exception, sub: str | None):
    error_code = "AGE_ATTESTATION_INTERNAL_ERROR"
    request_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    LOGGER.exception(
        "Age attestation failed | error_code=%s request_id=%s sub=%s",
        error_code,
        request_id,
        sub,
        exc_info=err,
    )
    return _response(
        500,
        {
            "message": "Failed to process age attestation",
            "errorCode": error_code,
            "requestId": request_id,
        },
    )
