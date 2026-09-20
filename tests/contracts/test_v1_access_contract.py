"""Versioned access contract fixtures consumed by Android."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2] / 'contracts' / 'v1-access' / 'v1'


def test_every_access_fixture_matches_exact_schema():
    fixtures = json.loads((ROOT / 'fixtures.json').read_text())
    assert len(fixtures) == 14
    schemas = {name: json.loads((ROOT / (name + '.schema.json')).read_text()) for name in ('snapshot', 'error', 'trial-request')}
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
    for fixture in fixtures:
        validator = Draft202012Validator(schemas['snapshot' if fixture['statusCode'] == 200 else 'error'])
        validator.validate(fixture['body'])
    assert {f['body']['error']['code'] for f in fixtures if f['statusCode'] != 200} == set(schemas['error']['properties']['error']['properties']['code']['enum'])
    assert {f['body']['access']['basis'] for f in fixtures if f['statusCode'] == 200} == set(schemas['snapshot']['properties']['access']['properties']['basis']['enum'])


def test_unknown_member_or_enum_never_silently_accepted():
    schema = json.loads((ROOT / 'snapshot.schema.json').read_text())
    fixture = json.loads((ROOT / 'fixtures.json').read_text())[0]['body']
    validator = Draft202012Validator(schema)
    assert not validator.is_valid(fixture | {'paid': True})
    fixture['access']['basis'] = 'legacy_pro'
    assert not validator.is_valid(fixture)
