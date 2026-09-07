import os


APP_ENVIRONMENT = os.environ.get("APP_ENVIRONMENT", "")
CAMPAIGN_SCHEMA_VERSION = int(os.environ.get("CAMPAIGN_SCHEMA_VERSION", "1"))
PIPELINE_TABLE_NAME = os.environ.get("PIPELINE_TABLE_NAME", "")
CLUSTER_QUEUE_URL = os.environ.get("CLUSTER_QUEUE_URL", "")
TRANSIENT_RETENTION_DAYS = int(os.environ.get("TRANSIENT_RETENTION_DAYS", "21"))
CONTRIBUTOR_PERIOD_DAYS = int(os.environ.get("CONTRIBUTOR_PERIOD_DAYS", "14"))


def validate_config() -> None:
    if APP_ENVIRONMENT not in {"dev", "uat", "prod"}:
        raise RuntimeError("APP_ENVIRONMENT must be dev, uat, or prod")
    if CAMPAIGN_SCHEMA_VERSION != 1:
        raise RuntimeError("Only campaign schema version 1 is supported")
    if not PIPELINE_TABLE_NAME:
        raise RuntimeError("PIPELINE_TABLE_NAME is required")
    if not CLUSTER_QUEUE_URL:
        raise RuntimeError("CLUSTER_QUEUE_URL is required")
    if not 1 <= TRANSIENT_RETENTION_DAYS <= 21:
        raise RuntimeError("TRANSIENT_RETENTION_DAYS must be between 1 and 21")
    if CONTRIBUTOR_PERIOD_DAYS != 14:
        raise RuntimeError("CONTRIBUTOR_PERIOD_DAYS must be 14")
