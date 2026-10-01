import json
import logging
import time

import config
from errors import AppError
from validation import parse, uuid4

ROUTE = "/v1/users/demographic-research-profile"
LOGGER = logging.getLogger(__name__)


def lambda_handler(event, _context):
    operation = "unknown"
    try:
        # Do not parse a body or import the AWS-backed service while disabled.
        claims = (((event or {}).get("requestContext") or {}).get("authorizer") or {}).get("jwt", {}).get("claims")
        subject = claims.get("sub") if isinstance(claims, dict) and claims.get("token_use") == "access" else None
        if not isinstance(subject, str) or not subject or len(subject) > 128:
            raise AppError("UNAUTHORIZED", "A Cognito access token is required.")
        config.require_service(subject)
        route = _route(event)
        from service import get_profile, update_profile
        if route == f"GET {ROUTE}":
            operation = "get"
            query = event.get("queryStringParameters") or {}
            if not isinstance(query, dict) or not set(query).issubset({"operationId"}):
                raise AppError("INVALID_REQUEST", "The query parameters are invalid.")
            if query.get("operationId") is not None:
                uuid4(query["operationId"])
            result = get_profile(subject, query.get("operationId"))
        elif route == f"PUT {ROUTE}":
            payload = parse(event, config.NOTICE_VERSION)
            operation = payload["action"]
            result = update_profile(subject, payload)
        else:
            raise AppError("NOT_FOUND", "The requested route was not found.")
        metric(operation, "Success")
        return response(200, result)
    except AppError as err:
        metric(operation, "Rejected")
        return response(err.status_code, error(err))
    except Exception:
        metric(operation, "InternalError")
        return response(503, error(AppError("SERVER_UNAVAILABLE", "Demographic research is temporarily unavailable.", retryable=True)))


def _route(event):
    if isinstance(event.get("routeKey"), str):
        return event["routeKey"]
    method = (((event.get("requestContext") or {}).get("http") or {}).get("method") or "").upper()
    return f"{method} {event.get('rawPath') or ''}"


def error(err):
    value = {"error": {"code": err.code, "message": err.message, "retryable": err.retryable}}
    if err.details:
        value["error"]["details"] = err.details
    return value


def response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, separators=(",", ":"))}


def metric(operation, name):
    # CloudWatch Embedded Metric Format; bounded dimensions contain no user data.
    environment = config.ENVIRONMENT if config.ENVIRONMENT in {"dev", "uat", "prod"} else "disabled"
    LOGGER.info(json.dumps({"_aws":{"Timestamp":int(time.time()*1000),"CloudWatchMetrics":[{
        "Namespace":config.METRICS_NAMESPACE,"Dimensions":[["Environment","Operation"]],
        "Metrics":[{"Name":name,"Unit":"Count"}]}]},
        "Environment":environment,"Operation":operation if operation in {"get","enroll","update","withdraw"} else "unknown",name:1},
        separators=(",", ":")))
