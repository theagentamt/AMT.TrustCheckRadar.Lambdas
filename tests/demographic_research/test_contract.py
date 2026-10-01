import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT=Path(__file__).resolve().parents[2]
CONTRACT=ROOT/"contracts"/"demographic-research"/"1.0.0-candidate.1"


def test_contract_checksums_and_fixtures_are_exact():
    for line in (CONTRACT/"SHA256SUMS").read_text().splitlines():
        digest,name=line.split()
        assert hashlib.sha256((CONTRACT/name).read_bytes()).hexdigest()==digest
    fixtures=json.loads((CONTRACT/"fixtures.json").read_text())
    request=Draft202012Validator(json.loads((CONTRACT/"request.schema.json").read_text()))
    response=Draft202012Validator(json.loads((CONTRACT/"response.schema.json").read_text()))
    error=Draft202012Validator(json.loads((CONTRACT/"error.schema.json").read_text()))
    assert not list(request.iter_errors(fixtures["enrollRequest"]))
    assert not list(request.iter_errors(fixtures["withdrawRequest"]))
    assert not list(response.iter_errors(fixtures["enrolledResponse"]))
    assert not list(error.iter_errors(fixtures["disabledError"]))


def test_closed_enums_match_approved_wire_codes():
    schema=json.loads((CONTRACT/"request.schema.json").read_text())
    enroll=schema["oneOf"][0]["properties"]
    assert enroll["ageBand"]["enum"]==[
        "18_24","25_34","35_44","45_54","55_64","65_74","75_PLUS","PREFER_NOT_TO_SAY"]
    assert len(enroll["stateCode"]["enum"])==53
    assert enroll["stateCode"]["enum"][-3:]==["DC","OTHER_US_JURISDICTION","PREFER_NOT_TO_SAY"]
