import hashlib
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from governed_history import app
from shared_governed_history import present_projection, validate_public_projection

CONTRACT = ROOT / "contracts/governed-history/1.0.0-candidate.1"


def validator(name):
    schema = json.loads((CONTRACT / name).read_text())
    registry = Registry()
    for path in CONTRACT.glob("*.schema.json"):
        registry = registry.with_resource(path.name, Resource.from_contents(json.loads(path.read_text())))
    return Draft202012Validator(schema, registry=registry)


def test_immutable_contract_checksums_and_fixtures_match_runtime():
    for line in (CONTRACT / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ")
        assert hashlib.sha256((CONTRACT / name).read_bytes()).hexdigest() == digest
    fixtures = json.loads((CONTRACT / "fixtures.json").read_text())
    assert fixtures["activationEnabled"] is False
    for fixture in fixtures["fixtures"]:
        name = "list.schema.json" if fixture["route"].endswith("analysis-history") else "detail.schema.json"
        validator(name).validate(fixture["response"])
        for item in fixture["response"].get("items", [fixture["response"].get("result")]):
            if item is not None:
                validate_public_projection(item)


def test_expired_message_and_url_keep_original_outcome_and_validate_public_presentation():
    fixtures = json.loads((CONTRACT / "fixtures.json").read_text())
    items = fixtures["fixtures"][0]["response"]["items"]
    for original in items:
        stored = {key: value for key, value in original.items() if key != "presentation"}
        if original["resultType"] == "message":
            presented = present_projection(stored, 1800000030)
            assert presented["outcome"]["evidence"][0]["freshness"] == "current"
            assert presented["presentation"]["freshnessStatus"] == "historical"
            assert presented["presentation"]["processingOutcome"] == "partial"
        else:
            expired = json.loads(json.dumps(stored))
            expired["outcome"].update(verdict="high_risk", processingOutcome="complete",
                                      coverage="supported_checks_complete",
                                      reasonCodes=["KNOWN_THREAT_MATCH"], threatTypes=["MALWARE"],
                                      lookupValidUntil="2027-01-15T08:00:20Z")
            expired["processingOutcome"] = "complete"
            presented = present_projection(expired, 1800000030)
            assert presented["outcome"]["verdict"] == "high_risk"
            assert presented["outcome"]["lookupValidUntil"] == "2027-01-15T08:00:20Z"
            assert presented["presentation"] == {
                "freshnessStatus": "historical", "verdict": "unknown",
                "processingOutcome": "partial", "coverage": "limited",
                "reasonCodes": ["PROVIDER_EVIDENCE_EXPIRED"],
                "messageKey": None, "nextAction": None,
            }
        validate_public_projection(presented)
        validator("item.schema.json").validate(presented)


def test_handler_is_default_off_and_never_echoes_input(monkeypatch, capsys):
    monkeypatch.delenv("GOVERNED_HISTORY_LIST_ENABLED", raising=False)
    event = {"version": "2.0", "routeKey": "GET /v1/users/analysis-history",
             "requestContext": {"http": {"method": "GET"}}, "queryStringParameters": None,
             "body": None, "isBase64Encoded": False, "sentinel": "private-user-content"}
    result = app.lambda_handler(event, None)
    assert result["statusCode"] == 503
    assert json.loads(result["body"])["error"]["code"] == "SERVICE_NOT_ENABLED"
    assert "private-user-content" not in result["body"]
    assert capsys.readouterr().out == ""


def test_handler_rejects_export_and_mutation_routes_without_aws(monkeypatch):
    monkeypatch.setenv("STAGE", "dev")
    monkeypatch.setenv("AUTHORITY_ENABLED", "true")
    for route in ("GET /v1/users/analysis-history/export", "DELETE /v1/users/analysis-history"):
        event = {"version": "2.0", "routeKey": route, "requestContext": {"http": {"method": route.split()[0]}},
                 "body": None, "isBase64Encoded": False}
        result = app.lambda_handler(event, None)
        assert result["statusCode"] == 404


def test_handler_rejects_device_generation_change_after_read(monkeypatch):
    from shared_check_authority import engineering, runtime

    class Authority:
        pass

    class Reader:
        def __init__(self, *_args, **_kwargs):
            pass

        def list(self, **_kwargs):
            return {"transportVersion": "1.0.0-governed-history-candidate.1",
                    "serverTimeEpoch": 1, "items": [], "nextCursor": None}

    for key, value in {
        "STAGE": "dev", "AUTHORITY_ENABLED": "true", "GOVERNED_HISTORY_LIST_ENABLED": "true",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(engineering, "require_engineering_subject", lambda _event: "synthetic-account")
    monkeypatch.setattr(runtime, "load_authority", lambda *, projected_inventory=False:
                        Authority() if projected_inventory is True else None)
    identities = iter([
        ("synthetic-account", "device-one", 1, 1),
        ("synthetic-account", "device-one", 2, 1),
    ])
    monkeypatch.setattr(app, "authorize", lambda _authority, _event: next(identities))
    monkeypatch.setattr(app.Settings, "from_env", classmethod(lambda _cls: object()))
    monkeypatch.setattr(app, "Service", Reader)
    event = {"version": "2.0", "routeKey": "GET /v1/users/analysis-history",
             "requestContext": {"http": {"method": "GET"}},
             "headers": {"x-device-binding-fingerprint": "device-one"},
             "queryStringParameters": None, "body": None, "isBase64Encoded": False}
    result = app.lambda_handler(event, None)
    assert result["statusCode"] == 403
    assert json.loads(result["body"])["error"]["code"] == "ACTIVE_DEVICE_REQUIRED"
