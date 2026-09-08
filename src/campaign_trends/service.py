from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time

from taxonomy import label

QUERY_FIELDS = {
    "locale",
    "limit",
    "nextToken",
    "categoryId",
    "tacticId",
    "channelId",
    "riskBand",
    "languageId",
    "trendDirection",
    "fromWeek",
    "toWeek",
}
WEEK = re.compile(r"^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$")
STABLE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
COUNT_BANDS = {"10-24", "25-49", "50-99", "100-249", "250+"}
RISK_BANDS = {"low", "medium", "high", "unknown"}
TREND_DIRECTIONS = {"new", "rising", "stable", "falling"}


class TrendsError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message); self.status, self.code, self.message = status, code, message


def list_trends(event, *, environment, table_name, index_name, maximum_page_size,
                token_ttl, token_secret, dynamodb, now_epoch=None):
    _authenticated(event)
    query = event.get("queryStringParameters") or {}
    if not isinstance(query, dict) or set(query) - QUERY_FIELDS:
        raise TrendsError(400, "INVALID_REQUEST", "Query parameters are invalid")
    locale = query.get("locale", "en")
    if not isinstance(locale, str) or locale not in {"en", "es"}:
        raise TrendsError(400, "INVALID_REQUEST", "locale must be en or es")
    try: limit = int(query.get("limit", maximum_page_size))
    except (TypeError, ValueError) as err: raise TrendsError(400, "INVALID_REQUEST", "limit is invalid") from err
    if not 1 <= limit <= maximum_page_size: raise TrendsError(400, "INVALID_REQUEST", "limit is invalid")
    _validate_filters(query)
    token_scope = _query_scope(query)
    start = decode_token(
        query.get("nextToken"),
        environment=environment,
        secret=token_secret,
        scope=token_scope,
        now_epoch=now_epoch,
    ) if query.get("nextToken") else None
    from_week, to_week = query.get("fromWeek"), query.get("toWeek")
    if (from_week is not None and (not isinstance(from_week, str) or not WEEK.fullmatch(from_week))) \
            or (to_week is not None and (not isinstance(to_week, str) or not WEEK.fullmatch(to_week))) \
            or (from_week and to_week and from_week > to_week):
        raise TrendsError(400, "INVALID_REQUEST", "Week filters are invalid")
    values = {":published": {"S": "STATE#PUBLISHED"}}
    expression = "GSI1PK = :published"
    if from_week or to_week:
        values[":from"] = {"S": f"{from_week or '0000-W01'}#"}
        values[":to"] = {"S": f"{to_week or '9999-W53'}#\uffff"}
        expression += " AND GSI1SK BETWEEN :from AND :to"
    kwargs = {"TableName": table_name, "IndexName": index_name, "KeyConditionExpression": expression,
              "ExpressionAttributeValues": values, "ScanIndexForward": False, "Limit": limit}
    if start: kwargs["ExclusiveStartKey"] = start
    response = dynamodb.query(**kwargs)
    trends = []
    for raw in response.get("Items", []):
        item = deserialize(raw)
        if item.get("contributorCountBand") not in COUNT_BANDS or item.get("submissionCountBand") not in COUNT_BANDS:
            continue
        dimensions = _thresholded_dimensions(item)
        if query.get("categoryId") and item.get("categoryId") != query["categoryId"]: continue
        if query.get("riskBand") and item.get("riskBand") != query["riskBand"]: continue
        if query.get("trendDirection") and item.get("trendDirection") != query["trendDirection"]: continue
        if query.get("languageId") and query["languageId"] not in dimensions["languageIds"]: continue
        if query.get("tacticId") and query["tacticId"] not in dimensions["tacticIds"]: continue
        if query.get("channelId") and query["channelId"] not in dimensions["channelIds"]: continue
        trends.append({"campaignId": item["campaignId"], "categoryId": item["categoryId"],
            "categoryLabel": label(item["categoryId"], locale), "periodWeek": item["periodWeek"],
            "riskBand": item["riskBand"], "contributorCountBand": item["contributorCountBand"],
            "submissionCountBand": item["submissionCountBand"], **dimensions,
            "summaryKey": item["summaryKey"], "trendDirection": item["trendDirection"]})
    next_token = encode_token(
        response.get("LastEvaluatedKey"),
        environment=environment,
        secret=token_secret,
        scope=token_scope,
        ttl=token_ttl,
        now_epoch=now_epoch,
    ) if response.get("LastEvaluatedKey") else None
    return {"schemaVersion": 1, "locale": locale, "trends": trends, "nextToken": next_token}


