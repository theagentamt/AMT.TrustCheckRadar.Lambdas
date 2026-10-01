import os
import re

APP_ENVIRONMENT = os.environ.get("APP_ENVIRONMENT", "")
CAMPAIGN_SCHEMA_VERSION = int(os.environ.get("CAMPAIGN_SCHEMA_VERSION", "1"))
PIPELINE_TABLE_NAME = os.environ.get("PIPELINE_TABLE_NAME", "")
EXPIRATION_INDEX_NAME = os.environ.get("EXPIRATION_INDEX_NAME", "")
INTELLIGENCE_TABLE_NAME = os.environ.get("INTELLIGENCE_TABLE_NAME", "")
CONTRIBUTOR_RECOVERY_DAYS = int(os.environ.get("CONTRIBUTOR_RECOVERY_DAYS", "7"))
AGGREGATE_RETENTION_DAYS = int(os.environ.get("AGGREGATE_RETENTION_DAYS", "400"))
MIN_CONTRIBUTOR_COUNT = int(os.environ.get("MIN_CONTRIBUTOR_COUNT", "10"))
PROJECT_NAME = os.environ.get("PROJECT_NAME", "trustcheckradar")


def validate_config():
    if APP_ENVIRONMENT not in {"dev", "uat", "prod"} or CAMPAIGN_SCHEMA_VERSION != 1:
        raise RuntimeError("Invalid campaign environment or schema")
    if not PIPELINE_TABLE_NAME or not INTELLIGENCE_TABLE_NAME or EXPIRATION_INDEX_NAME != "ExpirationIndex":
        raise RuntimeError("Campaign table and expiration-index contracts are required")
    if CONTRIBUTOR_RECOVERY_DAYS != 7 or AGGREGATE_RETENTION_DAYS > 400 or MIN_CONTRIBUTOR_COUNT != 10:
        raise RuntimeError("Campaign lifecycle bounds do not match V1")


# This upgraded candidate never falls back to legacy mutation routines.
CAMPAIGN_LIFECYCLE_CANDIDATE_ENABLED = os.environ.get("CAMPAIGN_LIFECYCLE_CANDIDATE_ENABLED", "false") == "true"
CAMPAIGN_LOCATOR_MANIFEST_SHA256 = os.environ.get("CAMPAIGN_LOCATOR_MANIFEST_SHA256", "")
CAMPAIGN_LOCATOR_INVENTORY_REVISION = os.environ.get("CAMPAIGN_LOCATOR_INVENTORY_REVISION", "0")


def validate_candidate():
    validate_config()
    if not CAMPAIGN_LIFECYCLE_CANDIDATE_ENABLED:
        raise RuntimeError("Campaign lifecycle candidate is disabled")
    if (re.fullmatch(r"[0-9a-f]{64}", CAMPAIGN_LOCATOR_MANIFEST_SHA256) is None
            or re.fullmatch(r"[1-9][0-9]*", CAMPAIGN_LOCATOR_INVENTORY_REVISION) is None):
        raise RuntimeError("Campaign locator inventory pins are required")
