import os


APP_ENVIRONMENT = os.environ.get("APP_ENVIRONMENT", "")
HISTORY_CONTROL_TABLE_NAME = os.environ.get("HISTORY_CONTROL_TABLE_NAME", "")
DELETION_LEDGER_TABLE_NAME = os.environ.get("DELETION_LEDGER_TABLE_NAME", "")
HISTORY_SCHEMA_VERSION = int(os.environ.get("HISTORY_SCHEMA_VERSION", "1"))
HISTORY_ERASURE_SLA_HOURS = int(os.environ.get("HISTORY_ERASURE_SLA_HOURS", "24"))
HISTORY_ACCOUNT_DELETION_ENABLED = (
    os.environ.get("HISTORY_ACCOUNT_DELETION_ENABLED", "false").strip().lower() == "true"
)


def validate_config():
    if not HISTORY_ACCOUNT_DELETION_ENABLED:
        raise RuntimeError("History account deletion is not enabled")
    if APP_ENVIRONMENT not in {"dev", "uat", "prod"}:
        raise RuntimeError("Invalid History account-deletion environment")
    if not HISTORY_CONTROL_TABLE_NAME or not DELETION_LEDGER_TABLE_NAME:
        raise RuntimeError("Invalid History account-deletion storage configuration")
    if HISTORY_SCHEMA_VERSION != 1 or HISTORY_ERASURE_SLA_HOURS != 24:
        raise RuntimeError("Invalid History account-deletion policy")
