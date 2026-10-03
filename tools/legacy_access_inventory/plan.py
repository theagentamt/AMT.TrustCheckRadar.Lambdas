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
import re

MAX_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 10000
CLASSIFIER_VERSION = 'legacy-preservation-v1'
CLASSIFIER_VERSION_V2 = 'legacy-preservation-v2'
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


CURRENT_RECORD_TYPES = {
    'PURCHASE_OWNERSHIP_INVENTORY', 'V1_ACCESS_AUTHORITY', 'V1_ALLOWANCE_PERIOD',
    'V1_ATTEMPT_COUNTER', 'V1_AUTHORITY_AUDIT', 'V1_CHECK_RECEIPT',
    'V1_HMAC_KEY_INVENTORY', 'V1_MESSAGE_PROVIDER_BUDGET', 'V1_PREPARATION',
    'V1_TRIAL_ELIGIBILITY',
}

REQUEST_COMMON = {'PK', 'SK', 'status', 'payloadHash', 'createdAt', 'updatedAt', 'expiresAt', 'ttl'}
REQUEST_OPTIONAL = {'historyAuthorization', 'campaignAuthorization', 'statisticsEventId'}
REQUEST_MARKERS = {'recordType', 'schemaVersion'}
CONSUMPTION_FIELDS = {'PK', 'SK', 'accountIdHash', 'consumptionType', 'createdAt', 'expiresAt', 'ttl'}
ENTITLEMENT_FIELDS = {'PK', 'SK', 'accountId', 'billingPeriodEndUtc', 'billingPeriodStartUtc',
                      'createdAt', 'entitlementTier', 'isAccessGranted', 'lastVerifiedAtUtc',
                      'monthlyScanLimit', 'platform', 'productId', 'remainingCredits',
                      'remainingMonthlyScans', 'subscriptionStatus', 'updatedAt'}
ENTITLEMENT_SCAN_FIELDS = {'lastScanAt', 'lastScanConsumptionType', 'lastScanRequestId'}
CONSENT_FIELDS = {'PK', 'SK', 'consentEpochId', 'effectiveFrom', 'environment', 'lastOperationId',
                  'noticeVersion', 'policyVersion', 'recordVersion', 'schemaVersion', 'state',
                  'stateVersion', 'updatedAt'}
CONSENT_OPERATION_FIELDS = {'PK', 'SK', 'action', 'consentEpochId', 'expiresAt', 'occurredAt',
                            'operationId', 'recordVersion', 'resultingState', 'schemaVersion'}
CONSENT_AUDIT_FIELDS = {'PK', 'SK', 'consentEpochId', 'effectiveMonthlyScanLimit', 'eventType',
                        'expiresAt', 'noticeVersion', 'occurredAt', 'operationId', 'policyVersion',
                        'recordVersion', 'resultingState', 'schemaVersion', 'stateVersion'}
DELETION_FIELDS = {'PK', 'SK', 'accountId', 'completedAtEpoch', 'deleteByEpoch', 'environment',
                   'eventType', 'occurredAtEpoch', 'operationId', 'recordVersion',
                   'retainUntilEpoch', 'schemaVersion', 'status'}
HASH = re.compile(r'^[0-9a-f]{64}$')


def _legacy_markers(row, record_type):
    present = REQUEST_MARKERS & set(row)
    return (not present or present == REQUEST_MARKERS
            and row.get('recordType') == record_type
            and type(row.get('schemaVersion')) is int and row['schemaVersion'] == 1)


def _request_shape(row):
    if not isinstance(row, dict) or not _legacy_markers(row, 'LEGACY_ANALYSIS_REQUEST'):
        return False
    fields, status = set(row), row.get('status')
    allowed = REQUEST_COMMON | REQUEST_OPTIONAL | REQUEST_MARKERS
    required = REQUEST_COMMON.copy()
    if status == 'PROCESSING':
        required |= {'leaseToken', 'leaseExpiresAt'}; allowed |= {'leaseToken', 'leaseExpiresAt'}
    elif status == 'RETRYABLE':
        pass
    elif status == 'RESULT_READY':
        required |= {'response', 'resultReadyAt'}; allowed |= {'response', 'resultReadyAt'}
    elif status == 'COMPLETED':
        required |= {'response', 'resultReadyAt', 'completedAt'}
        allowed |= {'response', 'resultReadyAt', 'completedAt'}
    elif status == 'COMPLETED_ERASED':
        # Redaction may leave original timestamps/authorization but never response.
        allowed |= {'resultReadyAt', 'completedAt'}
    else:
        return False
    if not required <= fields or not fields <= allowed or 'response' in row and not isinstance(row['response'], dict):
        return False
    if ('campaignAuthorization' in row) != ('statisticsEventId' in row):
        return False
    return (isinstance(row.get('PK'), str) and row['PK'].startswith('ANALYSIS#REQUEST#')
            and HASH.fullmatch(row['PK'].removeprefix('ANALYSIS#REQUEST#')) is not None
            and isinstance(row.get('SK'), str) and bool(row['SK'])
            and isinstance(row.get('payloadHash'), str) and HASH.fullmatch(row['payloadHash']) is not None
            and exact_int(row.get('expiresAt')) and row.get('ttl') == row['expiresAt']
            and all(isinstance(row.get(name), str) for name in ('createdAt', 'updatedAt')))


