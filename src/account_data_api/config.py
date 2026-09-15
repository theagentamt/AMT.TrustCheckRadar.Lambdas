import json
import os
import re

from errors import AppError


APP_ENVIRONMENT = os.environ.get("APP_ENVIRONMENT", "")
USERS_TABLE_NAME = os.environ.get("USERS_TABLE_NAME", "")
DELETION_LEDGER_TABLE_NAME = os.environ.get("DELETION_LEDGER_TABLE_NAME", "")
DEVICE_BINDINGS_TABLE_NAME = os.environ.get("DEVICE_BINDINGS_TABLE_NAME", "")
DEVICE_RECOVERY_CONTROL_TABLE_NAME = os.environ.get(
    "DEVICE_RECOVERY_CONTROL_TABLE_NAME", ""
)
ANALYSIS_ABUSE_TABLE_NAME = os.environ.get("ANALYSIS_ABUSE_TABLE_NAME", "")
CAMPAIGN_OUTBOX_TABLE_NAME = os.environ.get("CAMPAIGN_OUTBOX_TABLE_NAME", "")
COGNITO_ISSUER = os.environ.get("COGNITO_ISSUER", "")
COGNITO_APP_CLIENT_ID = os.environ.get("COGNITO_APP_CLIENT_ID", "")
COGNITO_REQUIRED_SCOPE = os.environ.get(
    "COGNITO_REQUIRED_SCOPE", "aws.cognito.signin.user.admin"
)
COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
COGNITO_USERNAME_IS_SUB = (
    os.environ.get("COGNITO_USERNAME_IS_SUB", "false").strip().lower() == "true"
)
ACCOUNT_DELETION_ENABLED = (
    os.environ.get("ACCOUNT_DELETION_ENABLED", "false").strip().lower() == "true"
)
ACCOUNT_DELETION_POLICY_STATUS = os.environ.get(
    "ACCOUNT_DELETION_POLICY_STATUS", "pending"
)
ACCOUNT_DATA_INVENTORY_STATUS = os.environ.get(
    "ACCOUNT_DATA_INVENTORY_STATUS", "pending"
)
ACCOUNT_DELETION_COMPLETION_STATUS = os.environ.get(
    "ACCOUNT_DELETION_COMPLETION_STATUS", "incomplete"
)
ACCOUNT_DELETION_MAX_REAUTH_AGE_SECONDS = int(
    os.environ.get("ACCOUNT_DELETION_MAX_REAUTH_AGE_SECONDS", "300")
)
ACCOUNT_DELETION_SLA_HOURS = int(
    os.environ.get("ACCOUNT_DELETION_SLA_HOURS", "24")
)
ACCOUNT_DELETION_RECONCILIATION_SCAN_LIMIT = int(
    os.environ.get("ACCOUNT_DELETION_RECONCILIATION_SCAN_LIMIT", "100")
)
ACCOUNT_DELETION_RECONCILIATION_MAX_PAGES = int(
    os.environ.get("ACCOUNT_DELETION_RECONCILIATION_MAX_PAGES", "10")
)
ACCOUNT_DELETION_DEVICE_DELETE_PAGE_SIZE = int(
    os.environ.get("ACCOUNT_DELETION_DEVICE_DELETE_PAGE_SIZE", "100")
)
ACCOUNT_DELETION_RECOVERY_DELETE_PAGE_SIZE = int(
    os.environ.get("ACCOUNT_DELETION_RECOVERY_DELETE_PAGE_SIZE", "100")
)
ACCOUNT_DELETION_ANALYSIS_ABUSE_PAGE_SIZE = int(
    os.environ.get("ACCOUNT_DELETION_ANALYSIS_ABUSE_PAGE_SIZE", "100")
)
ACCOUNT_DELETION_CAMPAIGN_OUTBOX_PAGE_SIZE = int(
    os.environ.get("ACCOUNT_DELETION_CAMPAIGN_OUTBOX_PAGE_SIZE", "100")
)
DEVICE_RECOVERY_RECEIPT_RETENTION_DAYS = int(
    os.environ.get("DEVICE_RECOVERY_RECEIPT_RETENTION_DAYS", "7")
)
DEVICE_RECOVERY_AUDIT_RETENTION_DAYS = int(
    os.environ.get("DEVICE_RECOVERY_AUDIT_RETENTION_DAYS", "90")
)
DEVICE_RECOVERY_RATE_STATE_TTL_SECONDS = int(
    os.environ.get("DEVICE_RECOVERY_RATE_STATE_TTL_SECONDS", "86400")
)
ACCOUNT_DELETION_RECEIPT_RETENTION_DAYS = int(
    os.environ.get("ACCOUNT_DELETION_RECEIPT_RETENTION_DAYS", "120")
)
ANALYSIS_REQUEST_ID_TTL_SECONDS = int(
    os.environ.get("ANALYSIS_REQUEST_ID_TTL_SECONDS", "900")
)
ANALYSIS_REQUEST_DEDUPE_POLICY_STATUS = os.environ.get(
    "ANALYSIS_REQUEST_DEDUPE_POLICY_STATUS", "pending"
)
ANALYSIS_LEGACY_REQUEST_RETENTION_POLICY_STATUS = os.environ.get(
    "ANALYSIS_LEGACY_REQUEST_RETENTION_POLICY_STATUS", "pending"
)
ANALYSIS_CONSUMPTION_DELETION_POLICY_STATUS = os.environ.get(
    "ANALYSIS_CONSUMPTION_DELETION_POLICY_STATUS", "pending"
)
CAMPAIGN_OUTBOX_LOCATOR_COVERAGE_STATUS = os.environ.get(
    "CAMPAIGN_OUTBOX_LOCATOR_COVERAGE_STATUS", "pending"
)
USER_PROFILE_DELETION_POLICY_STATUS = os.environ.get(
    "USER_PROFILE_DELETION_POLICY_STATUS", "pending"
)
HISTORY_DEDUP_RETENTION_DAYS = int(
    os.environ.get("HISTORY_DEDUP_RETENTION_DAYS", "120")
)