def encode_token(key, *, environment, secret, ttl, scope="", now_epoch=None):
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    payload = {"environment": environment, "expiresAt": now_epoch + ttl, "key": key, "scope": scope}
    encoded_payload = _base64url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signature = hmac.new(_secret_bytes(secret), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded_payload}.{_base64url(signature)}"


def decode_token(token, *, environment, secret, scope="", now_epoch=None):
    try:
        encoded_payload, encoded_signature = token.split(".")
        supplied_signature = _base64url_decode(encoded_signature)
        expected_signature = hmac.new(
            _secret_bytes(secret),
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("signature mismatch")
        payload = json.loads(_base64url_decode(encoded_payload).decode("utf-8"))
    except Exception as err: raise TrendsError(400, "INVALID_PAGINATION_TOKEN", "Pagination token is invalid") from err
    if not isinstance(payload, dict) or set(payload) != {"environment", "expiresAt", "key", "scope"} \
            or payload["environment"] != environment or payload["scope"] != scope \
            or not isinstance(payload["key"], dict):
        raise TrendsError(400, "INVALID_PAGINATION_TOKEN", "Pagination token is invalid")
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    if not isinstance(payload["expiresAt"], int) or payload["expiresAt"] <= now_epoch:
        raise TrendsError(400, "EXPIRED_PAGINATION_TOKEN", "Pagination token has expired")
    _validate_start_key(payload["key"])
    return payload["key"]


def _validate_filters(query):
    for name in ("categoryId", "tacticId", "channelId", "languageId"):
        value = query.get(name)
        if value is not None and (not isinstance(value, str) or not STABLE_ID.fullmatch(value)):
            raise TrendsError(400, "INVALID_REQUEST", f"{name} is invalid")
    if query.get("riskBand") is not None and (
        not isinstance(query["riskBand"], str) or query["riskBand"] not in RISK_BANDS
    ):
        raise TrendsError(400, "INVALID_REQUEST", "riskBand is invalid")
    if query.get("trendDirection") is not None and (
        not isinstance(query["trendDirection"], str)
        or query["trendDirection"] not in TREND_DIRECTIONS
    ):
        raise TrendsError(400, "INVALID_REQUEST", "trendDirection is invalid")


def _query_scope(query):
    filters = {key: value for key, value in query.items() if key != "nextToken"}
    encoded = json.dumps(filters, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _thresholded_dimensions(item):
    if item.get("dimensionSchemaVersion") != 1:
        return {"languageIds": [], "tacticIds": [], "channelIds": []}
    return {
        name: _stable_id_list(item.get(name))
        for name in ("languageIds", "tacticIds", "channelIds")
    }


def _stable_id_list(value):
    if not isinstance(value, list) or len(value) > 32:
        return []
    if any(not isinstance(item, str) or not STABLE_ID.fullmatch(item) for item in value):
        return []
    return sorted(set(value))


def _secret_bytes(secret):
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError("pagination token secret is invalid")
    return secret


def _base64url(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value):
    if not isinstance(value, str) or not value:
        raise ValueError("invalid base64url value")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def _validate_start_key(key):
    required = {"PK", "SK", "GSI1PK", "GSI1SK"}
    if set(key) != required or any(not isinstance(key[field], dict) or set(key[field]) != {"S"}
                                   for field in required):
        raise TrendsError(400, "INVALID_PAGINATION_TOKEN", "Pagination token is invalid")
    if not key["PK"]["S"].startswith("CAMPAIGN#") or key["SK"]["S"] != "AGGREGATE" \
            or key["GSI1PK"]["S"] != "STATE#PUBLISHED":
        raise TrendsError(400, "INVALID_PAGINATION_TOKEN", "Pagination token is invalid")


def _authenticated(event):
    claims = (((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}).get("claims") or {}
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise TrendsError(401, "UNAUTHORIZED", "Authentication is required")


def deserialize(item):
    def one(v):
        kind, raw = next(iter(v.items()))
        if kind == "S": return raw
        if kind == "N": return float(raw) if "." in raw else int(raw)
        if kind == "L": return [one(x) for x in raw]
    return {k: one(v) for k, v in item.items()}
