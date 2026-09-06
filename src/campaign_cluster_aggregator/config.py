import os

APP_ENVIRONMENT = os.environ.get("APP_ENVIRONMENT", "")
CAMPAIGN_SCHEMA_VERSION = int(os.environ.get("CAMPAIGN_SCHEMA_VERSION", "1"))
PIPELINE_TABLE_NAME = os.environ.get("PIPELINE_TABLE_NAME", "")
TRANSIENT_RETENTION_DAYS = int(os.environ.get("TRANSIENT_RETENTION_DAYS", "21"))
MIN_CONTRIBUTOR_COUNT = int(os.environ.get("MIN_CONTRIBUTOR_COUNT", "10"))
MAX_CONTRIBUTOR_SUBMISSIONS = int(os.environ.get("MAX_CONTRIBUTOR_SUBMISSIONS", "3"))


def validate_config():
    if APP_ENVIRONMENT not in {"dev", "uat", "prod"} or CAMPAIGN_SCHEMA_VERSION != 1:
        raise RuntimeError("Invalid campaign environment or schema")
    if not PIPELINE_TABLE_NAME:
        raise RuntimeError("PIPELINE_TABLE_NAME is required")
    if TRANSIENT_RETENTION_DAYS > 21 or MIN_CONTRIBUTOR_COUNT != 10 or MAX_CONTRIBUTOR_SUBMISSIONS != 3:
        raise RuntimeError("Campaign privacy bounds do not match V1")
