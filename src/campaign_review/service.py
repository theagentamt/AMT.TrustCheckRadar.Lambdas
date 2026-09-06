from __future__ import annotations

import json
import re
import uuid

CAMPAIGN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
REASON_CODES = {"quality_verified", "privacy_verified", "policy_violation", "false_positive", "emergency"}
TRANSITIONS = {
    ("PENDING_REVIEW", "confirm"): "CONFIRMED",
    ("PENDING_REVIEW", "suppress"): "SUPPRESSED",
    ("CONFIRMED", "publish"): "PUBLISHED",
    ("CONFIRMED", "suppress"): "SUPPRESSED",
    ("PUBLISHED", "emergency_suppress"): "SUPPRESSED",
}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?:\+?\d[\s().-]*){7,15}")


class ReviewError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message); self.status, self.code, self.message = status, code, message


def review(event, *, table_name, reviewer_group, minimum_contributors, dynamodb):
    _authorize(event, reviewer_group)
    campaign_id = (event.get("pathParameters") or {}).get("campaignId")
    if not isinstance(campaign_id, str) or not CAMPAIGN_ID.fullmatch(campaign_id):
        raise ReviewError(400, "INVALID_REQUEST", "campaignId is invalid")
    try: payload = json.loads(event.get("body") or "")
    except json.JSONDecodeError as err: raise ReviewError(400, "INVALID_REQUEST", "Body must be JSON") from err
    if not isinstance(payload, dict) or set(payload) != {"schemaVersion", "action", "reasonCode", "reason"}:
        raise ReviewError(400, "INVALID_REQUEST", "Review fields are invalid")
    if payload["schemaVersion"] != 1 or payload["reasonCode"] not in REASON_CODES:
        raise ReviewError(400, "INVALID_REQUEST", "Review version or reason code is invalid")
    reason = payload["reason"]
    if not isinstance(reason, str) or not 8 <= len(reason) <= 256 or EMAIL.search(reason) or PHONE.search(reason):
        raise ReviewError(400, "INVALID_REQUEST", "Reason must be bounded and privacy-safe")
    if payload["action"] in {"merge", "split"}:
        raise ReviewError(409, "ACTION_REQUIRES_SOURCE_LEDGER", "Merge and split require an approved overlap-safe aggregate contract")

    key = {"PK": {"S": f"CAMPAIGN#{campaign_id}"}, "SK": {"S": "AGGREGATE"}}
    raw = dynamodb.get_item(TableName=table_name, Key=key, ConsistentRead=True).get("Item")
    if not raw: raise ReviewError(404, "NOT_FOUND", "Campaign was not found")
    current = raw.get("state", {}).get("S")
    target = TRANSITIONS.get((current, payload["action"]))
    if not target: raise ReviewError(409, "INVALID_TRANSITION", "The requested state transition is invalid")
    contributors = int(raw.get("contributorCount", {"N": "0"})["N"])
    if target in {"CONFIRMED", "PUBLISHED"} and contributors < minimum_contributors:
        raise ReviewError(409, "PRIVACY_THRESHOLD", "Campaign does not meet the privacy threshold")

    version = int(raw.get("version", {"N": "0"})["N"])
    update = {"Update": {"TableName": table_name, "Key": key,
        "UpdateExpression": "SET #state = :target, version = :next_version",
        "ConditionExpression": "#state = :current AND version = :version",
        "ExpressionAttributeNames": {"#state": "state"},
        "ExpressionAttributeValues": {":target": {"S": target}, ":current": {"S": current},
            ":version": {"N": str(version)}, ":next_version": {"N": str(version + 1)}}}}
    if target == "PUBLISHED":
        period = raw["periodWeek"]["S"]
        update["Update"]["UpdateExpression"] += ", GSI1PK = :published, GSI1SK = :publication_key"
        update["Update"]["ExpressionAttributeValues"].update({
            ":published": {"S": "STATE#PUBLISHED"},
            ":publication_key": {"S": f"{period}#CAMPAIGN#{campaign_id}"}})
    elif current == "PUBLISHED":
        update["Update"]["UpdateExpression"] += " REMOVE GSI1PK, GSI1SK"

    audit_id = str(uuid.uuid4())
    audit = {"Put": {"TableName": table_name, "Item": {
        "PK": {"S": f"CAMPAIGN#{campaign_id}"}, "SK": {"S": f"AUDIT#{audit_id}"},
        "schemaVersion": {"N": "1"}, "action": {"S": payload["action"]},
        "fromState": {"S": current}, "toState": {"S": target},
        "reasonCode": {"S": payload["reasonCode"]}, "reviewerRole": {"S": reviewer_group},
        "auditId": {"S": audit_id}, "expiresAt": raw["expiresAt"],
    }, "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)"}}
    dynamodb.transact_write_items(TransactItems=[update, audit])
    return {"schemaVersion": 1, "campaignId": campaign_id, "state": target, "version": version + 1}


def _authorize(event, group):
    claims = (((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}).get("claims") or {}
    groups = claims.get("cognito:groups", [])
    if isinstance(groups, str): groups = [part.strip() for part in groups.strip("[]").split(",")]
    if group not in groups: raise ReviewError(403, "FORBIDDEN", "Reviewer authorization is required")
