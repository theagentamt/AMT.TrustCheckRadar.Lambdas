import os
import re

DEVICE_BINDINGS_TABLE_NAME = os.environ.get("DEVICE_BINDINGS_TABLE_NAME") or os.environ.get("TABLE_NAME")
DEVICE_BINDINGS_INACTIVE_RETENTION_DAYS = int(os.environ.get("DEVICE_BINDINGS_INACTIVE_RETENTION_DAYS", "180"))
DEVICE_RECOVERY_CONTROL_TABLE_NAME = os.environ.get("DEVICE_RECOVERY_CONTROL_TABLE_NAME", "")
USERS_TABLE_NAME = os.environ.get("USERS_TABLE_NAME", "")
DELETION_LEDGER_TABLE_NAME = os.environ.get("DELETION_LEDGER_TABLE_NAME", "")
APP_ENVIRONMENT = os.environ.get("APP_ENVIRONMENT", "")
COGNITO_ISSUER = os.environ.get("COGNITO_ISSUER", "")
COGNITO_APP_CLIENT_ID = os.environ.get("COGNITO_APP_CLIENT_ID", "")
COGNITO_REQUIRED_SCOPE = os.environ.get(
    "COGNITO_REQUIRED_SCOPE", "aws.cognito.signin.user.admin"
)
DEVICE_SELF_RECOVERY_ENABLED = os.environ.get(
    "DEVICE_SELF_RECOVERY_ENABLED", "false"
).strip().lower() == "true"
DEVICE_RECOVERY_POLICY_STATUS = os.environ.get("DEVICE_RECOVERY_POLICY_STATUS", "pending")
DEVICE_RECOVERY_MAX_REAUTH_AGE_SECONDS = int(
    os.environ.get("DEVICE_RECOVERY_MAX_REAUTH_AGE_SECONDS", "300")
)
DEVICE_RECOVERY_RATE_WINDOW_SECONDS = int(
    os.environ.get("DEVICE_RECOVERY_RATE_WINDOW_SECONDS", "3600")
)
DEVICE_RECOVERY_RATE_MAX_REQUESTS = int(
    os.environ.get("DEVICE_RECOVERY_RATE_MAX_REQUESTS", "3")
)
DEVICE_RECOVERY_RATE_STATE_TTL_SECONDS = int(
    os.environ.get("DEVICE_RECOVERY_RATE_STATE_TTL_SECONDS", "86400")
)
DEVICE_RECOVERY_RECEIPT_RETENTION_DAYS = int(
    os.environ.get("DEVICE_RECOVERY_RECEIPT_RETENTION_DAYS", "7")
)
DEVICE_RECOVERY_AUDIT_RETENTION_DAYS = int(
    os.environ.get("DEVICE_RECOVERY_AUDIT_RETENTION_DAYS", "0")
)

ALLOWED_ACTIONS = {"RESET_ACTIVE_BINDING", "RECOVER_BINDING"}
BINDING_FINGERPRINT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
ACCOUNT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
ALLOWED_PLATFORMS = {"ios", "android"}
OS_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,31}$")


def validate_self_recovery_config():
    if not DEVICE_SELF_RECOVERY_ENABLED:
        from errors import AppError
        raise AppError("FEATURE_DISABLED", "Self-service device recovery is not enabled.", retryable=True)
    from errors import AppError
    if (
        APP_ENVIRONMENT not in {"dev", "uat", "prod"}
        or not DEVICE_BINDINGS_TABLE_NAME or not DEVICE_RECOVERY_CONTROL_TABLE_NAME
        or not USERS_TABLE_NAME or not DELETION_LEDGER_TABLE_NAME
        or not COGNITO_ISSUER.startswith("https://cognito-idp.")
        or not COGNITO_APP_CLIENT_ID
        or COGNITO_REQUIRED_SCOPE != "aws.cognito.signin.user.admin"
    ):
        raise AppError("SERVER_UNAVAILABLE", "Self-service recovery is not configured.")
    if (
        DEVICE_RECOVERY_POLICY_STATUS != "approved"
        or DEVICE_RECOVERY_MAX_REAUTH_AGE_SECONDS != 300
        or DEVICE_RECOVERY_RATE_WINDOW_SECONDS != 3600
        or DEVICE_RECOVERY_RATE_MAX_REQUESTS != 3
        or DEVICE_RECOVERY_RATE_STATE_TTL_SECONDS != 86400
        or DEVICE_RECOVERY_RECEIPT_RETENTION_DAYS != 7
        or DEVICE_RECOVERY_AUDIT_RETENTION_DAYS not in {30, 90}
    ):
        raise AppError("SERVER_UNAVAILABLE", "The self-service recovery policy is not approved.")
