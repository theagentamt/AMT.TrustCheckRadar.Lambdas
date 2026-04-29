import json
import logging
from urllib import error, request

import boto3

from config import OPENAI_MODEL, OPENAI_RESPONSES_ENDPOINT, OPENAI_SECRET_FIELD, OPENAI_SECRET_NAME, OPENAI_TIMEOUT_SECONDS
from errors import AppError

LOGGER = logging.getLogger()
secrets_client = boto3.client("secretsmanager")

PROMPT = """You are analyzing sanitized conversation text for scam risk.
Return JSON only with this exact shape:
{
  "scamScore": integer from 0 to 100,
  "riskLevel": "low" | "medium" | "high",
  "confidence": number from 0 to 1,
  "summary": string,
  "signals": array of short snake_case strings,
  "recommendedActions": array of user-facing strings
}
Use the sanitizedText, sourceType, and entities to determine the result."""


def analyze_conversation(payload: dict) -> dict:
    api_key = _get_api_key()
    request_body = {
        "model": OPENAI_MODEL,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": PROMPT}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            {
                                "sourceType": payload["sourceType"],
                                "sanitizedText": payload["sanitizedText"],
                                "entities": payload["entities"],
                            }
                        ),
                    }
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "conversation_analysis_response",
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["scamScore", "riskLevel", "confidence", "summary", "signals", "recommendedActions"],
                    "properties": {
                        "scamScore": {"type": "integer", "minimum": 0, "maximum": 100},
                        "riskLevel": {"type": "string", "enum": ["low", "medium", "high"]},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "summary": {"type": "string"},
                        "signals": {"type": "array", "items": {"type": "string"}},
                        "recommendedActions": {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        },
    }

    req = request.Request(
        OPENAI_RESPONSES_ENDPOINT,
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=OPENAI_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as err:
        status_code = getattr(err, "code", 500)
        LOGGER.warning("OpenAI HTTP error: %s", status_code)
        if status_code == 401:
            raise AppError("UNAUTHORIZED", "The analysis service rejected the request.", retryable=False) from err
        if status_code == 403:
            raise AppError("FORBIDDEN", "The analysis service denied access to this request.", retryable=False) from err
        if status_code == 429:
            raise AppError("RATE_LIMITED", "The analysis service is currently rate limiting requests.", retryable=True) from err
        if status_code == 504:
            raise AppError("ANALYSIS_TIMEOUT", "The analysis service timed out.", retryable=True) from err
        raise AppError("SERVER_UNAVAILABLE", "The analysis service is currently unavailable.", retryable=True) from err
    except error.URLError as err:
        LOGGER.warning("OpenAI connection error: %s", err.reason)
        raise AppError("SERVER_UNAVAILABLE", "The analysis service is currently unavailable.", retryable=True) from err

    return _parse_analysis_response(raw)


def _get_api_key() -> str:
    if not OPENAI_SECRET_NAME:
        raise AppError("SERVER_UNAVAILABLE", "The analysis service configuration is incomplete.", retryable=False)

    response = secrets_client.get_secret_value(SecretId=OPENAI_SECRET_NAME)
    secret_string = response.get("SecretString")
    if not secret_string:
        raise AppError("SERVER_UNAVAILABLE", "The analysis service configuration is incomplete.", retryable=False)

    try:
        parsed_secret = json.loads(secret_string)
    except json.JSONDecodeError:
        parsed_secret = secret_string

    if isinstance(parsed_secret, dict):
        for field_name in (OPENAI_SECRET_FIELD, "apiKey", "OPENAI_API_KEY"):
            value = parsed_secret.get(field_name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    elif isinstance(parsed_secret, str) and parsed_secret.strip():
        return parsed_secret.strip()

    raise AppError("SERVER_UNAVAILABLE", "The analysis service configuration is incomplete.", retryable=False)


def _parse_analysis_response(raw_response: str) -> dict:
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as err:
        raise AppError("SERVER_UNAVAILABLE", "The analysis service returned an invalid response.", retryable=True) from err

    output_text = None
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                output_text = content.get("text")
                break
        if output_text:
            break

    if not output_text:
        raise AppError("SERVER_UNAVAILABLE", "The analysis service returned an incomplete response.", retryable=True)

    try:
        analysis = json.loads(output_text)
    except json.JSONDecodeError as err:
        raise AppError("SERVER_UNAVAILABLE", "The analysis service returned an invalid response.", retryable=True) from err

    return _validate_analysis(analysis)


def _validate_analysis(analysis: dict) -> dict:
    if not isinstance(analysis, dict):
        raise AppError("SERVER_UNAVAILABLE", "The analysis service returned an invalid response.", retryable=True)

    scam_score = analysis.get("scamScore")
    confidence = analysis.get("confidence")
    risk_level = analysis.get("riskLevel")
    summary = analysis.get("summary")
    signals = analysis.get("signals")
    recommended_actions = analysis.get("recommendedActions")

    if not isinstance(scam_score, int) or not 0 <= scam_score <= 100:
        raise AppError("SERVER_UNAVAILABLE", "The analysis service returned an invalid response.", retryable=True)
    if risk_level not in {"low", "medium", "high"}:
        raise AppError("SERVER_UNAVAILABLE", "The analysis service returned an invalid response.", retryable=True)
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        raise AppError("SERVER_UNAVAILABLE", "The analysis service returned an invalid response.", retryable=True)
    if not isinstance(summary, str) or not summary.strip():
        raise AppError("SERVER_UNAVAILABLE", "The analysis service returned an invalid response.", retryable=True)
    if not isinstance(signals, list) or not all(isinstance(item, str) for item in signals):
        raise AppError("SERVER_UNAVAILABLE", "The analysis service returned an invalid response.", retryable=True)
    if not isinstance(recommended_actions, list) or not all(isinstance(item, str) for item in recommended_actions):
        raise AppError("SERVER_UNAVAILABLE", "The analysis service returned an invalid response.", retryable=True)

    return {
        "scamScore": scam_score,
        "riskLevel": risk_level,
        "confidence": round(float(confidence), 4),
        "summary": summary.strip(),
        "signals": signals,
        "recommendedActions": recommended_actions,
    }
