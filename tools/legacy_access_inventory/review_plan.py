#!/usr/bin/env python3
"""Build and validate a protected, non-mutating legacy migration review plan.

Outputs contain canonical record digests, families and review-only dispositions.
There is no AWS client, executor or apply operation. Live record references are
protected evidence and must never be committed or published.
"""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan import (HASH, MAX_BYTES, REQUIRED_FAMILIES, _request_identity,
                  _settlement_matches, classify, known_v2, no_duplicates, plan_v2)  # noqa: E402


FORBIDDEN_OUTCOMES = (
    'charge_request', 'create_authority', 'dispatch_provider', 'erase_unknown',
    'grant_access', 'reset_allowance', 'synthesize_history',
)


def _integer(value):
    if isinstance(value, (int, Decimal)) and not isinstance(value, bool) and value == int(value):
        return int(value)
    raise TypeError('MIGRATION_REVIEW_INPUT_INVALID')


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False,
                      default=_integer).encode()


def _record_ref(entry):
    return hashlib.sha256(_canonical(entry)).hexdigest()


def _base_disposition(family, row):
    if not known_v2(family, row):
        return 'preserve_unknown'
    bucket = classify(family, row)
    if family == 'request' and bucket == 'dispatch_ambiguous_preserve':
        return 'preserve_in_flight'
    if family == 'request' and bucket == 'erased_preserve_suppression':
        return 'preserve_erasure_suppression'
    if family == 'request':
        return 'preserve_request_identity'
    if family == 'consumption':
        return 'preserve_accounting_evidence'
    if family == 'entitlement':
        return 'retirement_candidate_review_required'
    if family in {'consent', 'consent_operation', 'consent_audit', 'deletion'}:
        return 'preserve_policy_evidence'
    return 'preserve_unknown'


def build_review_plan(value):
    """Return protected review evidence; never propose or execute a mutation."""
    aggregate = plan_v2(value)
    refs = [_record_ref(entry) for entry in value['records']]
    ref_counts = Counter(refs)
    requests, receipts = [], {}
    for entry in value['records']:
        if not isinstance(entry, dict) or set(entry) != {'family', 'item'}:
            continue
        family, row = entry['family'], entry['item']
        if family == 'request' and known_v2(family, row):
            requests.append(row)
        elif family == 'consumption' and known_v2(family, row):
            key = _request_identity(row, 'ANALYSIS#CONSUMPTION#')
            if key is not None:
                receipts.setdefault(key, []).append(row)
    ambiguous_accounting = set()
    for row in requests:
        if row.get('status') != 'COMPLETED':
            continue
        key = _request_identity(row, 'ANALYSIS#REQUEST#')
        candidates = receipts.get(key, []) if key is not None else []
        if len(candidates) != 1 or not _settlement_matches(row, candidates[0]):
            ambiguous_accounting.add(key)
    dispositions = Counter()
    records = []
    for entry, record_ref in zip(value['records'], refs, strict=True):
        if isinstance(entry, dict) and set(entry) == {'family', 'item'}:
            raw_family, row = entry['family'], entry['item']
            family = (raw_family if isinstance(raw_family, str)
                      and raw_family in REQUIRED_FAMILIES else 'unknown')
        else:
            family, row = 'unknown', None
        identity = (_request_identity(row, 'ANALYSIS#REQUEST#') if family == 'request'
                    else _request_identity(row, 'ANALYSIS#CONSUMPTION#')
                    if family == 'consumption' else None)
        disposition = ('preserve_ambiguous_duplicate' if ref_counts[record_ref] > 1
                       else 'preserve_ambiguous_accounting' if identity in ambiguous_accounting
                       else _base_disposition(family, row))
        allowed = ['preserve']
        if disposition == 'retirement_candidate_review_required':
            allowed.append('retire_legacy_access')
        dispositions[disposition] += 1
        records.append({
            'recordRef': record_ref,
            'family': family if isinstance(family, str) else 'unknown',
            'disposition': disposition,
            'allowedProposalActions': allowed,
            'mutationProposed': False,
            'deletionFenceProtected': family == 'deletion',
            'consentEvidenceProtected': family in {'consent', 'consent_operation', 'consent_audit'},
        })
    duplicate_count = sum(count - 1 for count in ref_counts.values() if count > 1)
    blockers = {'apply_executor_absent', 'before_image_vault_absent', 'review_approval_absent'}
    if aggregate['shapeClassifications']['unknown']:
        blockers.add('unknown_shapes_present')
    if aggregate['requestLifecycle']['inFlightAmbiguous']:
        blockers.add('in_flight_records_present')
    if aggregate['requestLifecycle']['unknown']:
        blockers.add('request_accounting_reconciliation_required')
    if dispositions['retirement_candidate_review_required']:
        blockers.add('legacy_access_retirement_decision_required')
    if duplicate_count:
        blockers.add('ambiguous_duplicate_records_present')
    result = {
        'schemaVersion': 1,
        'mode': 'protected_review_only',
        'sourceInventorySha256': aggregate['inputSha256'],
        'recordCount': len(records),
        'duplicateRecordCount': duplicate_count,
        'records': records,
        'dispositionCounts': dict(sorted(dispositions.items())),
        'shapeClassifications': aggregate['shapeClassifications'],
        'requestLifecycle': aggregate['requestLifecycle'],
        'blockers': sorted(blockers),
        'forbiddenOutcomes': list(FORBIDDEN_OUTCOMES),
        'containsRawIdentifiers': False,
        'containsUserContent': False,
        'writesPerformed': False,
        'plannedMutationCount': 0,
        'applyAvailable': False,
        'rollbackReady': False,
        'migrationApproved': False,
    }
    result['reviewPlanSha256'] = hashlib.sha256(_canonical(result)).hexdigest()
    return result


