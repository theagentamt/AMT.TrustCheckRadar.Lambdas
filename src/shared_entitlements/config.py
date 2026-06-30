import os

ENTITLEMENTS_TABLE_NAME = os.environ.get("ENTITLEMENTS_TABLE_NAME") or os.environ.get("USERS_TABLE_NAME") or os.environ.get("TABLE_NAME")
FREE_MONTHLY_SCAN_LIMIT = int(os.environ.get("FREE_MONTHLY_SCAN_LIMIT", "5"))
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