def _consumption_shape(row):
    if not isinstance(row, dict) or not _legacy_markers(row, 'LEGACY_ANALYSIS_CONSUMPTION'):
        return False
    fields = set(row)
    if fields not in (CONSUMPTION_FIELDS, CONSUMPTION_FIELDS | REQUEST_MARKERS):
        return False
    owner = row.get('PK', '').removeprefix('ANALYSIS#CONSUMPTION#') if isinstance(row.get('PK'), str) else ''
    return (row.get('PK', '').startswith('ANALYSIS#CONSUMPTION#') and HASH.fullmatch(owner) is not None
            and row.get('accountIdHash') == owner and isinstance(row.get('SK'), str) and bool(row['SK'])
            and row.get('consumptionType') in ('monthly', 'credit')
            and isinstance(row.get('createdAt'), str) and exact_int(row.get('expiresAt'))
            and row.get('ttl') == row['expiresAt'])


def _entitlement_shape(row):
    if not isinstance(row, dict): return False
    fields = set(row)
    if fields not in (ENTITLEMENT_FIELDS, ENTITLEMENT_FIELDS | ENTITLEMENT_SCAN_FIELDS): return False
    owner = row.get('PK', '').removeprefix('USER#') if isinstance(row.get('PK'), str) else ''
    return (bool(owner) and row.get('accountId') == owner
            and row.get('SK') in {'ENTITLEMENT', 'ENTITLEMENT#google_play#trustcheck_radar_pro_monthly'}
            and row.get('entitlementTier') in ('FREE', 'PRO')
            and all(exact_int(row.get(k)) for k in ('monthlyScanLimit', 'remainingMonthlyScans', 'remainingCredits'))
            and row['remainingMonthlyScans'] <= row['monthlyScanLimit']
            and isinstance(row.get('isAccessGranted'), bool))


def known_v2(family, row):
    """Recognize only preservation-safe shapes; recognition never authorizes apply."""
    if family == 'request': return _request_shape(row)
    if family == 'consumption': return _consumption_shape(row)
    if family == 'entitlement': return _entitlement_shape(row)
    if not isinstance(row, dict): return False
    if family == 'consent': return set(row) == CONSENT_FIELDS and classify(family, row) != 'unknown_shape'
    if family == 'consent_operation': return set(row) == CONSENT_OPERATION_FIELDS and classify(family, row) != 'unknown_shape'
    if family == 'consent_audit': return set(row) == CONSENT_AUDIT_FIELDS and row.get('PK', '').startswith('USER#') and row.get('SK', '').startswith('CAMPAIGN_CONSENT#')
    if family == 'deletion': return set(row) == DELETION_FIELDS and classify(family, row) != 'unknown_shape'
    # Current authority and remaining families are inventoried but deliberately
    # never schema-qualified by this legacy migration planner.
    return False


def _request_identity(row, prefix):
    if not isinstance(row, dict) or not isinstance(row.get('PK'), str) or not row['PK'].startswith(prefix):
        return None
    if not isinstance(row.get('SK'), str):
        return None
    return row['PK'][len(prefix):], row['SK']


def _settlement_matches(request, receipt):
    if not known_v2('request', request) or not known_v2('consumption', receipt):
        return False
    identity = _request_identity(request, 'ANALYSIS#REQUEST#')
    consumed = _request_identity(receipt, 'ANALYSIS#CONSUMPTION#')
    return (identity is not None and identity == consumed
            and receipt.get('accountIdHash') == identity[0]
            and receipt.get('consumptionType') in ('monthly', 'credit')
            and exact_int(request.get('expiresAt'))
            and exact_int(request.get('ttl'))
            and exact_int(receipt.get('expiresAt'))
            and exact_int(receipt.get('ttl'))
            and request['expiresAt'] == request['ttl'] == receipt['expiresAt'] == receipt['ttl']
            and isinstance(request.get('completedAt'), str)
            and receipt.get('createdAt') == request['completedAt'])


