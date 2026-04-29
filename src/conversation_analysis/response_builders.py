from errors import AppError


def build_success_response(*, request_id: str, analysis: dict) -> dict:
    return {
        "schemaVersion": "1.0",
        "requestId": request_id,
        "scamScore": analysis["scamScore"],
        "riskLevel": analysis["riskLevel"],
        "confidence": analysis["confidence"],
        "summary": analysis["summary"],
        "signals": analysis["signals"],
        "recommendedActions": analysis["recommendedActions"],
    }


def build_error_response(*, request_id: str | None, err: AppError) -> dict:
    body = {
        "schemaVersion": "1.0",
        "requestId": request_id,
        "error": {
            "code": err.code,
            "message": err.message,
            "retryable": err.retryable,
        },
    }
    if err.details:
        body["error"]["details"] = err.details
    return body
