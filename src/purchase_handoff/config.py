import os
import re

ENTITLEMENTS_TABLE_NAME = os.environ.get("ENTITLEMENTS_TABLE_NAME") or os.environ.get("TABLE_NAME")
VERIFICATION_MODE = os.environ.get("PURCHASE_VERIFICATION_MODE", "google_play")
GOOGLE_PLAY_SECRET_NAME = os.environ.get("GOOGLE_PLAY_SECRET_NAME", "securityforall/dev/google-play-service-account")
GOOGLE_PLAY_PACKAGE_NAME = os.environ.get("GOOGLE_PLAY_PACKAGE_NAME")
GOOGLE_PLAY_PRO_PRODUCT_ID = os.environ.get("GOOGLE_PLAY_PRO_PRODUCT_ID", "trustcheck_radar_pro_monthly")

ALLOWED_PLATFORMS = {"google_play"}
ALLOWED_PURCHASE_STATES = {"PURCHASED", "PENDING", "CANCELED", "FAILED", "RESTORED"}
SUPPORTED_PRODUCTS = {
    GOOGLE_PLAY_PRO_PRODUCT_ID: {
        "productType": "subscription",
        "entitlementTier": "PRO",
        "monthlyScanLimit": 100,
        "creditAmount": 0,
        "platform": "google_play",
    },
}

PACKAGE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)+$")
STRING_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9\-._~+/=]{10,4096}$")
ORDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9.-]{3,255}$")
