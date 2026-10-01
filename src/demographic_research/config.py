import json
import os
from uuid import UUID

from errors import AppError

PURPOSE = "optional-demographic-protection-research"
PURPOSE_VERSION = "consumer-protection-research-v1"
NOTICE_VERSION = "demographic-research-2026-09-30-v1"
POLICY_VERSION = "optional-demographic-research-v1"
PROFILE_RETENTION_DAYS = 400
OPERATION_RETENTION_DAYS = 7
CONSENT_AUDIT_RETENTION_DAYS = 400
VALUE_CLEANUP_SLA_HOURS = 24
METRICS_NAMESPACE = "TrustCheckRadar/DemographicResearch"

SERVICE_ENABLED = os.environ.get("DEMOGRAPHIC_RESEARCH_SERVICE_ENABLED", "false").lower() == "true"
ENROLLMENT_ENABLED = os.environ.get("DEMOGRAPHIC_RESEARCH_ENROLLMENT_ENABLED", "false").lower() == "true"
ENVIRONMENT = os.environ.get("DEMOGRAPHIC_RESEARCH_ENVIRONMENT", "")
USERS_TABLE_NAME = os.environ.get("DEMOGRAPHIC_RESEARCH_USERS_TABLE_NAME", "")
DELETION_LEDGER_TABLE_NAME = os.environ.get("DEMOGRAPHIC_RESEARCH_DELETION_LEDGER_TABLE_NAME", "")


def require_service(subject=None):
    if not SERVICE_ENABLED:
        raise AppError("FEATURE_DISABLED", "Demographic research is not available.", retryable=True)
    try:
        raw = os.environ.get("DEMOGRAPHIC_RESEARCH_HTTP_SUBJECTS_JSON", "[]")
        values = json.loads(raw)
        if (not isinstance(values, list) or not 1 <= len(values) <= 10 or len(set(values)) != len(values)
                or any(not isinstance(value, str) or str(UUID(value)) != value for value in values)
                or subject not in values):
            raise ValueError
    except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
        raise AppError("FEATURE_DISABLED", "Demographic research is not available.", retryable=True) from None
    if ENVIRONMENT not in {"dev", "uat", "prod"} or not USERS_TABLE_NAME or not DELETION_LEDGER_TABLE_NAME:
        raise AppError("SERVER_UNAVAILABLE", "Demographic research is not configured.", retryable=True)
