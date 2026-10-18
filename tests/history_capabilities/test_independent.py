"""Independent offline compatibility/privacy counterexamples; no service calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / 'contracts/history-capabilities/1.0.0-candidate.1'
load = lambda path: json.loads(path.read_text())
SCHEMA = load(PACKAGE / 'operation-plan.schema.json')
PLANS = load(PACKAGE / 'contract-set.json')['existingContractOperationBindings']
VALIDATOR = Draft202012Validator(SCHEMA)


@pytest.mark.parametrize('permission', [True, 0, 1, None, 'false', []])
def test_no_boolean_numeric_or_string_permission_coercion(permission):
    for original in PLANS:
        assert list(VALIDATOR.iter_errors(original | {'executionAuthorized': permission}))


def test_each_plan_is_closed_and_requires_every_exact_binding_field():
    sensitive = {
        'accountId': 'synthetic-owner', 'sub': 'synthetic-owner',
        'proof': 'synthetic-proof', 'operationId': 'synthetic-operation',
        'requestId': 'gh1_synthetic', 'recognitionGeneration': 0,
        'capabilities': ['delete'], 'authorization': 'synthetic-token',
    }
    for original in PLANS:
        VALIDATOR.validate(original)
        for field in original:
            missing = deepcopy(original)
            missing.pop(field)
            assert list(VALIDATOR.iter_errors(missing)), field
        for field, value in sensitive.items():
            assert list(VALIDATOR.iter_errors(original | {field: value})), field


def test_no_cross_family_version_method_path_recombination():
    governed = [p for p in PLANS if p['family'] == 'governed_history_candidate1']
    legacy = [p for p in PLANS if p['family'] == 'legacy_history_v1']
    for g in governed:
        for l in legacy:
            for field in ('family', 'contractVersion', 'pathTemplate'):
                if g[field] != l[field]:
                    assert list(VALIDATOR.iter_errors(g | {field: l[field]}))
        for path in (g['pathTemplate'] + '?owner=synthetic',
                     g['pathTemplate'] + '/', '/v2/users/analysis-history'):
            assert list(VALIDATOR.iter_errors(g | {'pathTemplate': path}))
        for method in ('POST', 'DELETE', 'PATCH', 'get'):
            assert list(VALIDATOR.iter_errors(g | {'method': method}))


def test_schema_branch_set_and_checksums_have_no_hidden_extensions():
    branches = SCHEMA['oneOf']
    assert len(branches) == len(PLANS) == 11
    actual = []
    for branch in branches:
        assert branch['type'] == 'object' and branch['additionalProperties'] is False
        assert set(branch['required']) == set(branch['properties']) == set(PLANS[0])
        assert all(set(p) == {'const'} for p in branch['properties'].values())
        actual.append({k: v['const'] for k, v in branch['properties'].items()})
    canonical = lambda rows: sorted(json.dumps(x, sort_keys=True) for x in rows)
    assert canonical(actual) == canonical(PLANS)
    sums = {}
    for line in (PACKAGE / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ')
        assert name not in sums and Path(name).name == name
        sums[name] = digest
    assert set(sums) == {p.name for p in PACKAGE.iterdir() if p.name != 'SHA256SUMS'}
    assert all(hashlib.sha256((PACKAGE / name).read_bytes()).hexdigest() == digest
               for name, digest in sums.items())


def test_existing_governed_item_does_not_admit_new_award_or_owner_fields():
    governed = ROOT / 'contracts/governed-history/1.0.0-candidate.1'
    registry = Registry()
    for path in governed.glob('*.schema.json'):
        registry = registry.with_resource(path.name, Resource.from_contents(load(path)))
    validator = Draft202012Validator(load(governed / 'item.schema.json'), registry=registry)
    item = load(governed / 'fixtures.json')['fixtures'][0]['response']['items'][0]
    validator.validate(item)
    for field, value in {
        'qualifyingChecks': 1, 'recognitionGeneration': 0,
        'awardIds': ['checks_1'], 'unlockedAtEpoch': 1800000000,
        'accountId': 'synthetic-owner', 'operationId': 'synthetic-operation',
        'capabilities': ['delete'], 'historyGeneration': 0,
    }.items():
        assert list(validator.iter_errors(item | {field: value})), field
