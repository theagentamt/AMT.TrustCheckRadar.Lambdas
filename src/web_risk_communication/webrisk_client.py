import json
import logging
from typing import Any
from urllib import error, request

import boto3

from config import CONFIDENCE_SCORES, SECRET_FIELD, SECRET_NAME, THREAT_MULTIPLIERS, THREAT_TYPES, WEB_RISK_ENDPOINT

LOGGER = logging.getLogger()
secrets_client = boto3.client("secretsmanager")


def call_google_web_risk(uri: str) -> dict[str, Any]:
    api_key = get_api_key()
    payload = {
        "uri": uri,
        "threatTypes": THREAT_TYPES,
        "allowScan": True,
    }
    http_request = request.Request(
        WEB_RISK_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )

    try:
        with request.urlopen(http_request, timeout=10) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as err:
        LOGGER.warning("Web Risk API HTTP error: %s", err.code)
        raise ValueError("Unable to evaluate url risk at this time") from err
    except error.URLError as err:
        LOGGER.warning("Web Risk API connection error: %s", err.reason)
        raise ValueError("Unable to evaluate url risk at this time") from err

    parsed = json.loads(raw) if raw else {}
    return {"uri": uri, "threats": extract_threats(parsed)}


def get_api_key() -> str:
    response = secrets_client.get_secret_value(SecretId=SECRET_NAME)
    secret_string = response.get("SecretString")
    if not secret_string:
        raise ValueError("Web risk API secret is unavailable")

    try:
        parsed_secret = json.loads(secret_string)
    except json.JSONDecodeError:
        parsed_secret = secret_string

    if isinstance(parsed_secret, dict):
        for field_name in (SECRET_FIELD, "apiKey", "WEB_RISK_API_KEY"):
            value = parsed_secret.get(field_name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    elif isinstance(parsed_secret, str) and parsed_secret.strip():
        return parsed_secret.strip()

    raise ValueError("Web risk API secret does not contain a usable key")


def extract_threats(payload: Any) -> list[dict[str, str]]:
    if isinstance(payload, list):
        raw_threats = payload
    elif isinstance(payload, dict):
        raw_threats = payload.get("threats") or payload.get("matches") or payload.get("scores") or []
    else:
        raw_threats = []

    threats = []
    for threat in raw_threats:
        if not isinstance(threat, dict):
            continue
        threat_type = threat.get("threatType")
        confidence_level = threat.get("confidenceLevel")
        if threat_type in THREAT_MULTIPLIERS and confidence_level in CONFIDENCE_SCORES:
            threats.append(
                {
                    "threatType": threat_type,
                    "confidenceLevel": confidence_level,
                }
            )
    return threats
