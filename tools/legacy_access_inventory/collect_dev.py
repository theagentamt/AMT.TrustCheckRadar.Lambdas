"""Bounded Dev-only DynamoDB inventory; emits aggregate preservation evidence only.

The collector has no mutation or apply operation. Raw projected rows remain in memory
only and are reduced by plan_v2 before stdout is written.
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan import MAX_RECORDS, plan_v2  # noqa: E402

PROJECTION = ('PK,SK,recordType,schemaVersion,#state,#status,basis,policyVersion,validFromEpoch,'
              'validUntilEpoch,periodId,periodRevision,entitlementTier,subscriptionStatus,'
              'monthlyScanLimit,remainingMonthlyScans,remainingCredits,isAccessGranted,'
              'expiresAt,#ttl,leaseExpiresAt,consumptionType,createdAt,completedAt,'
              'resultReadyAt,payloadHash,accountIdHash,noticeVersion,environment,stateVersion')
NAMES = {'#state': 'state', '#status': 'status', '#ttl': 'ttl'}


def decode(value):
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError('INVENTORY_RESPONSE_INVALID')
    kind, data = next(iter(value.items()))
    if kind == 'S': return data
    if kind == 'N': return Decimal(data)
    if kind == 'BOOL': return data
    if kind == 'NULL' and data is True: return None
    if kind == 'L': return [decode(item) for item in data]
    if kind == 'M': return {key: decode(item) for key, item in data.items()}
    if kind == 'SS': return set(data)
    if kind == 'NS': return {Decimal(item) for item in data}
    raise ValueError('INVENTORY_RESPONSE_INVALID')


def scan(client, table_name, *, filter_expression=None, expression_values=None):
    kwargs = {'TableName': table_name, 'ProjectionExpression': PROJECTION,
              'ExpressionAttributeNames': NAMES, 'ConsistentRead': False}
    if filter_expression:
        kwargs['FilterExpression'] = filter_expression
        kwargs['ExpressionAttributeValues'] = expression_values
    rows, pages, scanned = [], 0, 0
    while True:
        response = client.scan(**kwargs)
        pages += 1; scanned += response.get('ScannedCount', 0)
        rows.extend({key: decode(value) for key, value in item.items()}
                    for item in response.get('Items', []))
        if len(rows) > MAX_RECORDS or pages > 100:
            raise ValueError('INVENTORY_BOUNDS_EXCEEDED')
        key = response.get('LastEvaluatedKey')
        if not key: break
        kwargs['ExclusiveStartKey'] = key
    return rows, {'returnedCount': len(rows), 'scannedCount': scanned,
                  'pages': pages, 'paginationComplete': True}


def family(table, row):
    pk, sk = row.get('PK'), row.get('SK')
    if not isinstance(pk, str) or not isinstance(sk, str): return 'unknown'
    if table == 'requests':
        if pk.startswith('ANALYSIS#REQUEST#'): return 'request'
        if pk.startswith('ANALYSIS#CONSUMPTION#'): return 'consumption'
        return 'unknown'
    if table == 'authority':
        if pk.startswith('USER#') and sk in {'ENTITLEMENT', 'ENTITLEMENT#google_play#trustcheck_radar_pro_monthly'}: return 'entitlement'
        if pk.startswith('TOKEN#') and sk == 'IDEMPOTENCY': return 'purchase_token'
        if pk.startswith('USER#') and sk.startswith('PURCHASE_TOKEN#'): return 'purchase_binding'
        return 'current_authority'
    if table == 'consent':
        if sk == 'CAMPAIGN_PARTICIPATION': return 'consent'
        if sk.startswith('CAMPAIGN_OPERATION#'): return 'consent_operation'
        if sk.startswith('CAMPAIGN_CONSENT#'): return 'consent_audit'
        return 'unknown'
    if table == 'deletion':
        if sk == 'ACCOUNT_DELETION': return 'deletion'
        if sk.startswith('CAMPAIGN_WITHDRAWAL#'): return 'withdrawal'
    return 'unknown'


def collect(client, table_names, observed_at_epoch):
    if set(table_names) != {'requests', 'authority', 'consent', 'deletion'}:
        raise ValueError('INVENTORY_TABLES_INVALID')
    if any(not isinstance(name, str) or 'trustcheckradar-dev-' not in name for name in table_names.values()):
        raise ValueError('DEV_TABLE_REQUIRED')
    definitions = {
        'requests': (None, None), 'authority': (None, None),
        'consent': ('SK = :p OR begins_with(SK, :o) OR begins_with(SK, :a)',
                    {':p': {'S': 'CAMPAIGN_PARTICIPATION'}, ':o': {'S': 'CAMPAIGN_OPERATION#'}, ':a': {'S': 'CAMPAIGN_CONSENT#'}}),
        'deletion': ('SK = :d OR begins_with(SK, :w)',
                     {':d': {'S': 'ACCOUNT_DELETION'}, ':w': {'S': 'CAMPAIGN_WITHDRAWAL#'}}),
    }
    entries, coverage = [], {}
    for logical in ('requests', 'authority', 'consent', 'deletion'):
        filt, values = definitions[logical]
        rows, coverage[logical] = scan(client, table_names[logical],
                                       filter_expression=filt, expression_values=values)
        entries.extend({'family': family(logical, row), 'item': row} for row in rows)
        if len(entries) > MAX_RECORDS: raise ValueError('INVENTORY_BOUNDS_EXCEEDED')
    result = plan_v2({'schemaVersion': 2, 'observedAtEpoch': observed_at_epoch, 'records': entries})
    result['sourceCoverage'] = coverage
    result['sourceEnvironment'] = 'dev'
    result['identifiersIncluded'] = False
    result['contentIncluded'] = False
    result['writesPerformed'] = False
    return result


def main():
    parser = argparse.ArgumentParser(description='Read Dev projections and print aggregate-only preservation evidence.')
    for logical in ('requests', 'authority', 'consent', 'deletion'):
        parser.add_argument('--' + logical + '-table', required=True)
    parser.add_argument('--region', default='us-east-1')
    args = parser.parse_args()
    try:
        import boto3
        client = boto3.client('dynamodb', region_name=args.region)
        tables = {name: getattr(args, name + '_table') for name in ('requests', 'authority', 'consent', 'deletion')}
        print(json.dumps(collect(client, tables, int(time.time())), sort_keys=True, default=str))
    except Exception:
        parser.exit(2, 'INVENTORY_COLLECTION_FAILED\n')


if __name__ == '__main__':
    main()
