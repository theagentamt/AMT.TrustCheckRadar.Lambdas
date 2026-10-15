"""Strict contract for the non-mobile complimentary-access operator route."""
import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2] / "contracts" / "v1-complimentary-operator" / "v1"


def _schema(name):
    value = json.loads((ROOT / f"{name}.schema.json").read_text())
    Draft202012Validator.check_schema(value)
    return value


def test_request_accepts_only_reviewed_grant_and_revoke_shapes():
    validator = Draft202012Validator(_schema("request"))
    grant = {
        "schemaVersion": 1,
        "operationId": "sec232-grant-1",
        "accountId": "01997e3a-0000-7000-8000-000000000001",
        "action": "grant",
        "reasonCode": "OWNER_GRANT",
        "expiresAtEpoch": None,
    }
    revoke = grant | {
        "operationId": "sec232-revoke-1",
        "action": "revoke",
        "reasonCode": "REVOKE",
    }
    validator.validate(grant)
    validator.validate(grant | {"expiresAtEpoch": 1800000000})
    validator.validate(revoke)

    rejected = [
        grant | {"operatorArn": "arn:aws:iam::107827791950:role/forged"},
        grant | {"accountId": " email@example.com "},
        grant | {"action": "revoke", "reasonCode": "OWNER_GRANT"},
        revoke | {"expiresAtEpoch": 1800000000},
        grant | {"operationId": "bad operation"},
    ]
    for value in rejected:
        assert not validator.is_valid(value)


def test_response_discloses_only_effective_access_summary():
    validator = Draft202012Validator(_schema("response"))
    response = {"schemaVersion": 1, "access": {"revision": 4, "state": "ACTIVE", "basis": "complimentary"}}
    validator.validate(response)
    for secret_field in ("accountId", "completedChecks", "purchaseToken", "auditKey"):
        changed = copy.deepcopy(response)
        changed["access"][secret_field] = "forbidden"
        assert not validator.is_valid(changed)


def test_error_contract_is_closed_and_complete_for_operator_failures():
    validator = Draft202012Validator(_schema("error"))
    allowed = _schema("error")["properties"]["error"]["properties"]["code"]["enum"]
    for code in allowed:
        validator.validate({"schemaVersion": 1, "error": {"code": code, "retryable": code == "TRANSACTION_UNCERTAIN"}})
    assert not validator.is_valid({"schemaVersion": 1, "error": {"code": "UNKNOWN", "retryable": False}})
