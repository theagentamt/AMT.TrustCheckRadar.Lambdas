import importlib.util
import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "age-attestation" / "1.0.0-candidate.1"


def load(path):
    return json.loads(path.read_text())


def test_versioned_fixtures_match_strict_schemas():
    request = load(CONTRACT / "request.schema.json")
    success = load(CONTRACT / "success.schema.json")
    error = load(CONTRACT / "error-response.schema.json")
    manifest = load(CONTRACT / "fixtures" / "manifest.json")
    checker = jsonschema.FormatChecker()
    for name in manifest["valid"]["requests"]:
        jsonschema.validate(load(CONTRACT / "fixtures" / name), request, format_checker=checker)
    for name in manifest["valid"]["successes"]:
        jsonschema.validate(load(CONTRACT / "fixtures" / name), success, format_checker=checker)
    for name in manifest["valid"]["errors"]:
        jsonschema.validate(load(CONTRACT / "fixtures" / name), error, format_checker=checker)
    for name in manifest["invalid"]["requests"]:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(load(CONTRACT / "fixtures" / name), request, format_checker=checker)


def test_published_contract_checksums_are_current():
    for line in (CONTRACT / "SHA256SUMS").read_text().splitlines():
        expected, name = line.split("  ", 1)
        assert hashlib.sha256((CONTRACT / name).read_bytes()).hexdigest() == expected


def test_real_phone_metadata_matches_golden_policy(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "users")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    spec = importlib.util.spec_from_file_location("age_phone_metadata", ROOT / "src" / "age_attestation" / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    allowed = set(load(CONTRACT / "phone-policy.json")["defaultAllowedRegionCodes"])
    for case in load(CONTRACT / "fixtures" / "phone-metadata.json"):
        try:
            region, _ = module.LibPhoneNumberMetadata().classify(case["phoneNumber"])
            eligible = region in allowed
            assert region == case["regionCode"]
        except module.AppError:
            eligible = False
        assert eligible is case["eligible"]
