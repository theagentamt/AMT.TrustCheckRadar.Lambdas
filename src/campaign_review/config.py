import os

APP_ENVIRONMENT = os.environ.get("APP_ENVIRONMENT", "")
CAMPAIGN_SCHEMA_VERSION = int(os.environ.get("CAMPAIGN_SCHEMA_VERSION", "1"))
INTELLIGENCE_TABLE_NAME = os.environ.get("INTELLIGENCE_TABLE_NAME", "")
REVIEWER_GROUP = os.environ.get("REVIEWER_GROUP", "campaign-reviewer")
MIN_CONTRIBUTOR_COUNT = int(os.environ.get("MIN_CONTRIBUTOR_COUNT", "10"))


def validate_config():
    if APP_ENVIRONMENT not in {"dev", "uat", "prod"} or CAMPAIGN_SCHEMA_VERSION != 1:
        raise RuntimeError("Invalid campaign environment or schema")
    if not INTELLIGENCE_TABLE_NAME or not REVIEWER_GROUP or MIN_CONTRIBUTOR_COUNT != 10:
        raise RuntimeError("Invalid campaign review configuration")
