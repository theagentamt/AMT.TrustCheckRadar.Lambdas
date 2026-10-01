import base64
import json
from uuid import UUID

from errors import AppError

AGE_BANDS = frozenset({"18_24", "25_34", "35_44", "45_54", "55_64", "65_74", "75_PLUS", "PREFER_NOT_TO_SAY"})
STATE_CODES = frozenset({
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC","OTHER_US_JURISDICTION","PREFER_NOT_TO_SAY",
})
BASE_FIELDS = {"schemaVersion", "operationId", "expectedStateVersion", "action", "noticeVersion"}


def uuid4(value):
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as err:
        raise invalid("operationId", "operationId must be a canonical UUIDv4.") from err
    if parsed.version != 4 or str(parsed) != value:
        raise invalid("operationId", "operationId must be a canonical UUIDv4.")
    return value


def parse(event, notice_version):
    body = event.get("body")
    if not isinstance(body, str):
        raise invalid("body", "A JSON request body is required.")
    try:
        raw = base64.b64decode(body, validate=True) if event.get("isBase64Encoded") is True else body.encode()
        if len(raw) > 4096:
            raise ValueError
        payload = json.loads(raw, object_pairs_hook=no_duplicates)
    except (ValueError, UnicodeError, json.JSONDecodeError) as err:
        raise invalid("body", "The request body must be valid JSON under 4096 bytes.") from err
    action = payload.get("action") if isinstance(payload, dict) else None
    expected = BASE_FIELDS | ({"ageBand", "stateCode"} if action in {"enroll", "update"} else set())
    if not isinstance(payload, dict) or set(payload) != expected:
        raise invalid("body", "Request fields do not match the demographic-research contract.")
    if type(payload["schemaVersion"]) is not int or payload["schemaVersion"] != 1:
        raise invalid("schemaVersion", "schemaVersion must be integer 1.")
    if action not in {"enroll", "update", "withdraw"}:
        raise invalid("action", "action must be enroll, update, or withdraw.")
    if type(payload["expectedStateVersion"]) is not int or not 0 <= payload["expectedStateVersion"] <= 9007199254740991:
        raise invalid("expectedStateVersion", "expectedStateVersion is invalid.")
    uuid4(payload["operationId"])
    if payload["noticeVersion"] != notice_version:
        raise AppError("CONFLICT", "Review the current demographic-research notice.")
    if action in {"enroll", "update"}:
        if payload["ageBand"] not in AGE_BANDS:
            raise invalid("ageBand", "ageBand is not supported.")
        if payload["stateCode"] not in STATE_CODES:
            raise invalid("stateCode", "stateCode is not supported.")
    return payload


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def invalid(field, issue):
    return AppError("INVALID_REQUEST", "The submitted request is invalid.", details=[{"field": field, "issue": issue}])
