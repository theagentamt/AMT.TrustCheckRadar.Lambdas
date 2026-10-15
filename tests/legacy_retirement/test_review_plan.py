"""Protected migration review preserves evidence and has no apply path."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


review = module('legacy_review_plan', ROOT/'tools/legacy_access_inventory/review_plan.py')
NOW = 1_800_000_000
OWNER = 'a' * 64
STAMP = '2026-01-01T00:00:00+00:00'
EPOCH = '15c81ba4-2fa6-43c3-8895-889f08c931bf'
OPERATION = '47debb73-444b-4bb1-9889-fb56885b7922'


def request():
    return {'PK':'ANALYSIS#REQUEST#'+OWNER,'SK':'one','status':'PROCESSING',
            'payloadHash':'b'*64,'createdAt':STAMP,'updatedAt':STAMP,
            'expiresAt':NOW+100,'ttl':NOW+100,
            'leaseToken':'lease','leaseExpiresAt':NOW+10}


def completed_request(status='COMPLETED'):
    row = {'PK':'ANALYSIS#REQUEST#'+OWNER,'SK':'one','status':status,
           'payloadHash':'b'*64,'createdAt':STAMP,'updatedAt':STAMP,
           'expiresAt':NOW+100,'ttl':NOW+100}
    if status == 'COMPLETED':
        row |= {'response':{'requestId':'one'},'resultReadyAt':STAMP,'completedAt':STAMP}
    return row


def receipt(kind='monthly'):
    return {'PK':'ANALYSIS#CONSUMPTION#'+OWNER,'SK':'one','accountIdHash':OWNER,
            'consumptionType':kind,'createdAt':STAMP,'expiresAt':NOW+100,'ttl':NOW+100}


def entitlement():
    return {'PK':'USER#owner','SK':'ENTITLEMENT','accountId':'owner',
            'billingPeriodEndUtc':None,'billingPeriodStartUtc':None,'createdAt':STAMP,
            'entitlementTier':'FREE','isAccessGranted':False,'lastVerifiedAtUtc':None,
            'monthlyScanLimit':15,'platform':None,'productId':None,'remainingCredits':2,
            'remainingMonthlyScans':8,'subscriptionStatus':'expired','updatedAt':STAMP}


def deletion():
    return {'PK':'ACCOUNT#owner','SK':'ACCOUNT_DELETION','schemaVersion':1,'recordVersion':1,
            'environment':'dev','eventType':'account.deletion.completed','accountId':'owner',
            'operationId':OPERATION,'status':'COMPLETE','occurredAtEpoch':NOW-100,
            'deleteByEpoch':NOW-100+86400,'completedAtEpoch':NOW-10,
            'retainUntilEpoch':NOW-10+120*86400}


def consent():
    return {'PK':'USER#owner','SK':'CAMPAIGN_PARTICIPATION','schemaVersion':1,
            'recordVersion':1,'environment':'dev','state':'enrolled','stateVersion':1,
            'noticeVersion':'2026-09-07','policyVersion':'policy-1','consentEpochId':EPOCH,
            'effectiveFrom':'2026-01-01T00:00:00Z','updatedAt':'2026-01-01T00:00:00Z',
            'lastOperationId':OPERATION}


def inventory(records):
    return {'schemaVersion':2,'observedAtEpoch':NOW,'records':records}


def proposal(plan, actions=None):
    actions = actions or {}
    decisions = []
    for row in plan['records']:
        action = actions.get(row['recordRef'], 'preserve')
        decisions.append({'recordRef':row['recordRef'],'action':action,
                          'beforeImageSha256':row['recordRef'],
                          'rollbackAction':('no_change_required' if action == 'preserve'
                                            else 'restore_quarantined_evidence_without_authority')})
    return {'schemaVersion':1,'sourceInventorySha256':plan['sourceInventorySha256'],
            'reviewPlanSha256':plan['reviewPlanSha256'],'decisions':decisions}


def test_review_plan_is_deterministic_digest_bound_and_contains_no_raw_values():
    value = inventory([
        {'family':'request','item':request()},
        {'family':'entitlement','item':entitlement()},
        {'family':'private@example.invalid/https://unsafe.invalid/message',
         'item':{'PK':'private@example.invalid','SK':'raw-message','text':'secret'}},
    ])
    first, second = review.build_review_plan(value), review.build_review_plan(json.loads(json.dumps(value)))
    assert first == second
    assert first['dispositionCounts'] == {
        'preserve_in_flight':1, 'preserve_unknown':1,
        'retirement_candidate_review_required':1,
    }
    encoded = json.dumps(first)
    assert 'private@example.invalid' not in encoded and 'unsafe.invalid' not in encoded
    assert 'secret' not in encoded and 'USER#owner' not in encoded
    assert first['records'][2]['family'] == 'unknown'
    assert all(len(row['recordRef']) == 64 for row in first['records'])
    assert first['plannedMutationCount'] == 0 and not first['applyAvailable']


def test_duplicate_or_unknown_evidence_is_preserved_and_never_retirement_eligible():
    duplicate = {'family':'entitlement','item':entitlement()}
    plan = review.build_review_plan(inventory([duplicate, duplicate,
        {'family':'entitlement','item':entitlement() | {'unknownField':'unsafe'}}]))
    assert plan['duplicateRecordCount'] == 1
    assert plan['dispositionCounts'] == {'preserve_ambiguous_duplicate':2,'preserve_unknown':1}
    assert all(row['allowedProposalActions'] == ['preserve'] for row in plan['records'])
    assert {'ambiguous_duplicate_records_present','unknown_shapes_present'} <= set(plan['blockers'])


def test_conflicting_valid_receipts_are_explicitly_ambiguous_and_block_reconciliation():
    plan = review.build_review_plan(inventory([
        {'family':'request','item':completed_request()},
        {'family':'consumption','item':receipt('monthly')},
        {'family':'consumption','item':receipt('credit')},
    ]))
    assert plan['requestLifecycle']['unknown'] == 1
    assert plan['dispositionCounts'] == {'preserve_ambiguous_accounting':3}
    assert 'request_accounting_reconciliation_required' in plan['blockers']
    assert all(row['allowedProposalActions'] == ['preserve'] for row in plan['records'])


def test_erased_request_preserves_suppression_explicitly():
    plan = review.build_review_plan(inventory([
        {'family':'request','item':completed_request('COMPLETED_ERASED')}]))
    assert plan['dispositionCounts'] == {'preserve_erasure_suppression':1}
    assert plan['requestLifecycle']['erased'] == 1


def test_validator_rejects_edited_stale_missing_or_forbidden_decisions():
    value = inventory([{'family':'request','item':request()},
                       {'family':'entitlement','item':entitlement()}])
    plan = review.build_review_plan(value)
    valid = proposal(plan, {plan['records'][1]['recordRef']:'retire_legacy_access'})
    pins = {'expected_source_sha256':plan['sourceInventorySha256'],
            'expected_review_plan_sha256':plan['reviewPlanSha256']}
    result = review.validate_proposal(value, plan, valid, **pins)
    assert result['actionCounts'] == {'preserve':1,'retire_legacy_access':1}
    assert result['beforeImagesDigestBound'] and not result['applyAvailable'] and not result['rollbackReady']
    edited = json.loads(json.dumps(plan)); edited['records'][0]['disposition'] = 'settled'
    type_tampered = json.loads(json.dumps(plan)); type_tampered['schemaVersion'] = True
    type_tampered['writesPerformed'] = 0
    stale = inventory([{'family':'request','item':request() | {'updatedAt':'2026-01-02T00:00:00+00:00'}},
                       {'family':'entitlement','item':entitlement()}])
    for supplied_value, supplied_plan, supplied_proposal in (
        (value, edited, valid), (value, type_tampered, valid), (stale, plan, valid),
        (value, plan, valid | {'reviewPlanSha256':'0'*64}),
        (value, plan, valid | {'decisions':valid['decisions'][:-1]}),
    ):
        with pytest.raises(ValueError):
            review.validate_proposal(supplied_value, supplied_plan, supplied_proposal, **pins)
    coordinated = review.build_review_plan(stale)
    with pytest.raises(ValueError, match='MIGRATION_REVIEW_STALE_OR_EDITED'):
        review.validate_proposal(stale, coordinated, proposal(coordinated), **pins)
    forbidden = proposal(plan); forbidden['decisions'][0]['action'] = 'grant_access'
    forbidden['decisions'][0]['rollbackAction'] = 'no_change_required'
    with pytest.raises(ValueError, match='MIGRATION_PROPOSAL_INVALID'):
        review.validate_proposal(value, plan, forbidden, **pins)


def test_retirement_proposal_cannot_ignore_deletion_or_consent_fences():
    value = inventory([{'family':'deletion','item':deletion()}, {'family':'consent','item':consent()}])
    plan = review.build_review_plan(value)
    assert all(row['allowedProposalActions'] == ['preserve'] for row in plan['records'])
    pins = {'expected_source_sha256':plan['sourceInventorySha256'],
            'expected_review_plan_sha256':plan['reviewPlanSha256']}
    assert review.validate_proposal(value, plan, proposal(plan), **pins)['deletionAndConsentFencesPreserved']
    for index in range(2):
        bad = proposal(plan); bad['decisions'][index]['action'] = 'retire_legacy_access'
        bad['decisions'][index]['rollbackAction'] = 'restore_quarantined_evidence_without_authority'
        with pytest.raises(ValueError, match='MIGRATION_PROPOSAL_INVALID'):
            review.validate_proposal(value, plan, bad, **pins)


def test_reordered_snapshot_has_new_digest_but_same_preservation_semantics():
    entries = [{'family':'request','item':request()}, {'family':'entitlement','item':entitlement()}]
    first = review.build_review_plan(inventory(entries))
    second = review.build_review_plan(inventory(list(reversed(entries))))
    assert first['sourceInventorySha256'] != second['sourceInventorySha256']
    assert first['reviewPlanSha256'] != second['reviewPlanSha256']
    assert first['dispositionCounts'] == second['dispositionCounts']


def test_cli_rejects_invalid_or_duplicate_input_with_fixed_error(tmp_path):
    cli = ROOT/'tools/legacy_access_inventory/review_plan.py'
    for raw in ('{"schemaVersion":2,"schemaVersion":2,"observedAtEpoch":1,"records":[]}',
                '{"schemaVersion":2,"observedAtEpoch":1,"records":"bad"}'):
        path = tmp_path/'input.json'; path.write_text(raw)
        result = subprocess.run([sys.executable,str(cli),'--input',str(path)],capture_output=True,text=True)
        assert result.returncode == 2 and result.stdout == ''
        assert result.stderr == 'MIGRATION_REVIEW_INPUT_INVALID\n'


def test_cli_validates_only_with_external_exact_pins(tmp_path):
    cli = ROOT/'tools/legacy_access_inventory/review_plan.py'
    value = inventory([{'family':'entitlement','item':entitlement()}])
    input_path = tmp_path/'input.json'; input_path.write_text(json.dumps(value))
    built = subprocess.run([sys.executable,str(cli),'--input',str(input_path)],
                           check=True,capture_output=True,text=True)
    plan = json.loads(built.stdout)
    plan_path = tmp_path/'plan.json'; plan_path.write_text(json.dumps(plan))
    proposal_path = tmp_path/'proposal.json'; proposal_path.write_text(json.dumps(proposal(plan)))
    command = [sys.executable,str(cli),'--input',str(input_path),'--review-plan',str(plan_path),
               '--proposal',str(proposal_path),'--expected-source-sha256',plan['sourceInventorySha256'],
               '--expected-review-plan-sha256',plan['reviewPlanSha256']]
    validated = subprocess.run(command,check=True,capture_output=True,text=True)
    assert json.loads(validated.stdout)['mode'] == 'proposal_validation_only'
    rejected = subprocess.run(command[:-1]+['0'*64],capture_output=True,text=True)
    assert rejected.returncode == 2 and rejected.stderr == 'MIGRATION_REVIEW_INPUT_INVALID\n'
