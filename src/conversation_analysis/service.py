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
    LOGGER.info("Stage started: request_lock")
    request_state = check_or_lock_request(identity, request_id, payload)
    if request_state["state"] == "completed":
        LOGGER.info("Stage completed: request_lock_replay_hit")
        return request_state["response"]
    if request_state["state"] == "result_ready":
        LOGGER.info("Stage completed: request_result_recovery")
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
    LOGGER.info("Stage completed: request_processing_lease")

    result_stored = False
    lease_token = request_state["leaseToken"]
    payload_hash = request_state["payloadHash"]
    try:
        LOGGER.info("Stage started: quota_precheck")
        access_grant = prepare_scan_access(identity)
        snapshot = access_grant.get("snapshot") or {}
        LOGGER.info(
            "Stage completed: quota_precheck | consumptionType=%s remainingMonthlyScans=%s remainingCredits=%s",
            access_grant["consumptionType"],
            snapshot.get("remainingMonthlyScans"),
            snapshot.get("remainingCredits"),
        )

        LOGGER.info("Stage started: prompt_safety_check")
        if is_instruction_style_abuse(payload["sanitizedText"]):
            LOGGER.info("Stage completed: prompt_safety_check | outcome=short_circuit")
            analysis = build_safe_low_confidence_response()
        else:
            LOGGER.info("Stage completed: prompt_safety_check | outcome=model_call")
            LOGGER.info("Stage started: model_analysis")
            analysis = analyze_conversation(payload)
            LOGGER.info(
                "Stage completed: model_analysis | riskLevel=%s scamScore=%s",
                analysis.get("riskLevel"),
                analysis.get("scamScore"),
            )

        LOGGER.info("Stage started: response_build")
        response_body = build_success_response(request_id=request_id, analysis=analysis)
        LOGGER.info("Stage completed: response_build")

        LOGGER.info("Stage started: result_store")
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
        LOGGER.info("Stage completed: result_store")

        LOGGER.info("Stage started: atomic_commit")
        commit_scan_and_request(
            access_grant,
            request_id,
            payload_hash,
            response_body,
            campaign_payload=payload,
            statistics_event_id=statistics_event_id,
            campaign_authorization=authorization,
        )
        LOGGER.info("Stage completed: atomic_commit")
        return response_body
    except Exception:
        if not result_stored:
            LOGGER.info("Stage started: request_release")
            release_request(identity, request_id, lease_token)
            LOGGER.info("Stage completed: request_release")
        raise
