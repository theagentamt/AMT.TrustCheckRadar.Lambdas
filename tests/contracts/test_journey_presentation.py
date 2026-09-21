import copy
import importlib.util
import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2] / 'contracts/journey-presentation/0.1.0-candidate.1'
SPEC = importlib.util.spec_from_file_location('journey_presentation_mapping', ROOT / 'reference_mapping.py')
MAPPING = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MAPPING)
SCHEMA = json.loads((ROOT / 'assessment.schema.json').read_text())
FIXTURES = json.loads((ROOT / 'fixtures.json').read_text())
MESSAGES = json.loads((ROOT / 'messages.json').read_text())['messages']


@pytest.mark.parametrize('case', FIXTURES, ids=lambda case: case['id'])
def test_canonical_fixtures_and_bilingual_closed_copy(case):
    result = MAPPING.present(**case['input'])
    assert result == case['assessment']
    jsonschema.Draft202012Validator(SCHEMA).validate(result)
    assert set(MESSAGES[result['messageKey']]) == {'en', 'es'}
    assert all(MESSAGES[result['messageKey']][language].strip() for language in case['languages'])
    assert not {'accounting', 'chargedChecks', 'access', 'summary', 'url', 'recommendedActions'} & set(result)


@pytest.mark.parametrize('field,value', [('url', 'https://attacker.invalid'), ('nextAction', 'open_url'),
                                         ('contractVersion', 'future'), ('verdict', 'safe')])
def test_rejects_arbitrary_actions_urls_and_version_fallback(field, value):
    candidate = copy.deepcopy(FIXTURES[0]['assessment'])
    candidate[field] = value
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(candidate, SCHEMA)


def test_independent_threat_cannot_be_downgraded_when_analysis_stops():
    candidate = copy.deepcopy(next(case['assessment'] for case in FIXTURES if case['id'] == 'message-known-hostile'))
    assert candidate['processingOutcome'] == 'partial'
    assert candidate['limitationCodes'] == ['HOSTILE_INPUT_STOP']
    candidate['verdict'] = 'unknown'
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(candidate, SCHEMA)


def test_model_claims_cannot_be_used_as_independent_evidence():
    with pytest.raises(ValueError):
        MAPPING.present(journey='message', processing='complete', limitations=[], verified_evidence=[
            {'source': 'model', 'outcome': 'match', 'targetScope': 'full_submitted_url'}])
    with pytest.raises(TypeError):
        MAPPING.present(journey='message', processing='complete', limitations=[], recommendedActions=['Pay now'])


def test_link_no_match_cannot_clear_message_or_limited_url():
    evidence = [{'source': 'google_web_risk_lookup', 'outcome': 'no_match', 'targetScope': 'full_submitted_url'}]
    assert MAPPING.present(journey='message', processing='complete', limitations=[], verified_evidence=evidence)['verdict'] == 'unknown'
    assert MAPPING.present(journey='link', processing='partial', limitations=[], verified_evidence=evidence)['verdict'] == 'unknown'


def test_missing_playbook_never_invents_content_or_complete_status():
    with pytest.raises(ValueError):
        MAPPING.present(journey='recovery', processing='complete', limitations=[])


@pytest.mark.parametrize('field,value', [('messageKey', 'journey.known_threat'),
                                         ('nextAction', 'avoid_link'),
                                         ('processingOutcome', 'complete')])
def test_stop_cannot_carry_misleading_copy_or_completed_state(field, value):
    candidate = copy.deepcopy(FIXTURES[0]['assessment'])
    candidate[field] = value
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(candidate, SCHEMA)
