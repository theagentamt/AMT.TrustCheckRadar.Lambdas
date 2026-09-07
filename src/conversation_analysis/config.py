import os
import re

SCHEMA_VERSION = "1.0"
ALLOWED_SOURCE_TYPES = {"ocr", "pasted_text", "mixed"}
ALLOWED_ENTITY_TYPES = {
    "email",
    "phone",
    "url",
    "credit_card",
    "ssn",
    "ip_address",
    "messenger_handle",
    "payment_handle",
    "crypto_wallet",
}
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
DEVICE_BINDING_FINGERPRINT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
MAX_SANITIZED_TEXT_LENGTH = 8000
MAX_ENTITIES = 100
MAX_REQUEST_BODY_BYTES = 64 * 1024
ANALYSIS_ABUSE_TABLE_NAME = os.environ.get("ANALYSIS_ABUSE_TABLE_NAME") or os.environ.get("TABLE_NAME") or os.environ.get("USERS_TABLE_NAME")
DEVICE_BINDINGS_TABLE_NAME = os.environ.get("DEVICE_BINDINGS_TABLE_NAME")
ENTITLEMENTS_TABLE_NAME = os.environ.get("ENTITLEMENTS_TABLE_NAME") or os.environ.get("USERS_TABLE_NAME") or os.environ.get("TABLE_NAME")
USERS_TABLE_NAME = os.environ.get("USERS_TABLE_NAME") or ENTITLEMENTS_TABLE_NAME
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "300"))
RATE_LIMIT_MAX_REQUESTS = int(os.environ.get("RATE_LIMIT_MAX_REQUESTS", "10"))
REQUEST_ID_TTL_SECONDS = int(os.environ.get("REQUEST_ID_TTL_SECONDS", "900"))
PROCESSING_LEASE_SECONDS = int(os.environ.get("PROCESSING_LEASE_SECONDS", "60"))
SCAN_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("SCAN_RATE_LIMIT_WINDOW_SECONDS", "60"))
SCAN_RATE_LIMIT_MAX_REQUESTS = int(os.environ.get("SCAN_RATE_LIMIT_MAX_REQUESTS", "10"))
FREE_MONTHLY_SCAN_LIMIT = int(os.environ.get("FREE_MONTHLY_SCAN_LIMIT", "10"))
PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT = int(os.environ.get("PARTICIPATING_FREE_MONTHLY_SCAN_LIMIT", "15"))
PRO_MONTHLY_SCAN_LIMIT = int(os.environ.get("PRO_MONTHLY_SCAN_LIMIT", "100"))
APP_ENVIRONMENT = os.environ.get("APP_ENVIRONMENT", "")
CAMPAIGN_OUTBOX_TABLE_NAME = os.environ.get("CAMPAIGN_OUTBOX_TABLE_NAME", "")
CAMPAIGN_SCHEMA_VERSION = int(os.environ.get("CAMPAIGN_SCHEMA_VERSION", "1"))
CAMPAIGN_OBSERVATION_RETENTION_HOURS = int(os.environ.get("OBSERVATION_RETENTION_HOURS", "72"))

OPENAI_SECRET_NAME = os.environ.get("OPENAI_SECRET_NAME")
OPENAI_SECRET_FIELD = os.environ.get("OPENAI_SECRET_FIELD", "apiKey")
OPENAI_RESPONSES_ENDPOINT = os.environ.get("OPENAI_RESPONSES_ENDPOINT", "https://api.openai.com/v1/responses")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_TIMEOUT_SECONDS = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "20"))
