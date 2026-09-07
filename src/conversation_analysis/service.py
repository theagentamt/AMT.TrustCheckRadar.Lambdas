import logging
import uuid

from abuse_controls import check_or_lock_request, release_request, store_result
from analysis_client import analyze_conversation
from errors import AppError
from response_builders import build_success_response
from safety import build_safe_low_confidence_response, is_instruction_style_abuse
from scan_access import campaign_authorization, commit_scan_and_request, prepare_scan_access

LOGGER = logging.getLogger(__name__)


def handle_analysis_request(payload: dict, identity: str) -> dict:
    request_id = payload["requestId"]
    LOGGER.info("Stage started: request_lock | requestId=%s", request_id)
    request_state = check_or_lock_request(identity, request_id, payload)
    if request_state["state"] == "completed":
        LOGGER.info("Stage completed: request_lock_replay_hit | requestId=%s", request_id)
        return request_state["response"]
    if request_state["state"] == "result_ready":
        LOGGER.info("Stage completed: request_result_recovery | requestId=%s", request_id)
        access_grant = prepare_scan_access(identity)
        commit_scan_and_request(
            access_grant,
            request_id,
            request_state["payloadHash"],
            request_state["response"],
            campaign_payload=payload,
            statistics_event_id=request_state.get("statisticsEventId"),
            campaign_authorization=request_state.get("campaignAuthorization"),
        )
        return request_state["response"]
    LOGGER.info("Stage completed: request_processing_lease | requestId=%s", request_id)

    result_stored = False
    lease_token = request_state["leaseToken"]
    payload_hash = request_state["payloadHash"]
    try:
        LOGGER.info("Stage started: quota_precheck | requestId=%s", request_id)
        access_grant = prepare_scan_access(identity)
        snapshot = access_grant.get("snapshot") or {}
        LOGGER.info(
            "Stage completed: quota_precheck | requestId=%s consumptionType=%s remainingMonthlyScans=%s remainingCredits=%s",
            request_id,
            access_grant["consumptionType"],
            snapshot.get("remainingMonthlyScans"),
            snapshot.get("remainingCredits"),
        )

        LOGGER.info("Stage started: prompt_safety_check | requestId=%s", request_id)
        if is_instruction_style_abuse(payload["sanitizedText"]):
            LOGGER.info("Stage completed: prompt_safety_check | requestId=%s outcome=short_circuit", request_id)
            analysis = build_safe_low_confidence_response()
        else:
            LOGGER.info("Stage completed: prompt_safety_check | requestId=%s outcome=model_call", request_id)
            LOGGER.info("Stage started: model_analysis | requestId=%s", request_id)
            analysis = analyze_conversation(payload)
            LOGGER.info("Stage completed: model_analysis | requestId=%s riskLevel=%s scamScore=%s", request_id, analysis.get("riskLevel"), analysis.get("scamScore"))

        LOGGER.info("Stage started: response_build | requestId=%s", request_id)
        response_body = build_success_response(request_id=request_id, analysis=analysis)
        LOGGER.info("Stage completed: response_build | requestId=%s", request_id)

        LOGGER.info("Stage started: result_store | requestId=%s", request_id)
        authorization = (
            campaign_authorization(access_grant)
            if payload.get("campaignConsentGranted")
            else None
        )
        statistics_event_id = str(uuid.uuid4()) if authorization else None
        store_result(
            identity,
            request_id,
            payload_hash,
            lease_token,
            response_body,
            statistics_event_id=statistics_event_id,
            campaign_authorization=authorization,
        )
        result_stored = True
        LOGGER.info("Stage completed: result_store | requestId=%s", request_id)

        LOGGER.info("Stage started: atomic_commit | requestId=%s", request_id)
        commit_scan_and_request(
            access_grant,
            request_id,
            payload_hash,
            response_body,
            campaign_payload=payload,
            statistics_event_id=statistics_event_id,
            campaign_authorization=authorization,
        )
        LOGGER.info("Stage completed: atomic_commit | requestId=%s", request_id)
        return response_body
    except Exception:
        if not result_stored:
            LOGGER.info("Stage started: request_release | requestId=%s", request_id)
            release_request(identity, request_id, lease_token)
            LOGGER.info("Stage completed: request_release | requestId=%s", request_id)
        raise