COMPONENT_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def validate_config():
    if not ACCOUNT_DELETION_ENABLED:
        raise AppError(
            "FEATURE_DISABLED", "Account deletion is not enabled.", retryable=True
        )
    if (
        APP_ENVIRONMENT not in {"dev", "uat", "prod"}
        or not USERS_TABLE_NAME
        or not DELETION_LEDGER_TABLE_NAME
        or not DEVICE_BINDINGS_TABLE_NAME
        or not DEVICE_RECOVERY_CONTROL_TABLE_NAME
        or not ANALYSIS_ABUSE_TABLE_NAME
        or not CAMPAIGN_OUTBOX_TABLE_NAME
        or not COGNITO_ISSUER.startswith("https://cognito-idp.")
        or not COGNITO_APP_CLIENT_ID
        or not COGNITO_USER_POOL_ID
        or COGNITO_REQUIRED_SCOPE != "aws.cognito.signin.user.admin"
        or not COGNITO_USERNAME_IS_SUB
    ):
        raise AppError("SERVER_UNAVAILABLE", "Account deletion is not configured.")
    if (
        ACCOUNT_DELETION_POLICY_STATUS != "approved"
        or ACCOUNT_DATA_INVENTORY_STATUS != "approved"
        or ACCOUNT_DELETION_COMPLETION_STATUS != "complete"
        or ACCOUNT_DELETION_MAX_REAUTH_AGE_SECONDS != 300
        or ACCOUNT_DELETION_SLA_HOURS != 24
        or not 1 <= ACCOUNT_DELETION_RECONCILIATION_SCAN_LIMIT <= 100
        or not 1 <= ACCOUNT_DELETION_RECONCILIATION_MAX_PAGES <= 10
        or ACCOUNT_DELETION_DEVICE_DELETE_PAGE_SIZE != 100
        or ACCOUNT_DELETION_RECOVERY_DELETE_PAGE_SIZE != 100
        or ACCOUNT_DELETION_ANALYSIS_ABUSE_PAGE_SIZE != 100
        or ACCOUNT_DELETION_CAMPAIGN_OUTBOX_PAGE_SIZE != 100
        or DEVICE_RECOVERY_RECEIPT_RETENTION_DAYS != 7
        or DEVICE_RECOVERY_AUDIT_RETENTION_DAYS != 90
        or DEVICE_RECOVERY_RATE_STATE_TTL_SECONDS != 86400
        or ACCOUNT_DELETION_RECEIPT_RETENTION_DAYS != 120
        or ANALYSIS_REQUEST_ID_TTL_SECONDS != 900
        or ANALYSIS_REQUEST_DEDUPE_POLICY_STATUS != "approved"
        or ANALYSIS_LEGACY_REQUEST_RETENTION_POLICY_STATUS != "approved"
        or ANALYSIS_CONSUMPTION_DELETION_POLICY_STATUS != "approved"
        or CAMPAIGN_OUTBOX_LOCATOR_COVERAGE_STATUS != "approved"
        or USER_PROFILE_DELETION_POLICY_STATUS != "approved"
        or HISTORY_DEDUP_RETENTION_DAYS != 120
    ):
        raise AppError("SERVER_UNAVAILABLE", "The account-deletion policy is not approved.")
    required_components()


def required_components():
    try:
        values = json.loads(os.environ.get("ACCOUNT_DELETION_REQUIRED_COMPONENTS_JSON", ""))
    except ValueError as err:
        raise AppError("SERVER_UNAVAILABLE", "The account-data inventory is invalid.") from err
    if (
        not isinstance(values, list)
        or not 1 <= len(values) <= 16
        or len(set(values)) != len(values)
        or any(not isinstance(value, str) or not COMPONENT_PATTERN.fullmatch(value) for value in values)
        or not {
            "SESSION_REVOCATION", "DEVICE_BINDINGS", "DEVICE_RECOVERY",
            "ANALYSIS_ABUSE", "HISTORY", "CAMPAIGN",
            "CAMPAIGN_OUTBOX",
            "ENTITLEMENTS", "USER_PROFILE", "IDENTITY",
        }.issubset(values)
    ):
        raise AppError("SERVER_UNAVAILABLE", "The account-data inventory is invalid.")
    return tuple(values)
