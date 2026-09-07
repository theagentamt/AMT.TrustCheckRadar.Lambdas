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
ENTITLEMENTS_TABLE_NAME = _table_name("ENTITLEMENTS_TABLE_NAME", "ENTITLEMENTS_TABLE_ARN")
DELETION_LEDGER_TABLE_NAME = _table_name("DELETION_LEDGER_TABLE_NAME", "DELETION_LEDGER_TABLE_ARN")
ENVIRONMENT = (os.environ.get("ENVIRONMENT") or os.environ.get("APP_ENVIRONMENT") or "").strip()
NOTICE_VERSION = os.environ.get("CAMPAIGN_PARTICIPATION_NOTICE_VERSION", "").strip()
POLICY_VERSION = os.environ.get("CAMPAIGN_PARTICIPATION_POLICY_VERSION", "").strip()
AUDIT_RETENTION_DAYS = int(os.environ.get("CAMPAIGN_PARTICIPATION_AUDIT_RETENTION_DAYS", "400"))
DELETION_SLA_HOURS = int(os.environ.get("CAMPAIGN_PARTICIPATION_DELETION_SLA_HOURS", "24"))
FREE_MONTHLY_SCAN_LIMIT = int(os.environ.get("FREE_MONTHLY_SCAN_LIMIT", "10"))
PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT = int(os.environ.get("PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT", "15"))
PRO_MONTHLY_SCAN_LIMIT = int(os.environ.get("PRO_MONTHLY_SCAN_LIMIT", "100"))


def validate_config() -> None:
    if not USERS_TABLE_NAME or not ENTITLEMENTS_TABLE_NAME or not DELETION_LEDGER_TABLE_NAME:
        raise RuntimeError("Campaign participation tables are not configured")
    if ENVIRONMENT not in {"dev", "uat", "prod"}:
        raise RuntimeError("Campaign participation environment is invalid")
    if (
        not NOTICE_VERSION
        or len(NOTICE_VERSION.encode("utf-8")) > 64
        or POLICY_VERSION != "policy-1"
    ):
        raise RuntimeError("Campaign participation policy versions are not configured")
    if AUDIT_RETENTION_DAYS != 400 or DELETION_SLA_HOURS != 24:
        raise RuntimeError("Campaign participation retention policy is invalid")
    if FREE_MONTHLY_SCAN_LIMIT < 1:
        raise RuntimeError("Base free quota must be positive")
    if PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT <= FREE_MONTHLY_SCAN_LIMIT:
        raise RuntimeError("Participating free quota must exceed the base free quota")
    if PRO_MONTHLY_SCAN_LIMIT <= PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT:
        raise RuntimeError("Pro quota must exceed the participating free quota")
