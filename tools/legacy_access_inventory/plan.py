"""Offline, bounded dry-run classification. No AWS SDK or apply operation.

Input is a protected JSON object: {schemaVersion:1, records:[{family,item}]}.
Output is aggregate counts only. Unknown evidence is never normalized into grants.
"""
import argparse
from collections import Counter
from decimal import Decimal
import json
import hashlib
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 10000
CLASSIFIER_VERSION = 'legacy-preservation-v1'
REQUIRED_FAMILIES = {'request', 'consumption', 'entitlement', 'consent', 'consent_operation',
                    'consent_audit', 'withdrawal', 'purchase_token', 'purchase_binding',
                    'current_authority', 'deletion'}


def exact_int(value):
    return isinstance(value, (int, Decimal)) and not isinstance(value, bool) and value >= 0 and value == int(value)


def classify(family, row):
    if not isinstance(family, str) or not isinstance(row, dict) or not isinstance(row.get('PK'), str) or not isinstance(row.get('SK'), str):
        return 'unknown_shape'
    if family == 'request':
        if not row['PK'].startswith('ANALYSIS#REQUEST#'):
            return 'unknown_shape'
        if row.get('status') in ('PROCESSING', 'RETRYABLE'):
            return 'dispatch_ambiguous_preserve'
        if row.get('status') == 'RESULT_READY':
            return 'result_ready_accounting_unproven_preserve'
        if row.get('status') == 'COMPLETED_ERASED':
            return 'erased_preserve_suppression'
        if row.get('status') == 'COMPLETED':
            # Not a replay approval: handler must independently cross-check the
            # original owned consumption/History/account/device evidence.
            return 'completed_requires_replay_evidence'
    elif family == 'consumption':
        if (row['PK'].startswith('ANALYSIS#CONSUMPTION#') and row.get('consumptionType') in ('monthly', 'credit')
                and exact_int(row.get('expiresAt'))):
            return 'consumption_preserve_original_accounting'
    elif family == 'entitlement':
        if (row['PK'].startswith('USER#') and row['SK'] in {'ENTITLEMENT', 'ENTITLEMENT#google_play#trustcheck_radar_pro_monthly'}
                and row.get('entitlementTier') in ('FREE', 'PRO')
                and all(exact_int(row.get(k)) for k in ('monthlyScanLimit', 'remainingMonthlyScans', 'remainingCredits'))
                and row['remainingMonthlyScans'] <= row['monthlyScanLimit']):
            return 'legacy_' + row['entitlementTier'].lower() + '_preserve_no_grant_conversion'
    elif family == 'consent':
        if row['PK'].startswith('USER#') and row['SK'] == 'CAMPAIGN_PARTICIPATION':
            if row.get('state') == 'withdrawal_pending':
                return 'withdrawal_pending_preserve_epoch_and_deadline'
            if row.get('state') == 'withdrawn':
                return 'withdrawn_never_reenroll_automatically'
            if row.get('state') == 'enrolled':
                return 'enrolled_requires_version_qualification'
    elif family == 'purchase_token' and row['PK'].startswith('TOKEN#') and row['SK'] == 'IDEMPOTENCY':
        return 'purchase_token_preserve_owner_and_verification'
    elif family == 'purchase_binding' and row['PK'].startswith('USER#') and row['SK'].startswith('PURCHASE_TOKEN#'):
        return 'purchase_locator_preserve_owned_lineage'
    elif family == 'consent_operation' and row['PK'].startswith('USER#') and row['SK'].startswith('CAMPAIGN_OPERATION#'):
        return 'consent_operation_preserve_exact_retry_identity'
    elif family == 'consent_audit':
        return 'consent_audit_preserve_review_separately'
    elif family == 'withdrawal' and row['SK'].startswith('CAMPAIGN_WITHDRAWAL#'):
        return 'withdrawal_command_preserve_original_deadline'
    elif family == 'current_authority':
        # This planner has no authority to reinterpret current grants/trial state.
        return 'current_authority_preserve_review_separately'
    elif family == 'deletion' and row['PK'].startswith('ACCOUNT#') and row['SK'] == 'ACCOUNT_DELETION':
        return 'deletion_fence_preserve_precedence'
    return 'unknown_shape'


def plan(value, *, raw_input_sha256=None):
    if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'records'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or not isinstance(value['records'], list) or len(value['records']) > MAX_RECORDS):
        raise ValueError('INVENTORY_INPUT_INVALID')
    counts = Counter()
    seen = set()
    for entry in value['records']:
        if not isinstance(entry, dict) or set(entry) != {'family', 'item'}:
            counts['unknown_shape'] += 1
        else:
            bucket = classify(entry['family'], entry['item'])
            counts[bucket] += 1
            if bucket != 'unknown_shape':
                seen.add(entry['family'])
    digest = raw_input_sha256 or hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    return {'schemaVersion': 1, 'mode': 'dry_run_only', 'classifierVersion': CLASSIFIER_VERSION,
            'inputSha256': digest, 'digestKind': 'source_bytes' if raw_input_sha256 else 'canonical_json',
            'missingRequiredFamilies': sorted(REQUIRED_FAMILIES - seen), 'inputRecordCount': len(value['records']),
            'classifications': dict(sorted(counts.items())), 'applyAvailable': False,
            'inventoryComplete': False, 'replayQualified': False, 'migrationApproved': False}


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('INVENTORY_INPUT_INVALID')
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description='Read a protected inventory; print aggregate dry-run counts only.')
    parser.add_argument('--input', type=Path, required=True)
    args = parser.parse_args()
    try:
        with args.input.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('INVENTORY_INPUT_INVALID')
        value = json.loads(raw, object_pairs_hook=no_duplicates, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        print(json.dumps(plan(value, raw_input_sha256=hashlib.sha256(raw).hexdigest()), sort_keys=True))
    except Exception:
        parser.exit(2, 'INVENTORY_INPUT_INVALID\n')


if __name__ == '__main__':
    main()
