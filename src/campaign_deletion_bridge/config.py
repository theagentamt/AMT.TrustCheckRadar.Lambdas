import os
import re

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
    if CONTRIBUTOR_RECOVERY_DAYS != 7 or not 1 <= TRANSIENT_RETENTION_DAYS <= 21:
        raise RuntimeError("Invalid campaign deletion configuration")
    if CAMPAIGN_COMPLETION_ENABLED:
        for manifest,revision in ((CAMPAIGN_COMPLETION_MANIFEST_SHA256,CAMPAIGN_COMPLETION_INVENTORY_REVISION),
            (CAMPAIGN_LOCATOR_MANIFEST_SHA256,CAMPAIGN_LOCATOR_INVENTORY_REVISION),
            (CAMPAIGN_RECOVERY_MANIFEST_SHA256,CAMPAIGN_RECOVERY_INVENTORY_REVISION)):
            if not isinstance(manifest,str) or not re.fullmatch('[0-9a-f]{64}',manifest) or type(revision) is not int or revision<1:
                raise RuntimeError("Invalid campaign completion qualification pins")

CAMPAIGN_LOCATOR_MANIFEST_SHA256 = os.environ.get("CAMPAIGN_LOCATOR_MANIFEST_SHA256", "")
CAMPAIGN_LOCATOR_INVENTORY_REVISION = int(os.environ.get("CAMPAIGN_LOCATOR_INVENTORY_REVISION", "0") or "0")

CAMPAIGN_RECOVERY_ENABLED = os.environ.get("CAMPAIGN_RECOVERY_ENABLED", "false") == "true"
CAMPAIGN_RECOVERY_INDEX_NAME = os.environ.get("CAMPAIGN_RECOVERY_INDEX_NAME", "CampaignRecoveryDueIndex")
CAMPAIGN_RECOVERY_MANIFEST_SHA256 = os.environ.get("CAMPAIGN_RECOVERY_MANIFEST_SHA256", "")
CAMPAIGN_RECOVERY_INVENTORY_REVISION = int(os.environ.get("CAMPAIGN_RECOVERY_INVENTORY_REVISION", "0") or "0")

CAMPAIGN_DELETION_STREAM_ENABLED = os.environ.get("CAMPAIGN_DELETION_STREAM_ENABLED", "false") == "true"
CAMPAIGN_COMPLETION_ENABLED = os.environ.get("CAMPAIGN_COMPLETION_ENABLED", "false") == "true"
CAMPAIGN_COMPLETION_MANIFEST_SHA256 = os.environ.get("CAMPAIGN_COMPLETION_MANIFEST_SHA256", "")
CAMPAIGN_COMPLETION_INVENTORY_REVISION = int(os.environ.get("CAMPAIGN_COMPLETION_INVENTORY_REVISION", "0") or "0")

INTELLIGENCE_TABLE_NAME = os.environ.get('INTELLIGENCE_TABLE_NAME','')
