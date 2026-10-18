"""Offline existing-contract bindings, not a new API or permission surface."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / 'contracts/history-capabilities/1.0.0-candidate.1'
read = lambda p: json.loads(p.read_text())
DOC = read(CONTRACT/'contract-set.json')
FIXTURES = read(CONTRACT/'fixtures.json')
SCHEMA = read(CONTRACT/'operation-plan.schema.json')


def test_schema_and_offline_not_wire_boundary():
    Draft202012Validator.check_schema(SCHEMA)
    assert DOC['documentKind'] == 'offline_compatibility_not_wire'
    assert not DOC['activationEnabled'] and not DOC['existingWireContractsChanged']
    assert DOC['newRoutes'] == DOC['newHeaders'] == []
    assert not DOC['settledImplementationBoundary']['bridgeAdapterImplemented']
    for line in (CONTRACT/'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ')
        assert hashlib.sha256((CONTRACT/name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize('fixture', FIXTURES['positive'], ids=lambda x:x['name'])
def test_existing_operation_binding_only(fixture):
    Draft202012Validator(SCHEMA).validate(fixture['document'])
    assert fixture['document']['executionAuthorized'] is False


@pytest.mark.parametrize('fixture', FIXTURES['negative'], ids=lambda x:x['name'])
def test_unsupported_or_identity_bearing_plan_is_rejected(fixture):
    assert list(Draft202012Validator(SCHEMA).iter_errors(fixture['document']))


def test_exact_existing_contract_routes_and_byte_anchors():
    for p, digest in DOC['sourceContracts'].items():
        assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==digest
    legacy = read(ROOT/'contracts/history/v1/contract-set.json')
    governed = read(ROOT/'contracts/governed-history/1.0.0-candidate.1/contract-set.json')
    plans = DOC['existingContractOperationBindings']
    legacy_plans = [p for p in plans if p['family']=='legacy_history_v1']
    assert {(p['method'],p['pathTemplate'])for p in legacy_plans} == {(p['method'],p['path'])for p in legacy['routes']}
    gov_plans = [p for p in plans if p['family']=='governed_history_candidate1']
    assert {p['method']+' '+p['pathTemplate']for p in gov_plans} == set(governed['routes'])
    assert len(gov_plans)==2 and {p['operation']for p in gov_plans}=={'list','detail'}


def test_approved_invariants_are_distinct_from_unapproved_governed_policy():
    inv = DOC['approvedInvariants']
    assert inv['badgeThresholds']==[1,5,20] and inv['sameIdRetryCountsOnce'] is True
    assert all(inv[k] is False for k in ('points','clearResetsBadges','badgeResetClearsHistory','quotaCashCampaignEffects','historicalBackfill','retentionRenewedByReadOrRetry'))
    choices={x['id']:x for x in DOC['openDecisions']}
    assert choices['G1']['status']==choices['G2']['status']=='unapproved_candidate'
    assert choices['T1']['status']=='design_required' and choices['P1']['status']=='specific_record_design_required'
    assert DOC['families']['governed_history_candidate1']['recognition'] is False
    assert DOC['families']['legacy_history_v1']['retentionDays']==90
    assert DOC['families']['governed_history_candidate1']['retentionDays']==7


def test_governed_result_is_not_legacy_assessment_and_ids_are_not_disjoint():
    fixture=read(ROOT/'contracts/governed-history/1.0.0-candidate.1/fixtures.json')['fixtures'][0]['response']['items'][0]
    legacy=read(ROOT/'contracts/history/v1/api-schemas.json')
    # The wrapper and lexical grammar overlap. Semantic ownership does not.
    Draft202012Validator(legacy['$defs']['requestId']).validate(fixture['resultId'])
    Draft202012Validator(legacy['$defs']['mutationRequest']).validate({'schemaVersion':1,'operationId':'11111111-1111-4111-8111-111111111111'})
    for conversion in (fixture,fixture['outcome']):
        assert list(Draft202012Validator(legacy['$defs']['assessment']).iter_errors(conversion))
    assert DOC['identityAndIdempotency']['lexicalIdDisjointness'] is False
    assert DOC['identityAndIdempotency']['sameStringIsOwnershipEvidence'] is False


def test_strict_governed_wire_rejects_capabilities_progress_and_client_claims():
    schema=read(ROOT/'contracts/governed-history/1.0.0-candidate.1/list.schema.json')
    from referencing import Registry,Resource
    registry=Registry()
    for p in (ROOT/'contracts/governed-history/1.0.0-candidate.1').glob('*.schema.json'):
        registry=registry.with_resource(p.name,Resource.from_contents(read(p)))
    validator=Draft202012Validator(schema,registry=registry)
    page=deepcopy(read(ROOT/'contracts/governed-history/1.0.0-candidate.1/fixtures.json')['fixtures'][0]['response'])
    for key,value in [('capabilities',[]),('recognitionGeneration',0),('qualifyingChecks',0),('accountId','synthetic-owner')]:
        invalid=page|{key:value}
        assert list(validator.iter_errors(invalid))
    validator.validate(page)


def test_recognition_policy_does_not_block_destructive_contract_design_or_existing_reads():
    split=DOC['workSplit']
    assert split['deleteAndClear']['designAndSyntheticFixturesAllowedNow'] is True
    assert split['deleteAndClear']['requiresRecognitionPolicyG1G2'] is False
    assert all('G1' not in x and 'G2' not in x for x in split['deleteAndClear']['adapterPrerequisites'])
    assert {'G1','G2'} <= set(split['recognitionAwardsAndPresentation']['adapterPrerequisites'])
    assert split['existingReadOnly']['blockedByG1G2'] is False
