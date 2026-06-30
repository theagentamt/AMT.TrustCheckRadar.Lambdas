from abuse_controls import check_or_lock_request, complete_request, release_request
from analysis_client import analyze_conversation
from errors import AppError
from response_builders import build_success_response
from safety import build_safe_low_confidence_response, is_instruction_style_abuse
from scan_access import consume_scan_access, prepare_scan_access


def handle_analysis_request(payload: dict, identity: str) -> dict:
    request_id = payload["requestId"]
    request_state = check_or_lock_request(identity, request_id)
    if request_state["state"] == "completed":
        return request_state["response"]

    quota_consumed = False
    try:
        access_grant = prepare_scan_access(identity)
        if is_instruction_style_abuse(payload["sanitizedText"]):
            analysis = build_safe_low_confidence_response()
        else:
            analysis = analyze_conversation(payload)
        response_body = build_success_response(request_id=request_id, analysis=analysis)
        consume_scan_access(access_grant, request_id)
        quota_consumed = True
        complete_request(identity, request_id, response_body)
        return response_body
    except Exception:
        if not quota_consumed:
            release_request(identity, request_id)
        raise
