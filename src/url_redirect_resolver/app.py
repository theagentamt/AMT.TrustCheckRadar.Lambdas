"""Direct synchronous IAM invocation only; no API Gateway envelope."""

import json
import re
import time

from resolver import Deadline, resolve, resolve_step
from transport import Transport


def lambda_handler(event, context):
    started = time.monotonic()
    check_id = None
    result = {
        "schemaVersion": 1, "resolutionStatus": "invalid_input", "hops": [],
        "lastObservedUrl": None, "httpChainComplete": False, "warnings": [],
        "reasonCodes": ["INVALID_REQUEST"], "requestCount": 0,
        "scope": "HTTP_REDIRECTS_ONLY",
    }
    valid = (
        isinstance(event, dict) and set(event) == {"schemaVersion", "checkId", "url"}
        and type(event.get("schemaVersion")) is int and event["schemaVersion"] == 1
        and isinstance(event.get("checkId"), str)
        and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", event["checkId"]) is not None
        and isinstance(event.get("url"), str)
    )
    single = (isinstance(event, dict) and set(event) == {"schemaVersion", "checkId", "url", "executionBudgetMs"}
              and type(event.get("schemaVersion")) is int and event["schemaVersion"] == 2
              and isinstance(event.get("checkId"), str)
              and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", event["checkId"]) is not None
              and isinstance(event.get("url"), str)
              and type(event.get("executionBudgetMs")) is int and 1 <= event["executionBudgetMs"] <= 10000)
    if single:
        check_id = event["checkId"]
        remaining = min(event["executionBudgetMs"] / 1000, max(0, context.get_remaining_time_in_millis() / 1000 - 0.5))
        result = {"schemaVersion": 2, "resolutionStatus": "partial", "hops": [], "location": None,
                  "reasonCodes": ["INTERNAL_ERROR"], "requestCount": 0, "scope": "HTTP_SINGLE_HOP_ONLY"}
        try:
            result = resolve_step(event["url"], Transport(), Deadline(remaining))
        except Exception:
            # Unknown internal failure cannot prove no target contact.
            result["requestCount"] = 1
    elif valid:
        check_id = event["checkId"]
        remaining = min(10.0, max(0, context.get_remaining_time_in_millis() / 1000 - 0.5))
        try:
            result = resolve(event["url"], Transport(), Deadline(remaining))
        except Exception:
            # Do not expose network/library exceptions containing raw input.
            result["resolutionStatus"] = "partial"
            result["reasonCodes"] = ["INTERNAL_ERROR"]
    result["checkId"] = check_id
    # Deliberately omit even the caller-provided checkId from telemetry.
    print(json.dumps({
        "event": "url_resolution", "status": result["resolutionStatus"],
        "reason": (result["reasonCodes"] or ["HTTP_RESPONSE_OBSERVED"])[0], "requestCount": result["requestCount"],
        "hopCount": len(result["hops"]), "elapsedMs": int((time.monotonic() - started) * 1000),
    }))
    return result
