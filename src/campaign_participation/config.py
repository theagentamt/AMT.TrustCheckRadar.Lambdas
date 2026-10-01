import os


def _table_name(name_key: str, arn_key: str) -> str:
    name = os.environ.get(name_key, "").strip()
    if name:
        return name
    arn = os.environ.get(arn_key, "").strip()
    marker = ":table/"
    if marker in arn:
        return arn.split(marker, 1)[1].split("/", 1)[0]
    return ""


USERS_TABLE_NAME = _table_name("USERS_TABLE_NAME", "USERS_TABLE_ARN")
DELETION_LEDGER_TABLE_NAME = _table_name("DELETION_LEDGER_TABLE_NAME", "DELETION_LEDGER_TABLE_ARN")
ENVIRONMENT = (os.environ.get("ENVIRONMENT") or os.environ.get("APP_ENVIRONMENT") or "").strip()
NOTICE_VERSION = os.environ.get("CAMPAIGN_PARTICIPATION_NOTICE_VERSION", "").strip()
POLICY_VERSION = os.environ.get("CAMPAIGN_PARTICIPATION_POLICY_VERSION", "").strip()
AUDIT_RETENTION_DAYS = int(os.environ.get("CAMPAIGN_PARTICIPATION_AUDIT_RETENTION_DAYS", "400"))
DELETION_SLA_HOURS = int(os.environ.get("CAMPAIGN_PARTICIPATION_DELETION_SLA_HOURS", "24"))
CONSENT_INDEPENDENCE_ENABLED = os.environ.get("CONSENT_INDEPENDENCE_ENABLED", "false") == "true"
CURRENT_NOTICE = "research-consent-2026-09-21-v2"
CURRENT_POLICY = "independent-research-v1"


def validate_config() -> None:
    if not USERS_TABLE_NAME or not DELETION_LEDGER_TABLE_NAME:
        raise RuntimeError("Campaign participation tables are not configured")
    if ENVIRONMENT not in {"dev", "uat", "prod"}:
        raise RuntimeError("Campaign participation environment is invalid")
    if NOTICE_VERSION != CURRENT_NOTICE or POLICY_VERSION != CURRENT_POLICY:
        raise RuntimeError("Campaign participation policy versions are not configured")
    if AUDIT_RETENTION_DAYS != 400 or DELETION_SLA_HOURS != 24:
        raise RuntimeError("Campaign participation retention policy is invalid")

CAMPAIGN_RECOVERY_WRITES_ENABLED = os.environ.get("CAMPAIGN_RECOVERY_WRITES_ENABLED", "false") == "true"
