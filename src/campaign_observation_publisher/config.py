import os


APP_ENVIRONMENT = os.environ.get("APP_ENVIRONMENT", "")
CAMPAIGN_SCHEMA_VERSION = int(os.environ.get("CAMPAIGN_SCHEMA_VERSION", "1"))
PIPELINE_TABLE_NAME = os.environ.get("PIPELINE_TABLE_NAME", "")
FEATURE_QUEUE_URL = os.environ.get("FEATURE_QUEUE_URL", "")
OBSERVATION_RETENTION_HOURS = int(os.environ.get("OBSERVATION_RETENTION_HOURS", "72"))
TRANSIENT_RETENTION_DAYS = int(os.environ.get("TRANSIENT_RETENTION_DAYS", "21"))
CONTRIBUTOR_PERIOD_DAYS = int(os.environ.get("CONTRIBUTOR_PERIOD_DAYS", "14"))


def validate_config() -> None:
    if APP_ENVIRONMENT not in {"dev", "uat", "prod"}:
        raise RuntimeError("APP_ENVIRONMENT must be dev, uat, or prod")
    if CAMPAIGN_SCHEMA_VERSION != 1:
        raise RuntimeError("Only campaign schema version 1 is supported")
    if not PIPELINE_TABLE_NAME:
        raise RuntimeError("PIPELINE_TABLE_NAME is required")
    if not FEATURE_QUEUE_URL:
        raise RuntimeError("FEATURE_QUEUE_URL is required")
    if not 1 <= OBSERVATION_RETENTION_HOURS <= 72:
        raise RuntimeError("OBSERVATION_RETENTION_HOURS must be between 1 and 72")
    if not 1 <= TRANSIENT_RETENTION_DAYS <= 21:
        raise RuntimeError("TRANSIENT_RETENTION_DAYS must be between 1 and 21")
    if CONTRIBUTOR_PERIOD_DAYS != 14:
        raise RuntimeError("CONTRIBUTOR_PERIOD_DAYS must be 14")
