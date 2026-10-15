"""Scoped Dev list/detail for seven-day content-free governed results."""
import json
import os

try:
    from .service import GovernedHistoryError, Service, Settings
    from .security import authorize
except ImportError:
    from service import GovernedHistoryError, Service, Settings
    from security import authorize


def response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(body, separators=(",", ":"))}


def lambda_handler(event, _context):
    operation = "unknown"
    try:
        if type(event) is not dict or event.get("version") != "2.0" or event.get("isBase64Encoded") is True:
            raise GovernedHistoryError("INVALID_REQUEST", 422)
        route = event.get("routeKey")
        if route == "GET /v1/users/analysis-history":
            operation = "list"; gate = "GOVERNED_HISTORY_LIST_ENABLED"
        elif route == "GET /v1/users/analysis-history/{resultId}":
            operation = "detail"; gate = "GOVERNED_HISTORY_DETAIL_ENABLED"
        else:
            raise GovernedHistoryError("NOT_FOUND", 404)
        if (os.environ.get("STAGE") != "dev" or os.environ.get("AUTHORITY_ENABLED") != "true"
                or os.environ.get(gate) != "true"):
            raise GovernedHistoryError("SERVICE_NOT_ENABLED", 503)
        http = (event.get("requestContext") or {}).get("http") or {}
        if http.get("method") != "GET" or event.get("body") not in (None, ""):
            raise GovernedHistoryError("INVALID_REQUEST", 422)
        query = event.get("queryStringParameters") or {}
        if type(query) is not dict or (operation == "list" and set(query) - {"cursor", "limit"}) or (operation == "detail" and query):
            raise GovernedHistoryError("INVALID_REQUEST", 422)
        from shared_check_authority.engineering import require_engineering_subject
        require_engineering_subject(event)
        from shared_check_authority.runtime import load_authority
        # This read-only surface starts with the same minimized inventory view
        # enforced by its IAM policy. Mutation adapters retain the full-row
        # verifier by default.
        authority = load_authority(projected_inventory=True)
        identity = authorize(authority, event)
        account, fingerprint, device_version, inventory_revision = identity
        service = Service(authority, Settings.from_env(), account=account, fingerprint=fingerprint,
                          device_version=device_version, inventory_revision=inventory_revision)
        if operation == "list":
            result = service.list(cursor=query.get("cursor"), requested_limit=query.get("limit"))
        else:
            params = event.get("pathParameters") or {}
            if type(params) is not dict or set(params) != {"resultId"}:
                raise GovernedHistoryError("INVALID_REQUEST", 422)
            result = service.detail(params["resultId"])
        # Recheck authoritative fences after eventual-index and canonical reads.
        refreshed = authorize(authority, event)
        if refreshed[:3] != identity[:3]:
            from shared_check_authority.core import AuthorityError
            raise AuthorityError("ACTIVE_DEVICE_REQUIRED")
        if refreshed[3] != identity[3]:
            from shared_check_authority.core import AuthorityError
            raise AuthorityError("KEY_INVENTORY_UNAVAILABLE")
        print(json.dumps({"event": "governed_history_read", "operation": operation, "statusCode": 200}, separators=(",", ":")))
        return response(200, result)
    except GovernedHistoryError as error:
        return response(error.status, {"error": {"code": error.code, "retryable": error.status >= 500}})
    except Exception as error:
        from shared_check_authority.core import AuthorityError
        code = error.code if isinstance(error, AuthorityError) else "SERVICE_UNAVAILABLE"
        status = {"AUTHENTICATION_REQUIRED": 401, "ENGINEERING_ACCESS_UNAVAILABLE": 403,
                  "ACCOUNT_UNAVAILABLE": 403, "ACTIVE_DEVICE_REQUIRED": 403}.get(code, 503)
        return response(status, {"error": {"code": code if status != 503 else "SERVICE_UNAVAILABLE",
                                           "retryable": status >= 500}})
