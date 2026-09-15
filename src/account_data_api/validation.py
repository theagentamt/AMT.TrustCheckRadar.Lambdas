import json
from uuid import UUID

from errors import AppError


def deletion_request(event):
    _no_query(event)
    raw = event.get("body")
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as err:
        raise _invalid("body", "Request body must be valid JSON.") from err
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "operationId", "action"}:
        raise _invalid("body", "Request fields do not match the account-deletion contract.")
    if value.get("schemaVersion") != 1 or value.get("action") != "DELETE_ACCOUNT":
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
    if event.get("body") not in (None, ""):
        raise _invalid("body", "The status route does not accept a request body.")


def _no_query(event):
    query = event.get("queryStringParameters") or {}
    if not isinstance(query, dict) or query:
        raise _invalid("query", "Account-deletion routes do not accept query parameters.")


def _invalid(field, issue):
    return AppError(
        "INVALID_REQUEST", "The submitted request is invalid.",
        details=[{"field": field, "issue": issue}],
    )
