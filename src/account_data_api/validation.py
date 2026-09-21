import json
from uuid import UUID

from errors import AppError


MAX_REQUEST_BYTES = 1024


def deletion_request(event):
    _no_query(event)
    raw = event.get("body")
    if event.get("isBase64Encoded") is not None and event.get("isBase64Encoded") is not False:
        raise _invalid("body", "Request body must be unencoded JSON.")
    if not isinstance(raw, str):
        raise _invalid("body", "Request body must be valid JSON.")
    try:
        if len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise ValueError("oversized")
        value = json.loads(raw, object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
    except (TypeError, ValueError, UnicodeError, RecursionError) as err:
        raise _invalid("body", "Request body must be bounded valid JSON.") from err
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "operationId", "action"}:
        raise _invalid("body", "Request fields do not match the account-deletion contract.")
    if type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1 or value.get("action") != "DELETE_ACCOUNT":
        raise _invalid("body", "Request fields do not match the account-deletion contract.")
    try:
        operation = UUID(value.get("operationId"))
    except (TypeError, ValueError, AttributeError) as err:
        raise _invalid("operationId", "operationId must be a canonical UUIDv4.") from err
    if operation.version != 4 or str(operation) != value["operationId"]:
        raise _invalid("operationId", "operationId must be a canonical UUIDv4.")
    return value


def validate_get(event):
    _no_query(event)
    if event.get("body") not in (None, "") or (event.get("isBase64Encoded") is not None and event.get("isBase64Encoded") is not False):
        raise _invalid("body", "The status route does not accept a request body.")


def _no_query(event):
    query = event.get("queryStringParameters")
    if (query is not None and (not isinstance(query, dict) or query)) or event.get("rawQueryString") not in (None, ""):
        raise _invalid("query", "Account-deletion routes do not accept query parameters.")


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON field")
        value[key] = item
    return value


def _reject_constant(_value):
    raise ValueError("non-JSON constant")


def _invalid(field, issue):
    return AppError(
        "INVALID_REQUEST", "The submitted request is invalid.",
        details=[{"field": field, "issue": issue}],
    )
