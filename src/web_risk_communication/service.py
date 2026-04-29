import time

from cache import get_or_evaluate, hash_value
from config import DOMAIN_TTL_SECONDS, FULL_URL_TTL_SECONDS
from scoring import build_critical_decision, build_decision, highest_confidence, is_critical_fast_fail
from validation import normalize_url
from webrisk_client import call_google_web_risk


def evaluate_url(url: str) -> dict:
    normalized_url, domain, domain_uri = normalize_url(url)
    full_hash = hash_value(normalized_url)
    domain_hash = hash_value(domain)
    now = int(time.time())

    full_result, full_cache = get_or_evaluate(
        cache_scope="FULL_URL",
        cache_hash=full_hash,
        lookup_value=normalized_url,
        ttl_map=FULL_URL_TTL_SECONDS,
        now=now,
        evaluate_fn=call_google_web_risk,
        highest_confidence_fn=highest_confidence,
    )

    if is_critical_fast_fail(full_result):
        return {"decision": build_critical_decision(full_result)}

    domain_result, domain_cache = get_or_evaluate(
        cache_scope="DOMAIN",
        cache_hash=domain_hash,
        lookup_value=domain_uri,
        ttl_map=DOMAIN_TTL_SECONDS,
        now=now,
        evaluate_fn=call_google_web_risk,
        highest_confidence_fn=highest_confidence,
    )

    return {
        "decision": build_decision(full_result, domain_result),
        "cache": {
            "fullUrl": full_cache,
            "domain": domain_cache,
        },
    }
