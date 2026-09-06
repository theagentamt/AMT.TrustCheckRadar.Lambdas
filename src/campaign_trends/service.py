from __future__ import annotations

import base64
import json
import re
import time

from taxonomy import label

QUERY_FIELDS = {"locale", "limit", "nextToken", "categoryId", "riskBand", "languageId", "fromWeek", "toWeek"}
WEEK = re.compile(r"^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$")
COUNT_BANDS = {"10-24", "25-49", "50-99", "100-249", "250+"}


class TrendsError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message); self.status, self.code, self.message = status, code, message


def list_trends(event, *, environment, table_name, index_name, maximum_page_size,
                token_ttl, dynamodb, now_epoch=None):
    _authenticated(event)
    query = event.get("queryStringParameters") or {}
    if not isinstance(query, dict) or set(query) - QUERY_FIELDS:
        raise TrendsError(400, "INVALID_REQUEST", "Query parameters are invalid")
    locale = query.get("locale", "en")
    if locale not in {"en", "es"}: raise TrendsError(400, "INVALID_REQUEST", "locale must be en or es")
    try: limit = int(query.get("limit", maximum_page_size))
    except (TypeError, ValueError) as err: raise TrendsError(400, "INVALID_REQUEST", "limit is invalid") from err
    if not 1 <= limit <= maximum_page_size: raise TrendsError(400, "INVALID_REQUEST", "limit is invalid")
    start = decode_token(query.get("nextToken"), environment=environment, now_epoch=now_epoch) if query.get("nextToken") else None
    from_week, to_week = query.get("fromWeek"), query.get("toWeek")
    if (from_week and not WEEK.fullmatch(from_week)) or (to_week and not WEEK.fullmatch(to_week)) or (from_week and to_week and from_week > to_week):
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
        if query.get("categoryId") and item.get("categoryId") != query["categoryId"]: continue
        if query.get("riskBand") and item.get("riskBand") != query["riskBand"]: continue
        if query.get("languageId") and query["languageId"] not in item.get("languageIds", []): continue
        trends.append({"campaignId": item["campaignId"], "categoryId": item["categoryId"],
            "categoryLabel": label(item["categoryId"], locale), "periodWeek": item["periodWeek"],
            "riskBand": item["riskBand"], "contributorCountBand": item["contributorCountBand"],
            "submissionCountBand": item["submissionCountBand"], "languageIds": item.get("languageIds", []),
            "summaryKey": item["summaryKey"], "trendDirection": item["trendDirection"]})
    next_token = encode_token(response.get("LastEvaluatedKey"), environment=environment,
                              ttl=token_ttl, now_epoch=now_epoch) if response.get("LastEvaluatedKey") else None
    return {"schemaVersion": 1, "locale": locale, "trends": trends, "nextToken": next_token}


def encode_token(key, *, environment, ttl, now_epoch=None):
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    payload = {"environment": environment, "expiresAt": now_epoch + ttl, "key": key}
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_token(token, *, environment, now_epoch=None):
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
    except Exception as err: raise TrendsError(400, "INVALID_PAGINATION_TOKEN", "Pagination token is invalid") from err
    if not isinstance(payload, dict) or set(payload) != {"environment", "expiresAt", "key"} \
            or payload["environment"] != environment or not isinstance(payload["key"], dict):
        raise TrendsError(400, "INVALID_PAGINATION_TOKEN", "Pagination token is invalid")
    now_epoch = int(time.time()) if now_epoch is None else now_epoch
    if not isinstance(payload["expiresAt"], int) or payload["expiresAt"] <= now_epoch:
        raise TrendsError(400, "EXPIRED_PAGINATION_TOKEN", "Pagination token has expired")
    _validate_start_key(payload["key"])
    return payload["key"]


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
