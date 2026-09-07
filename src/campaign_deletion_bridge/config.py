import os

APP_ENVIRONMENT = os.environ.get("APP_ENVIRONMENT") or os.environ.get("ENVIRONMENT", "")
CAMPAIGN_SCHEMA_VERSION = int(os.environ.get("CAMPAIGN_SCHEMA_VERSION", "1"))
PIPELINE_TABLE_NAME = os.environ.get("PIPELINE_TABLE_NAME", "")
USERS_TABLE_NAME = os.environ.get("USERS_TABLE_NAME", "")
DELETION_LEDGER_TABLE_NAME = os.environ.get("DELETION_LEDGER_TABLE_NAME", "")
PARTICIPATION_ITEM_SK = os.environ.get("PARTICIPATION_ITEM_SK", "CAMPAIGN_PARTICIPATION")
PARTICIPATION_AUDIT_DAYS = int(os.environ.get("PARTICIPATION_AUDIT_DAYS", "400"))
CONTRIBUTOR_RECOVERY_DAYS = int(os.environ.get("CONTRIBUTOR_RECOVERY_DAYS", "7"))
TRANSIENT_RETENTION_DAYS = int(os.environ.get("TRANSIENT_RETENTION_DAYS", "21"))


def validate_config():
    if APP_ENVIRONMENT not in {"dev", "uat", "prod"} or CAMPAIGN_SCHEMA_VERSION != 1:
        raise RuntimeError("Invalid campaign environment or schema")
    if not PIPELINE_TABLE_NAME or not USERS_TABLE_NAME or not DELETION_LEDGER_TABLE_NAME:
        raise RuntimeError("Invalid campaign deletion storage configuration")
    if PARTICIPATION_ITEM_SK != "CAMPAIGN_PARTICIPATION" or PARTICIPATION_AUDIT_DAYS != 400:
        raise RuntimeError("Invalid campaign participation completion policy")
    if CONTRIBUTOR_RECOVERY_DAYS != 7 or TRANSIENT_RETENTION_DAYS > 21:
        raise RuntimeError("Invalid campaign deletion configuration")
