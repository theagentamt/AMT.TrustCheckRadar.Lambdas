import os

ENTITLEMENT_PLATFORM = os.environ.get("ENTITLEMENT_PLATFORM", "google_play")
ENTITLEMENT_PRODUCT_ID = os.environ.get("ENTITLEMENT_PRODUCT_ID", "trustcheck_radar_pro_monthly")
ENTITLEMENT_USAGE_PERIOD_MODE = os.environ.get("ENTITLEMENT_USAGE_PERIOD_MODE", "billing_cycle")

