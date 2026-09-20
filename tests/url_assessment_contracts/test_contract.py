import importlib.util
import json
from pathlib import Path
import sys
from unittest import mock

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).parents[2]
CONTRACT = ROOT / 'contracts/url-assessment/v1-draft'


def read(path):
    return json.loads((CONTRACT / path).read_text())


def validator(name):
    schema = read(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.mark.parametrize('fixture', read('fixtures/manifest.json')['fixtures'], ids=lambda value: value['file'])
def test_synthetic_fixtures_match_proposed_schema_and_bilingual_keys(fixture):
    schema = fixture['schema'].removeprefix('../')
    payload = read('fixtures/' + fixture['file'])
    validator(schema).validate(payload)
    key = payload.get('messageKey') or payload.get('error', {}).get('messageKey')
    if key:
        copy = read('messages.json')[key]
        assert set(copy) == {'en', 'es'}
        assert all(isinstance(text, str) and text.strip() for text in copy.values())
    assert fixture['evidenceType'] == 'synthetic_refinement_fixture'


def test_contract_never_claims_mobile_endpoint_or_activation():
    contract = read('contract-set.json')
    assert contract['consumerEndpoint'] is None
    assert contract['consumerActivationAllowed'] is False
    assert contract['privateResolver']['mobileCallable'] is False
    assert contract['privateResolver']['assessmentVerdictProvided'] is False


@pytest.mark.parametrize('extra', [{'accountId': 'other-account'}, {'complimentary': True}, {'isPaid': True}, {'image': 'not-a-server-upload'}, {'researchConsent': True}])
def test_client_flags_cannot_be_added_as_authority(extra):
    request = read('fixtures/request-standalone_url.json') | extra
    assert not validator('request.schema.json').is_valid(request)


def test_unsupported_version_and_non_http_target_are_rejected():
    request = read('fixtures/request-standalone_url.json')
    request['contractVersion'] = '1.0'
    assert not validator('request.schema.json').is_valid(request)
    request['contractVersion'] = read('contract-set.json')['contractVersion']
    request['target']['url'] = 'file:///etc/passwd'
    assert not validator('request.schema.json').is_valid(request)


def test_origin_projection_cannot_disguise_path_query_or_fragment():
    request = read('fixtures/request-origin-only.json')
    for url in ['https://example.com/private', 'https://example.com/?secret=x', 'https://example.com/#private']:
        request['target']['url'] = url
        assert not validator('request.schema.json').is_valid(request)


@pytest.mark.parametrize('fixture', ['result-provider-unavailable.json', 'result-origin-only.json', 'result-blocked.json', 'result-unsupported.json', 'result-browser-redirect.json'])
def test_incomplete_or_reduced_evidence_cannot_become_no_known_threat(fixture):
    result = read('fixtures/' + fixture)
    result['verdict'] = 'no_known_threat_detected'
    assert not validator('result.schema.json').is_valid(result)


def test_lookup_no_match_cannot_invent_suspicious_provider_confidence():
    result = read('fixtures/result-no-match.json')
    result['verdict'] = 'suspicious'
    assert not validator('result.schema.json').is_valid(result)


def test_high_risk_can_remain_partial_and_http_warning_is_separate():
    result = read('fixtures/result-high-partial.json')
    validator('result.schema.json').validate(result)
    http = read('fixtures/result-http-warning.json')
    validator('result.schema.json').validate(http)
    assert http['verdict'] == 'no_known_threat_detected'
    assert http['transportWarnings'] == ['UNENCRYPTED_CONNECTION']


def test_deployed_resolver_completion_is_not_a_public_risk_verdict():
    spec = importlib.util.spec_from_file_location('contract_resolver', ROOT / 'src/url_redirect_resolver/resolver.py')
    resolver = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {spec.name: resolver}):
        spec.loader.exec_module(resolver)
    class Transport:
        def addresses(self, host, deadline): return ['93.184.215.14']
        def fetch(self, target, address, deadline): return 200, None
    for url in ('https://example.com/', 'http://169.254.169.254/'):
        response = resolver.resolve(url, Transport()) | {'checkId': 'synthetic-private-check'}
        validator('private-resolver-response.schema.json').validate(response)
        assert 'verdict' not in response
        assert not validator('result.schema.json').is_valid(response)
