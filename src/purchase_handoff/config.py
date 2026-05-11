import os
import re

ENTITLEMENTS_TABLE_NAME = os.environ.get("ENTITLEMENTS_TABLE_NAME") or os.environ.get("TABLE_NAME")
VERIFICATION_MODE = os.environ.get("PURCHASE_VERIFICATION_MODE", "stub")

ALLOWED_PLATFORMS = {"ios", "android"}
ALLOWED_PURCHASE_STATES = {"purchased", "pending", "failed", "restored"}
SUPPORTED_PRODUCTS = {
    "pro_monthly": {
        "productType": "subscription",
        "entitlementTier": "PRO",
        "monthlyScanLimit": 100,
        "creditAmount": 0,
    },
    "credits_20": {
        "productType": "consumable",
        "entitlementTier": "FREE",
        "monthlyScanLimit": 5,
        "creditAmount": 20,
    },
}

STRING_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")