def plan_v2(value, *, raw_input_sha256=None):
    if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'observedAtEpoch', 'records'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 2
            or not exact_int(value['observedAtEpoch'])
            or not isinstance(value['records'], list) or len(value['records']) > MAX_RECORDS):
        raise ValueError('INVENTORY_INPUT_INVALID')
    observed = int(value['observedAtEpoch'])
    counts, seen = Counter(), set()
    known = unknown = expired = 0
    requests, receipts = [], {}
    access = Counter({'legacyBonus': 0, 'legacyFree': 0, 'legacyPaid': 0,
                      'currentAccess': 0, 'currentSupporting': 0, 'unknown': 0})
    for entry in value['records']:
        if not isinstance(entry, dict) or set(entry) != {'family', 'item'}:
            counts['unknown_shape'] += 1; unknown += 1
            continue
        family, row = entry['family'], entry['item']
        raw_bucket = classify(family, row)
        is_known = known_v2(family, row)
        bucket = raw_bucket if is_known else 'unknown_shape'
        counts[bucket] += 1
        if is_known:
            known += 1; seen.add(family)
        else:
            unknown += 1
        if isinstance(row, dict) and exact_int(row.get('expiresAt')) and row['expiresAt'] <= observed:
            expired += 1
        if family == 'request':
            requests.append(row)
        elif family == 'consumption':
            key = _request_identity(row, 'ANALYSIS#CONSUMPTION#')
            if key is not None: receipts.setdefault(key, []).append(row)
        elif family == 'entitlement':
            if is_known and bucket.startswith('legacy_free_'):
                access['legacyBonus'] += int(row.get('monthlyScanLimit') == 15)
                access['legacyFree'] += int(row.get('monthlyScanLimit') != 15)
            elif is_known and bucket.startswith('legacy_pro_'):
                access['legacyPaid'] += 1
            else:
                access['unknown'] += 1
        elif family == 'current_authority':
            record_type = row.get('recordType') if isinstance(row, dict) else None
            access['currentAccess'] += int(record_type == 'V1_ACCESS_AUTHORITY')
            access['currentSupporting'] += int(record_type in CURRENT_RECORD_TYPES - {'V1_ACCESS_AUTHORITY'})
            access['unknown'] += int(record_type not in CURRENT_RECORD_TYPES)
    lifecycle = Counter({'neverDispatched': 0, 'inFlightAmbiguous': 0, 'settled': 0,
                         'erased': 0, 'unknown': 0})
    for row in requests:
        if not isinstance(row, dict): lifecycle['unknown'] += 1; continue
        if not known_v2('request', row): lifecycle['unknown'] += 1; continue
        status = row.get('status')
        if status in ('PROCESSING', 'RETRYABLE', 'RESULT_READY'):
            lifecycle['inFlightAmbiguous'] += 1
        elif status == 'COMPLETED':
            key = _request_identity(row, 'ANALYSIS#REQUEST#')
            candidates = receipts.get(key, []) if key is not None else []
            if len(candidates) == 1 and _settlement_matches(row, candidates[0]):
                lifecycle['settled'] += 1
            else:
                lifecycle['unknown'] += 1
        elif status == 'COMPLETED_ERASED':
            lifecycle['erased'] += 1
        else:
            lifecycle['unknown'] += 1
    def decimal(value):
        if exact_int(value): return int(value)
        raise TypeError('INVENTORY_INPUT_INVALID')
    digest = raw_input_sha256 or hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(',', ':'), allow_nan=False, default=decimal
    ).encode()).hexdigest()
    return {
        'schemaVersion': 2, 'mode': 'dry_run_only', 'classifierVersion': CLASSIFIER_VERSION_V2,
        'inputSha256': digest, 'digestKind': 'source_bytes' if raw_input_sha256 else 'canonical_json',
        'observedAtEpoch': observed, 'inputRecordCount': len(value['records']),
        'shapeClassifications': {'known': known, 'unknown': unknown},
        'requestLifecycle': dict(lifecycle), 'expiredRecordCount': expired,
        'legacyAccess': dict(sorted(access.items())),
        'preservationClassifications': dict(sorted(counts.items())),
        'missingRequiredFamilies': sorted(REQUIRED_FAMILIES - seen),
        'neverDispatchedProofAvailable': False,
        'unknownRecovery': 'preserve_source_and_require_explicit_review',
        'applyAvailable': False, 'inventoryComplete': False,
        'replayQualified': False, 'migrationApproved': False,
    }


def plan(value, *, raw_input_sha256=None):
    if isinstance(value, dict) and value.get('schemaVersion') == 2:
        return plan_v2(value, raw_input_sha256=raw_input_sha256)
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
