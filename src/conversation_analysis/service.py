from abuse_controls import check_or_lock_request, complete_request, enforce_rate_limit, release_request
from analysis_client import analyze_conversation
from errors import AppError
from response_builders import build_success_response
from safety import build_safe_low_confidence_response, is_instruction_style_abuse


def handle_analysis_request(payload: dict, identity: str) -> dict:
    request_id = payload["requestId"]
    check_or_lock_request(identity, request_id)
    try:
        enforce_rate_limit(identity)
        if is_instruction_style_abuse(payload["sanitizedText"]):
            analysis = build_safe_low_confidence_response()
        else:
            analysis = analyze_conversation(payload)
        response_body = build_success_response(request_id=request_id, analysis=analysis)
        complete_request(identity, request_id)
        return response_body
    except Exception:
        release_request(identity, request_id)
        raise