def validate_review_plan(value, saved_plan, *, expected_source_sha256, expected_review_plan_sha256):
    expected = build_review_plan(value)
    try:
        exact_plan = isinstance(saved_plan, dict) and _canonical(saved_plan) == _canonical(expected)
    except (TypeError, ValueError):
        exact_plan = False
    if (not isinstance(expected_source_sha256, str) or HASH.fullmatch(expected_source_sha256) is None
            or not isinstance(expected_review_plan_sha256, str)
            or HASH.fullmatch(expected_review_plan_sha256) is None
            or expected['sourceInventorySha256'] != expected_source_sha256
            or expected['reviewPlanSha256'] != expected_review_plan_sha256
            or not exact_plan):
        raise ValueError('MIGRATION_REVIEW_STALE_OR_EDITED')
    return expected


def validate_proposal(value, saved_plan, proposal, *, expected_source_sha256, expected_review_plan_sha256):
    """Validate review decisions; return aggregate evidence and never apply them."""
    plan = validate_review_plan(
        value, saved_plan, expected_source_sha256=expected_source_sha256,
        expected_review_plan_sha256=expected_review_plan_sha256)
    if (not isinstance(proposal, dict)
            or set(proposal) != {'schemaVersion', 'sourceInventorySha256', 'reviewPlanSha256', 'decisions'}
            or type(proposal.get('schemaVersion')) is not int or proposal['schemaVersion'] != 1
            or proposal.get('sourceInventorySha256') != plan['sourceInventorySha256']
            or proposal.get('reviewPlanSha256') != plan['reviewPlanSha256']
            or not isinstance(proposal.get('decisions'), list)):
        raise ValueError('MIGRATION_PROPOSAL_INVALID')
    expected = Counter(row['recordRef'] for row in plan['records'])
    received = Counter(decision.get('recordRef') for decision in proposal['decisions']
                       if isinstance(decision, dict))
    if received != expected or len(proposal['decisions']) != plan['recordCount']:
        raise ValueError('MIGRATION_PROPOSAL_INVALID')
    plan_by_ref = {}
    for row in plan['records']:
        plan_by_ref.setdefault(row['recordRef'], row)
    action_counts = Counter()
    for decision in proposal['decisions']:
        if (not isinstance(decision, dict)
                or set(decision) != {'recordRef', 'action', 'beforeImageSha256', 'rollbackAction'}):
            raise ValueError('MIGRATION_PROPOSAL_INVALID')
        review = plan_by_ref[decision['recordRef']]
        action = decision.get('action')
        expected_rollback = ('no_change_required' if action == 'preserve'
                             else 'restore_quarantined_evidence_without_authority')
        if (action not in review['allowedProposalActions']
                or decision.get('beforeImageSha256') != decision['recordRef']
                or decision.get('rollbackAction') != expected_rollback
                or review['deletionFenceProtected'] and action != 'preserve'
                or review['consentEvidenceProtected'] and action != 'preserve'):
            raise ValueError('MIGRATION_PROPOSAL_INVALID')
        action_counts[action] += 1
    return {
        'schemaVersion': 1,
        'mode': 'proposal_validation_only',
        'sourceInventorySha256': plan['sourceInventorySha256'],
        'reviewPlanSha256': plan['reviewPlanSha256'],
        'decisionCount': len(proposal['decisions']),
        'actionCounts': dict(sorted(action_counts.items())),
        'beforeImagesDigestBound': True,
        'deletionAndConsentFencesPreserved': True,
        'forbiddenOutcomes': list(FORBIDDEN_OUTCOMES),
        'writesPerformed': False,
        'applyAvailable': False,
        'rollbackReady': False,
        'migrationApproved': False,
    }


def _read(path):
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('MIGRATION_REVIEW_INPUT_INVALID')
    return json.loads(raw, object_pairs_hook=no_duplicates,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def main():
    parser = argparse.ArgumentParser(
        description='Build or validate protected digest-bound decisions; never apply them.')
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--review-plan', type=Path)
    parser.add_argument('--proposal', type=Path)
    parser.add_argument('--expected-source-sha256')
    parser.add_argument('--expected-review-plan-sha256')
    args = parser.parse_args()
    try:
        value = _read(args.input)
        validation_args = (args.review_plan, args.proposal, args.expected_source_sha256,
                           args.expected_review_plan_sha256)
        if any(validation_args) and not all(validation_args):
            raise ValueError('MIGRATION_REVIEW_INPUT_INVALID')
        result = (validate_proposal(
                      value, _read(args.review_plan), _read(args.proposal),
                      expected_source_sha256=args.expected_source_sha256,
                      expected_review_plan_sha256=args.expected_review_plan_sha256)
                  if args.review_plan else build_review_plan(value))
        print(json.dumps(result, sort_keys=True))
    except Exception:
        parser.exit(2, 'MIGRATION_REVIEW_INPUT_INVALID\n')


if __name__ == '__main__':
    main()
