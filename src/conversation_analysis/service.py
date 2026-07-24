import logging

from abuse_controls import check_or_lock_request, complete_request, release_request
from analysis_client import analyze_conversation
from errors import AppError
from response_builders import build_success_response
from safety import build_safe_low_confidence_response, is_instruction_style_abuse
from scan_access import consume_scan_access, prepare_scan_access

LOGGER = logging.getLogger(__name__)


def handle_analysis_request(payload: dict, identity: str) -> dict:
    request_id = payload["requestId"]
    LOGGER.info("Stage started: request_lock | requestId=%s accountId=%s", request_id, identity)
    request_state = check_or_lock_request(identity, request_id)
    if request_state["state"] == "completed":
        LOGGER.info("Stage completed: request_lock_replay_hit | requestId=%s accountId=%s", request_id, identity)
        return request_state["response"]
    LOGGER.info("Stage completed: request_locked | requestId=%s accountId=%s", request_id, identity)

    quota_consumed = False
    try:
        LOGGER.info("Stage started: quota_precheck | requestId=%s accountId=%s", request_id, identity)
        access_grant = prepare_scan_access(identity)
        snapshot = access_grant.get("snapshot") or {}
        LOGGER.info(
            "Stage completed: quota_precheck | requestId=%s accountId=%s consumptionType=%s remainingMonthlyScans=%s remainingCredits=%s",
            request_id,
            identity,
            access_grant["consumptionType"],
            snapshot.get("remainingMonthlyScans"),
            snapshot.get("remainingCredits"),
        )

        LOGGER.info("Stage started: prompt_safety_check | requestId=%s accountId=%s", request_id, identity)
        if is_instruction_style_abuse(payload["sanitizedText"]):
            LOGGER.info("Stage completed: prompt_safety_check | requestId=%s accountId=%s outcome=short_circuit", request_id, identity)
            analysis = build_safe_low_confidence_response()
        else:
            LOGGER.info("Stage completed: prompt_safety_check | requestId=%s accountId=%s outcome=model_call", request_id, identity)
            LOGGER.info("Stage started: model_analysis | requestId=%s accountId=%s", request_id, identity)
            analysis = analyze_conversation(payload)
            LOGGER.info("Stage completed: model_analysis | requestId=%s accountId=%s riskLevel=%s scamScore=%s", request_id, identity, analysis.get("riskLevel"), analysis.get("scamScore"))

        LOGGER.info("Stage started: response_build | requestId=%s accountId=%s", request_id, identity)
        response_body = build_success_response(request_id=request_id, analysis=analysis)
        LOGGER.info("Stage completed: response_build | requestId=%s accountId=%s", request_id, identity)

        LOGGER.info("Stage started: quota_consume | requestId=%s accountId=%s", request_id, identity)
        consume_scan_access(access_grant, request_id)
        quota_consumed = True
        LOGGER.info("Stage completed: quota_consume | requestId=%s accountId=%s", request_id, identity)

        LOGGER.info("Stage started: request_complete | requestId=%s accountId=%s", request_id, identity)
        complete_request(identity, request_id, response_body)
        LOGGER.info("Stage completed: request_complete | requestId=%s accountId=%s", request_id, identity)
        return response_body
    except Exception:
        if not quota_consumed:
            LOGGER.info("Stage started: request_release | requestId=%s accountId=%s", request_id, identity)
            release_request(identity, request_id)
            LOGGER.info("Stage completed: request_release | requestId=%s accountId=%s", request_id, identity)
        raise
