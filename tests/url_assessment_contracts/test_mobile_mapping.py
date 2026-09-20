import copy
import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).parents[2] / 'contracts/url-assessment/v1-draft'
spec = importlib.util.spec_from_file_location('mobile_reference_mapping', ROOT / 'reference_mapping.py')
mapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mapper)


def read(file): return json.loads((ROOT / file).read_text())
def valid(file, value): return Draft202012Validator(read(file), format_checker=FormatChecker()).is_valid(value)


@pytest.mark.parametrize('case', read('mapping-cases.json')['cases'], ids=lambda case: case['name'])
def test_every_private_to_public_mapping_is_reproducible_and_localized(case):
    result = mapper.map_private(case['private'], **case['authority'])
    assert result == case['expected']
    schema = 'result.schema.json' if result['kind'] == 'assessment_result' else 'error.schema.json'
    assert valid(schema, result)
    key = result.get('messageKey') or result['error']['messageKey']
    assert set(read('messages.json')[key]) == {'en', 'es'}
    assert not {'lookupCount', 'providerCallCount', 'observedHopCount', 'consumerAccessEnabled', 'schemaVersion'} & result.keys()


def test_mapping_covers_every_current_private_reason():
    cases = read('mapping-cases.json')['cases']
    reasons = {x for case in cases for x in case['private']['reasonCodes'] if x in mapper.REASON_MAP}
    assert reasons == set(mapper.REASON_MAP) == set(read('private-reason-map.json'))


def test_private_configuration_failures_hide_internal_details():
    for case in read('mapping-cases.json')['cases']:
        if case['name'] in ('provider-authorization-failed', 'secret-unavailable', 'configuration-unavailable', 'internal-error'):
            result = case['expected']
            assert result['reasonCodes'] == ['SERVICE_UNAVAILABLE']
            assert result['messageKey'] == 'url.unavailable'
            assert result['nextAction'] == 'use_built_in_help'


def test_private_mismatch_is_not_mobile_upgrade_requirement():
    for case in read('mapping-cases.json')['cases']:
        if case['name'].startswith('unsupported-private-'):
            result = case['expected']
            assert result['error']['code'] == 'SERVICE_UNAVAILABLE'
            assert result['nextAction'] == 'use_built_in_help'
            assert result['retry']['disposition'] == 'reconcile_same_check'


def test_same_private_failure_does_not_determine_charge_or_replay():
    cases = {case['name']: case for case in read('mapping-cases.json')['cases']}
    selected = [cases[name] for name in ('authoritative-retry', 'same-failure-charged', 'same-failure-unknown', 'replay-not-authorized')]
    assert all(case['private'] == selected[0]['private'] for case in selected)
    assert [case['expected']['accounting']['chargedChecks'] for case in selected] == [0, 1, None, 0]
    assert [case['expected']['retry']['disposition'] for case in selected] == ['retry_same_check', 'do_not_retry', 'reconcile_same_check', 'do_not_retry']
    assert all(case['expected']['retry']['automaticRetryAllowed'] is False for case in selected)


def test_expired_access_still_allows_reconciliation_without_provider_work():
    case = next(x for x in read('mapping-cases.json')['cases'] if x['name'] == 'expired-reconcile')
    response = case['expected']
    assert not response['access']['externalChecksAllowed']
    assert response['access']['reconciliationAllowed']
    assert response['access']['builtInChecksAllowed']
    assert response['retry']['disposition'] == 'reconcile_same_check'
    request = read('fixtures/request-reconciliation.json')
    assert valid('reconciliation-request.schema.json', request)
    assert not valid('reconciliation-request.schema.json', request | {'url': 'https://example.com/'})


@pytest.mark.parametrize('field,value', [('contractVersion', 'next-version'), ('kind', 'private_result'), ('verdict', 'safe'), ('processingOutcome', 'new_enum'), ('messageKey', 'untrusted.server.copy'), ('reasonCodes', ['UNKNOWN_REASON'])])
def test_unknown_public_contract_values_fail_closed(field, value):
    payload = read('fixtures/result-no-match.json')
    payload[field] = value
    assert not valid('result.schema.json', payload)


@pytest.mark.parametrize('container,key', [('access', 'state'), ('access', 'basis'), ('accounting', 'state'), ('retry', 'disposition')])
def test_unknown_control_enums_fail_closed(container, key):
    payload = read('fixtures/result-no-match.json')
    payload[container][key] = 'new_unrecognized_enum'
    assert not valid('result.schema.json', payload)


@pytest.mark.parametrize('fixture', ['result-high-partial.json', 'result-conflicting-evidence.json'])
def test_known_threat_cannot_carry_reassuring_message_or_open_action(fixture):
    payload = read('fixtures/' + fixture)
    payload['messageKey'] = 'url.no_known_threat'
    assert not valid('result.schema.json', payload)
    payload = read('fixtures/' + fixture)
    payload['nextAction'] = 'verify_independently'
    assert not valid('result.schema.json', payload)


def test_unknown_result_cannot_carry_no_threat_copy():
    payload = read('fixtures/result-provider-unavailable.json')
    payload['messageKey'] = 'url.no_known_threat'
    assert not valid('result.schema.json', payload)


def test_no_match_cannot_carry_known_threat_copy():
    payload = read('fixtures/result-no-match.json')
    payload['messageKey'] = 'url.known_threat'
    assert not valid('result.schema.json', payload)


def test_pending_accounting_is_not_zero_and_does_not_allow_retry():
    payload = read('fixtures/mapped-accounting-pending.json')
    payload['accounting']['chargedChecks'] = 0
    assert not valid('result.schema.json', payload)
    payload = read('fixtures/mapped-accounting-pending.json')
    payload['retry']['disposition'] = 'retry_same_check'
    assert not valid('result.schema.json', payload)


def test_allowed_access_cannot_be_claimed_by_free_or_huge_numeric_quota():
    payload = read('fixtures/mapped-authoritative-retry.json')['access']
    payload['basis'] = 'free'
    assert not valid('access.schema.json', payload)
    payload['basis'] = 'complimentary'
    payload['remainingChecks'] = 99999999
    payload['unlimited'] = True
    assert not valid('access.schema.json', payload)


def test_reference_mapper_requires_explicit_authority_not_defaults():
    case = read('mapping-cases.json')['cases'][0]
    kwargs = copy.deepcopy(case['authority'])
    del kwargs['same_check_replay_authorized']
    with pytest.raises(TypeError): mapper.map_private(case['private'], **kwargs)
    kwargs = copy.deepcopy(case['authority'])
    kwargs['accounting'] = None
    with pytest.raises(ValidationError): mapper.map_private(case['private'], **kwargs)
