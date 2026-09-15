import os
import re

DEVICE_BINDINGS_TABLE_NAME = os.environ.get("DEVICE_BINDINGS_TABLE_NAME") or os.environ.get("TABLE_NAME")
DEVICE_BINDINGS_INACTIVE_RETENTION_DAYS = int(os.environ.get("DEVICE_BINDINGS_INACTIVE_RETENTION_DAYS", "180"))
USERS_TABLE_NAME = os.environ.get("USERS_TABLE_NAME", "")
DELETION_LEDGER_TABLE_NAME = os.environ.get("DELETION_LEDGER_TABLE_NAME", "")
COGNITO_ISSUER = os.environ.get("COGNITO_ISSUER", "")
COGNITO_APP_CLIENT_ID = os.environ.get("COGNITO_APP_CLIENT_ID", "")
COGNITO_REQUIRED_SCOPE = os.environ.get(
    "COGNITO_REQUIRED_SCOPE", "aws.cognito.signin.user.admin"
)

ALLOWED_PLATFORMS = {"ios", "android"}
BINDING_FINGERPRINT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
OS_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,31}$")
