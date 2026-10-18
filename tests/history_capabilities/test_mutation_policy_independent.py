"""Independent technical-draft counterexamples; no runtime mutation proof."""
from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / 'contracts/governed-history-mutation/2.0.0-candidate.1'
SCHEMAS = json.loads((PACKAGE / 'schemas.json').read_text())
FIXTURES = json.loads((PACKAGE / 'fixtures.json').read_text())['cases']
STATE = {'incarnation': 'a' * 32, 'generation': 1}


def errors(name, value):
    return list(Draft202012Validator(SCHEMAS[name]).iter_errors(value))


def test_all_nested_patterned_identifiers_have_strict_framing():
    def visit(node):
        if isinstance(node, dict):
            if node.get('type') == 'string' and 'pattern' in node:
                yield node
            for child in node.values(): yield from visit(child)
        elif isinstance(node, list):
            for child in node: yield from visit(child)

    valid = ['a' * 32, '11111111-1111-4111-8111-111111111111',
             'newrun_abcdefghij.ABCDEF0123456789_rn', 'gh1_1800000000_' + 'a' * 32]
    count = 0
    for schema in visit(SCHEMAS):
        validator = Draft202012Validator(schema)
        examples = [v for v in valid if not list(validator.iter_errors(v))]
        assert examples, schema['pattern']
        count += 1
        for value in examples:
            for suffix in ('\n', '\r', '\r\n', '\u2028', '\u2029', '\x00'):
                assert list(validator.iter_errors(value + suffix)), (schema['pattern'], repr(suffix))
    assert count >= 10  # Includes every repeated nested state/lease/UUID schema.


@pytest.mark.parametrize('field,value', [
    ('accountId', 'synthetic-owner'), ('operationProof', 'synthetic-proof'),
    ('executionAuthorized', True), ('purgeStatusAtCommit', 'COMPLETE'),
])
def test_client_cannot_inject_owner_authority_or_success_into_any_request(field, value):
    for fixture in FIXTURES:
        if fixture['valid']:
            assert errors(fixture['schema'], fixture['value'] | {field: value})


def test_prepare_cannot_reissue_a_client_selected_operation_uuid():
    value = {'schemaVersion': 2, 'family': 'governed-history', 'mode': 'prepare',
             'action': 'clear', 'expectedHistoryState': STATE}
    assert not errors('bootstrapPrepare', value)
    value['operationId'] = '11111111-1111-4111-8111-111111111111'
    assert errors('bootstrapPrepare', value)


@pytest.mark.parametrize('generation', [True, False, -1, 9007199254740992, '1', None])
def test_state_generation_never_coerces_boolean_string_or_overflow(generation):
    assert errors('state', STATE | {'generation': generation})
    for fixture in FIXTURES:
        if fixture['valid'] and isinstance(fixture['value'].get('expectedHistoryState'), dict):
            value = deepcopy(fixture['value'])
            value['expectedHistoryState']['generation'] = generation
            assert errors(fixture['schema'], value)


def test_absent_state_is_only_a_startfresh_request_shape_not_clear_authority():
    base = {'schemaVersion': 2, 'family': 'governed-history', 'mode': 'prepare',
            'expectedHistoryState': None}
    assert errors('bootstrapPrepare', base | {'action': 'clear'})
    assert errors('bootstrapPrepare', base | {'action': 'delete_one',
        'targetResultId': 'gh1_1800000000_' + 'a' * 32})
    assert not errors('bootstrapPrepare', base | {'action': 'start_fresh'})
    # Shape acceptance is explicitly not a usable MAC, policy approval or write.


def test_lease_ascii_letters_are_not_accidentally_rejected_by_newline_guard():
    schema = SCHEMAS['mutationRequest']['properties']['intentLease']
    validator = Draft202012Validator(schema)
    validator.validate('newrun_abcdefghij.ABCDEF0123456789_rn')
    assert list(validator.iter_errors('newrun_abcdefghij.ABCDEF0123456789_rn\n'))
