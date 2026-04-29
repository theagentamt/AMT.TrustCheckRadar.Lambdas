import hashlib
import time
from datetime import UTC, datetime
from typing import Any

import boto3

from config import TABLE_NAME

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)


def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def get_or_evaluate(*, cache_scope: str, cache_hash: str, lookup_value: str, ttl_map: dict[str, int], now: int, evaluate_fn, highest_confidence_fn) -> tuple[dict[str, Any], dict[str, Any]]:
    cache_item = get_cache_item(cache_scope, cache_hash)
    if cache_item and int(cache_item.get("expiresAt", 0)) > now:
        return deserialize_cache(cache_item), {"source": "CACHE_HIT", "expiresAt": int(cache_item["expiresAt"])}

    evaluation = evaluate_fn(lookup_value)
    expires_at = now + ttl_map[highest_confidence_fn(evaluation["threats"])]
    put_cache_item(
        cache_scope=cache_scope,
        cache_hash=cache_hash,
        lookup_value=lookup_value,
        threats=evaluation["threats"],
        expires_at=expires_at,
        highest_confidence_fn=highest_confidence_fn,
    )
    return evaluation, {"source": "GOOGLE_API", "expiresAt": expires_at}


def get_cache_item(cache_scope: str, cache_hash: str) -> dict[str, Any] | None:
    response = table.get_item(Key={"PK": f"WEBRISK#{cache_scope}#{cache_hash}", "SK": "RESULT"})
    return response.get("Item")


def deserialize_cache(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "uri": item.get("uri"),
        "threats": item.get("threats", []),
    }


def put_cache_item(*, cache_scope: str, cache_hash: str, lookup_value: str, threats: list[dict[str, str]], expires_at: int, highest_confidence_fn):
    now = int(time.time())
    item = {
        "PK": f"WEBRISK#{cache_scope}#{cache_hash}",
        "SK": "RESULT",
        "uri": lookup_value,
        "threats": threats,
        "expiresAt": expires_at,
        "ttl": expires_at,
        "updatedAt": datetime.now(UTC).isoformat(),
        "cacheScope": cache_scope,
        "highestConfidence": highest_confidence_fn(threats),
        "threatCount": len(threats),
        "cachedAtEpoch": now,
    }
    table.put_item(Item=item)
