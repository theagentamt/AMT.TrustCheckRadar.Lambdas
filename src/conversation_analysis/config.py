import os

SCHEMA_VERSION = "1.0"
ALLOWED_SOURCE_TYPES = {"text", "chat", "sms", "email", "mixed"}
MAX_SANITIZED_TEXT_LENGTH = 8000
MAX_ENTITIES = 100
MAX_REQUEST_BODY_BYTES = 64 * 1024

OPENAI_SECRET_NAME = os.environ.get("OPENAI_SECRET_NAME")
OPENAI_SECRET_FIELD = os.environ.get("OPENAI_SECRET_FIELD", "apiKey")
OPENAI_RESPONSES_ENDPOINT = os.environ.get("OPENAI_RESPONSES_ENDPOINT", "https://api.openai.com/v1/responses")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_TIMEOUT_SECONDS = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "20"))
