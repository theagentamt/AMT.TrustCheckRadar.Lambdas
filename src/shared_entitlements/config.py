import os


def _table_name(name_key: str, arn_key: str) -> str | None:
    name = os.environ.get(name_key)
    if name:
        return name
    arn = os.environ.get(arn_key, "")
    if ":table/" in arn:
        return arn.split(":table/", 1)[1].split("/", 1)[0]
    return None


ENTITLEMENTS_TABLE_NAME = _table_name("ENTITLEMENTS_TABLE_NAME", "ENTITLEMENTS_TABLE_ARN") or os.environ.get("USERS_TABLE_NAME") or os.environ.get("TABLE_NAME")
USERS_TABLE_NAME = _table_name("USERS_TABLE_NAME", "USERS_TABLE_ARN") or ENTITLEMENTS_TABLE_NAME
ENVIRONMENT = os.environ.get("ENVIRONMENT") or os.environ.get("APP_ENVIRONMENT") or ""
FREE_MONTHLY_SCAN_LIMIT = int(os.environ.get("FREE_MONTHLY_SCAN_LIMIT", "10"))
PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT = int(os.environ.get("PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT", "15"))
PRO_MONTHLY_SCAN_LIMIT = int(os.environ.get("PRO_MONTHLY_SCAN_LIMIT", "100"))
SUPPORTED_SUBSCRIPTION_STATUSES = {
    "active",
    "expired",
    "grace",
    "hold",
    "paused",
    "canceled",
    "pending",
}
