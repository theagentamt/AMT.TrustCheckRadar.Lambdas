from analysis_client import analyze_conversation
from response_builders import build_success_response


def handle_analysis_request(payload: dict) -> dict:
    analysis = analyze_conversation(payload)
    return build_success_response(request_id=payload["requestId"], analysis=analysis)
