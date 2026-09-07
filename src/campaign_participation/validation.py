import base64
import json
from uuid import UUID

from errors import AppError

MAX_BODY_BYTES = 4096
REQUEST_FIELDS = {"schemaVersion", "action", "noticeVersion", "operationId"}


def parse_request(event: dict, *, notice_version: str) -> dict:
    body = event.get("body")
    if not isinstance(body, (str, dict)):
        raise _invalid("body", "A JSON request body is required.")
    if isinstance(body, str):
        try:
            raw = base64.b64decode(body, validate=True) if event.get("isBase64Encoded") is True else body.encode("utf-8")
        except (ValueError, UnicodeError) as err:
            raise _invalid("body", "Request body encoding is invalid.") from err
        if len(raw) > MAX_BODY_BYTES:
            raise _invalid("body", "Request body is too large.")
        try:
            payload = json.loads(raw, object_pairs_hook=_object_without_duplicates)
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            raise _invalid("body", "Request body must be valid JSON.") from err
    else:
        payload = body
        try:
            if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_BODY_BYTES:
                raise _invalid("body", "Request body is too large.")
        except (TypeError, ValueError, UnicodeError) as err:
            raise _invalid("body", "Request body must be valid JSON.") from err

    if not isinstance(payload, dict) or set(payload) != REQUEST_FIELDS:
        raise _invalid("body", "Request body fields do not match the V1 contract.")
    if isinstance(payload["schemaVersion"], bool) or payload["schemaVersion"] != 1:
        raise _invalid("schemaVersion", "schemaVersion must be integer 1.")
    if not isinstance(payload["action"], str) or payload["action"] not in {"join", "withdraw"}:
        raise _invalid("action", "action must be join or withdraw.")
    if not isinstance(payload["noticeVersion"], str) or payload["noticeVersion"] != notice_version:
        raise _invalid("noticeVersion", "noticeVersion does not match the current notice.")
    _require_uuid4(payload["operationId"])
    return dict(payload)


def _object_without_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise _invalid("body", "Request body contains duplicate fields.")
        value[key] = item
    return value


def _require_uuid4(value) -> None:
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError) as err:
        raise _invalid("operationId", "operationId must be a canonical UUIDv4.") from err
    if parsed.version != 4 or str(parsed) != value:
        raise _invalid("operationId", "operationId must be a canonical UUIDv4.")


def _invalid(field: str, issue: str) -> AppError:
    return AppError(
        "INVALID_REQUEST",
        "The submitted request is invalid.",
        details=[{"field": field, "issue": issue}],
    )
