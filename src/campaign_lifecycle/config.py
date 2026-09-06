import os

APP_ENVIRONMENT = os.environ.get("APP_ENVIRONMENT", "")
CAMPAIGN_SCHEMA_VERSION = int(os.environ.get("CAMPAIGN_SCHEMA_VERSION", "1"))
PIPELINE_TABLE_NAME = os.environ.get("PIPELINE_TABLE_NAME", "")
INTELLIGENCE_TABLE_NAME = os.environ.get("INTELLIGENCE_TABLE_NAME", "")
CONTRIBUTOR_RECOVERY_DAYS = int(os.environ.get("CONTRIBUTOR_RECOVERY_DAYS", "7"))
AGGREGATE_RETENTION_DAYS = int(os.environ.get("AGGREGATE_RETENTION_DAYS", "400"))
MIN_CONTRIBUTOR_COUNT = int(os.environ.get("MIN_CONTRIBUTOR_COUNT", "10"))
PROJECT_NAME = os.environ.get("PROJECT_NAME", "trustcheckradar")


def validate_config():
    if APP_ENVIRONMENT not in {"dev", "uat", "prod"} or CAMPAIGN_SCHEMA_VERSION != 1:
        raise RuntimeError("Invalid campaign environment or schema")
    if not PIPELINE_TABLE_NAME or not INTELLIGENCE_TABLE_NAME:
        raise RuntimeError("Campaign table names are required")
    if CONTRIBUTOR_RECOVERY_DAYS != 7 or AGGREGATE_RETENTION_DAYS > 400 or MIN_CONTRIBUTOR_COUNT != 10:
        raise RuntimeError("Campaign lifecycle bounds do not match V1")
