import logging

from abuse_controls import check_or_lock_request, release_request, store_result
from analysis_client import analyze_conversation
from errors import AppError
from response_builders import build_success_response
from safety import build_safe_low_confidence_response, is_instruction_style_abuse
from scan_access import commit_scan_and_request, prepare_scan_access

LOGGER = logging.getLogger(__name__)


def handle_analysis_request(payload: dict, identity: str) -> dict:
    request_id = payload["requestId"]
    LOGGER.info("Stage started: request_lock | requestId=%s accountId=%s", request_id, identity)
    request_state = check_or_lock_request(identity, request_id, payload)
    if request_state["state"] == "completed":
        LOGGER.info("Stage completed: request_lock_replay_hit | requestId=%s accountId=%s", request_id, identity)
        return request_state["response"]
    if request_state["state"] == "result_ready":
        LOGGER.info("Stage completed: request_result_recovery | requestId=%s accountId=%s", request_id, identity)
        access_grant = prepare_scan_access(identity)
        commit_scan_and_request(
            access_grant,
            request_id,
            request_state["payloadHash"],
            request_state["response"],
        )
        return request_state["response"]
    LOGGER.info("Stage completed: request_processing_lease | requestId=%s accountId=%s", request_id, identity)

    result_stored = False
    lease_token = request_state["leaseToken"]
    payload_hash = request_state["payloadHash"]
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

        LOGGER.info("Stage started: result_store | requestId=%s accountId=%s", request_id, identity)
        store_result(identity, request_id, payload_hash, lease_token, response_body)
        result_stored = True
        LOGGER.info("Stage completed: result_store | requestId=%s accountId=%s", request_id, identity)

        LOGGER.info("Stage started: atomic_commit | requestId=%s accountId=%s", request_id, identity)
        commit_scan_and_request(access_grant, request_id, payload_hash, response_body)
        LOGGER.info("Stage completed: atomic_commit | requestId=%s accountId=%s", request_id, identity)
        return response_body
    except Exception:
        if not result_stored:
            LOGGER.info("Stage started: request_release | requestId=%s accountId=%s", request_id, identity)
            release_request(identity, request_id, lease_token)
            LOGGER.info("Stage completed: request_release | requestId=%s accountId=%s", request_id, identity)
        raise
