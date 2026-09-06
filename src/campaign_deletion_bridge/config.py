import os

APP_ENVIRONMENT = os.environ.get("APP_ENVIRONMENT", "")
CAMPAIGN_SCHEMA_VERSION = int(os.environ.get("CAMPAIGN_SCHEMA_VERSION", "1"))
PIPELINE_TABLE_NAME = os.environ.get("PIPELINE_TABLE_NAME", "")
CONTRIBUTOR_RECOVERY_DAYS = int(os.environ.get("CONTRIBUTOR_RECOVERY_DAYS", "7"))
TRANSIENT_RETENTION_DAYS = int(os.environ.get("TRANSIENT_RETENTION_DAYS", "21"))


def validate_config():
    if APP_ENVIRONMENT not in {"dev", "uat", "prod"} or CAMPAIGN_SCHEMA_VERSION != 1:
        raise RuntimeError("Invalid campaign environment or schema")
    if not PIPELINE_TABLE_NAME or CONTRIBUTOR_RECOVERY_DAYS != 7 or TRANSIENT_RETENTION_DAYS > 21:
        raise RuntimeError("Invalid campaign deletion configuration")
