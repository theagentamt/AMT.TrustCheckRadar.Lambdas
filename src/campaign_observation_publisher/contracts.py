from __future__ import annotations

from decimal import Decimal
import re
from uuid import UUID

from shared_campaign_contracts import AppFeaturesContractError, validate_app_features


class ContractError(ValueError):
    """Raised when an outbox record violates the campaign contract."""


OUTBOX_EVENT_TYPE = "campaign.observation.ready"
CLUSTER_EVENT_TYPE = "campaign.cluster.requested"

REQUIRED_FIELDS = {
    "PK",
    "SK",
    "schemaVersion",
    "recordVersion",
    "eventType",
    "environment",
    "statisticsEventId",
    "accountId",
    "campaignConsentGranted",
    "observedAtEpoch",
    "sourceType",
    "sanitizedText",
    "riskLevel",
    "signalIds",
    "appFeatures",
    "expiresAt",
}

PROHIBITED_FIELD_NAMES = {
    "requestid",
    "deviceid",
    "devicefingerprint",
    "ipaddress",
    "email",
    "phonenumber",
    "cognitosub",
    "screenshot",
    "ocrtext",
    "rawtext",
    "originaltext",
    "originalmessage",
}

SOURCE_TYPES = {"ocr", "pasted_text", "mixed"}
RISK_LEVELS = {"low", "medium", "high", "unknown"}
SIGNAL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
EMAIL_PATTERN = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+?\d[\s().-]*){7,15}(?!\w)")


def parse_stream_record(record: dict, *, environment: str, schema_version: int) -> dict | None:
    if record.get("eventName") != "INSERT":
        return None
    image = record.get("dynamodb", {}).get("NewImage")
    if not isinstance(image, dict):
        raise ContractError("DynamoDB INSERT record must contain NewImage")
    item = deserialize_item(image)
    _validate_item(item, environment=environment, schema_version=schema_version)
    return item


def deserialize_item(image: dict) -> dict:
    return {key: _deserialize_value(value) for key, value in image.items()}


def build_cluster_envelope(item: dict) -> dict:
    return {
        "schemaVersion": item["schemaVersion"],
        "eventType": CLUSTER_EVENT_TYPE,
        "environment": item["environment"],
        "statisticsEventId": item["statisticsEventId"],
        "recordVersion": item["recordVersion"],
    }


def _validate_item(item: dict, *, environment: str, schema_version: int) -> None:
    fields = set(item)
    missing = REQUIRED_FIELDS - fields
    extra = fields - REQUIRED_FIELDS
    if missing:
        raise ContractError("Outbox record is missing required fields")
    if extra:
        raise ContractError("Outbox record contains unknown fields")
    if item["schemaVersion"] != schema_version or item["recordVersion"] != 1:
        raise ContractError("Outbox record uses an unsupported version")
    if item["eventType"] != OUTBOX_EVENT_TYPE:
        raise ContractError("Outbox record uses an unsupported event type")
    if item["environment"] != environment:
        raise ContractError("Outbox record belongs to another environment")
    if item["PK"] != f"EVENT#{item['statisticsEventId']}" or item["SK"] != "OBSERVATION_READY":
        raise ContractError("Outbox record key does not match the event")
    _require_uuid4(item["statisticsEventId"])
    _require_string(item["accountId"], "accountId", maximum=256)
    if not isinstance(item["campaignConsentGranted"], bool):
        raise ContractError("campaignConsentGranted must be boolean")
    _require_integer(item["observedAtEpoch"], "observedAtEpoch", minimum=0)
    _require_integer(item["expiresAt"], "expiresAt", minimum=0)
    if item["sourceType"] not in SOURCE_TYPES:
        raise ContractError("sourceType is unsupported")
    _require_string(item["sanitizedText"], "sanitizedText", maximum=12_000)
    if EMAIL_PATTERN.search(item["sanitizedText"]) or PHONE_PATTERN.search(item["sanitizedText"]):
        raise ContractError("sanitizedText contains a prohibited direct identifier")
    if item["riskLevel"] not in RISK_LEVELS:
        raise ContractError("riskLevel is unsupported")
    if not isinstance(item["signalIds"], list) or len(item["signalIds"]) > 32:
        raise ContractError("signalIds must be a bounded list")
    for signal_id in item["signalIds"]:
        _require_string(signal_id, "signalIds", maximum=64)
        if not SIGNAL_ID_PATTERN.fullmatch(signal_id):
            raise ContractError("signalIds contains an invalid identifier")
    try:
        item["appFeatures"] = validate_app_features(item["appFeatures"])
    except AppFeaturesContractError as err:
        raise ContractError("appFeatures violates the V1 contract") from err
    _reject_prohibited_fields(item)


def _reject_prohibited_fields(value) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = "".join(character for character in key.lower() if character.isalnum())
            if normalized in PROHIBITED_FIELD_NAMES:
                raise ContractError("Outbox record contains a prohibited field")
            _reject_prohibited_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_prohibited_fields(child)


def _require_uuid4(value) -> None:
    try:
        parsed = UUID(str(value))
    except (ValueError, AttributeError, TypeError) as err:
        raise ContractError("statisticsEventId must be a UUIDv4") from err
    if parsed.version != 4 or str(parsed) != value:
        raise ContractError("statisticsEventId must be a canonical UUIDv4")


def _require_string(value, field: str, *, maximum: int) -> None:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ContractError(f"{field} must be a bounded non-empty string")


def _require_integer(value, field: str, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(f"{field} must be an integer")


def _deserialize_value(value):
    if not isinstance(value, dict) or len(value) != 1:
        raise ContractError("Invalid DynamoDB attribute value")
    kind, raw = next(iter(value.items()))
    if kind == "S" and isinstance(raw, str):
        return raw
    if kind == "N" and isinstance(raw, str):
        number = Decimal(raw)
        return int(number) if number == number.to_integral_value() else number
    if kind == "BOOL" and isinstance(raw, bool):
        return raw
    if kind == "NULL" and raw is True:
        return None
    if kind == "L" and isinstance(raw, list):
        return [_deserialize_value(item) for item in raw]
    if kind == "M" and isinstance(raw, dict):
        return deserialize_item(raw)
    raise ContractError("Unsupported DynamoDB attribute value")
