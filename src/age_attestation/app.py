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

TABLE_NAME = os.environ["TABLE_NAME"]
dynamodb_client = boto3.client("dynamodb")
serializer = TypeSerializer()


def lambda_handler(event, context):
    if _is_cognito_trigger_event(event):
        return _handle_cognito_trigger(event, context)
    return _handle_api_request(event, context)


def _handle_api_request(event, context):
    tenant_id = None
    subject_id = None
    try:
        payload = _parse_body(event)

        tenant_id = _get_required_str(payload, "tenantId")
        subject_id = _get_required_str(payload, "subjectId")
        over_18_acknowledged = _get_required_bool(payload, "over18Acknowledged")

        result = _process_attestation(
            tenant_id=tenant_id,
            subject_id=subject_id,
            over_18_acknowledged=over_18_acknowledged,
            source="API",
            metadata={
                "countryCode": payload.get("countryCode"),
                "consentVersion": payload.get("consentVersion"),
                "sourceIp": _extract_source_ip(event),
                "userAgent": _extract_user_agent(event),
            },
        )

        return _response(200, result)
    except ValueError as err:
        if str(err) == "User item not found for subjectId":
            return _response(404, {"message": str(err)})
        if str(err) == "Attestation item already exists":
            return _response(409, {"message": str(err)})
        return _response(400, {"message": str(err)})
    except Exception as err:
        return _internal_error_response(
            context=context,
            err=err,
            tenant_id=tenant_id,
            subject_id=subject_id,
        )


def _handle_cognito_trigger(event, context):
    tenant_id = None
    subject_id = None
    try:
        request = event.get("request") or {}
        attributes = request.get("userAttributes") or {}
        client_metadata = request.get("clientMetadata") or {}

        tenant_id = (
            _optional_str(attributes.get("custom:tenantId"))
            or _optional_str(client_metadata.get("tenantId"))
        )
        subject_id = (
            _optional_str(attributes.get("sub"))
            or _optional_str(event.get("userName"))
            or _optional_str(attributes.get("email"))
        )

        if not tenant_id:
            raise ValueError("tenantId is required in Cognito attributes or client metadata")
        if not subject_id:
            raise ValueError("subjectId is required in Cognito attributes")

        over_18_acknowledged = _parse_cognito_boolean(
            attributes.get("custom:over18Acknowledged")
            or client_metadata.get("over18Acknowledged")
        )

        _process_attestation(
            tenant_id=tenant_id,
            subject_id=subject_id,
            over_18_acknowledged=over_18_acknowledged,
            source="COGNITO_TRIGGER",
            metadata={
                "triggerSource": event.get("triggerSource"),
                "userPoolId": event.get("userPoolId"),
                "clientId": event.get("callerContext", {}).get("clientId"),
            },
        )

        return event
    except Exception as err:
        LOGGER.exception(
            "Cognito age attestation trigger failed | request_id=%s tenant_id=%s subject_id=%s",
            getattr(context, "aws_request_id", None),
            tenant_id,
            subject_id,
            exc_info=err,
        )
        raise


def _process_attestation(*, tenant_id: str, subject_id: str, over_18_acknowledged: bool, source: str, metadata: dict):
    eligible_for_signup = bool(over_18_acknowledged)
    denial_reasons = []

    if not over_18_acknowledged:
        denial_reasons.append("over_18_not_acknowledged")

    attestation_id = str(uuid.uuid4())
    timestamp = datetime.now(UTC).isoformat()

    item = {
        "pk": f"TENANT#{tenant_id}",
        "sk": f"AGE_ATTESTATION#{subject_id}#{timestamp}",
        "entityType": "AGE_ATTESTATION",
        "id": attestation_id,
        "tenantId": tenant_id,
        "subjectId": subject_id,
        "over18Acknowledged": over_18_acknowledged,
        "eligibleForSignup": eligible_for_signup,
        "denialReasons": denial_reasons,
        "attestedAt": timestamp,
        "source": source,
    }

    for key, value in metadata.items():
        if isinstance(value, str) and value.strip():
            item[key] = value.strip()

    _write_attestation_and_update_user(
        tenant_id=tenant_id,
        subject_id=subject_id,
        over_18_acknowledged=over_18_acknowledged,
        eligible_for_signup=eligible_for_signup,
        denial_reasons=denial_reasons,
        attested_at=timestamp,
        attestation_id=attestation_id,
        attestation_item=item,
    )

    return {
        "attestationId": attestation_id,
        "tenantId": tenant_id,
        "subjectId": subject_id,
        "eligibleForSignup": eligible_for_signup,
        "denialReasons": denial_reasons,
        "attestedAt": timestamp,
        "over18Acknowledged": over_18_acknowledged,
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


def _write_attestation_and_update_user(
    *,
    tenant_id: str,
    subject_id: str,
    over_18_acknowledged: bool,
    eligible_for_signup: bool,
    denial_reasons: list[str],
    attested_at: str,
    attestation_id: str,
    attestation_item: dict,
):
    try:
        dynamodb_client.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": TABLE_NAME,
                        "Key": _serialize_map({"pk": f"TENANT#{tenant_id}", "sk": f"USER#{subject_id}"}),
                        "UpdateExpression": (
                            "SET ageOver18Acknowledged = :ack, "
                            "ageVerificationEligibleForSignup = :eligible, "
                            "ageVerificationDeniedReasons = :reasons, "
                            "ageVerifiedAt = :verified_at, "
                            "ageLastAttestationId = :attestation_id"
                        ),
                        "ExpressionAttributeValues": _serialize_map(
                            {
                                ":ack": over_18_acknowledged,
                                ":eligible": eligible_for_signup,
                                ":reasons": denial_reasons,
                                ":verified_at": attested_at,
                                ":attestation_id": attestation_id,
                            }
                        ),
                        "ConditionExpression": "attribute_exists(pk) AND attribute_exists(sk)",
                    }
                },
                {
                    "Put": {
                        "TableName": TABLE_NAME,
                        "Item": _serialize_map(attestation_item),
                        "ConditionExpression": "attribute_not_exists(pk) AND attribute_not_exists(sk)",
                    }
                },
            ]
        )
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "TransactionCanceledException":
            reasons = err.response.get("CancellationReasons") or []
            if len(reasons) > 0 and reasons[0].get("Code") == "ConditionalCheckFailed":
                raise ValueError("User item not found for subjectId") from err
            if len(reasons) > 1 and reasons[1].get("Code") == "ConditionalCheckFailed":
                raise ValueError("Attestation item already exists") from err
        raise


def _is_cognito_trigger_event(event):
    return isinstance(event, dict) and isinstance(event.get("triggerSource"), str)


def _response(status_code: int, body: dict):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _internal_error_response(*, context, err: Exception, tenant_id: str | None, subject_id: str | None):
    error_code = "AGE_ATTESTATION_INTERNAL_ERROR"
    request_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    LOGGER.exception(
        "Age attestation failed | error_code=%s request_id=%s tenant_id=%s subject_id=%s",
        error_code,
        request_id,
        tenant_id,
        subject_id,
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
