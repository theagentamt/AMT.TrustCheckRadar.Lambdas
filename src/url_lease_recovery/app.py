"""Scheduled bounded lease and explicit-expiry cleanup; no secrets/providers."""
import json
import os
import time


def lambda_handler(event, context):
    if os.environ.get('STAGE') != 'dev' or os.environ.get('LEASE_SWEEP_ENABLED') != 'true':
        return {'enabled': False, 'recovered': 0}
    if event != {'schemaVersion': 1}:
        return {'enabled': True, 'rejected': True}
    try:
        return _sweep(context)
    except Exception:
        print(json.dumps({'event': 'url_lease_recovery', 'failed': 1, 'reason': 'RECOVERY_UNAVAILABLE'}))
        raise RuntimeError('RECOVERY_UNAVAILABLE') from None


def _save_cursor(resource, table, key, current, cursor):
    from shared_check_authority.core import integral
    revision = integral(current.get('revision', 0))
    if revision is None or revision < 0:
        raise ValueError()
    operation = {'TableName': table, 'Key': key,
                 'UpdateExpression': 'SET #cursor = :cursor, revision = :next',
                 'ConditionExpression': 'attribute_not_exists(revision)' if revision == 0 else 'revision = :old',
                 'ExpressionAttributeNames': {'#cursor': 'cursor'},
                 'ExpressionAttributeValues': {':cursor': cursor, ':next': revision + 1}}
    if revision:
        operation['ExpressionAttributeValues'][':old'] = revision
    resource.meta.client.transact_write_items(TransactItems=[{'Update': operation}])


def _oldest_overdue(resource, table, index, now):
    # Inspect the queue head independently of the fair continuation cursor, so a
    # poison oldest row remains visible in the alarm metric while later rows drain.
    page = resource.Table(table).query(IndexName='GSI1', KeyConditionExpression='GSI1PK = :index AND GSI1SK <= :deadline',
                                      ExpressionAttributeValues={':index': index, ':deadline': f'{now:012d}~'}, Limit=1)
    rows = page.get('Items', [])
    if not rows:
        return 0
    value = rows[0].get('GSI1SK', '')
    if not isinstance(value, str) or len(value) < 13 or not value[:12].isdigit() or value[12] != '#':
        return 0  # Malformed queue entries are counted by the bounded sweep.
    return max(0, now - int(value[:12]))


def run_passes(resource, table, context, *, now):
    """Independent durable cursors and time budgets prevent one queue starving the other."""
    from shared_check_authority.recovery import Recovery
    from shared_check_authority.expiry import Expiry
    if context is None or not callable(getattr(context, 'get_remaining_time_in_millis', None)):
        raise ValueError()
    remaining = context.get_remaining_time_in_millis
    initial = remaining()
    if type(initial) is not int or initial < 8000:
        raise ValueError()
    counts = {'examined': 0, 'recovered': 0, 'expiredDeleted': 0, 'failed': 0, 'pages': 0,
              'leaseExamined': 0, 'expiryExamined': 0, 'oldestOverdueSeconds': 0,
              'leaseOldestOverdueSeconds': 0, 'expiryOldestOverdueSeconds': 0}
    passes = [('LEASE_SWEEP_CURSOR', 'V1_PENDING', Recovery(resource, table, now=now), max(2000, initial // 2)),
              ('EXPIRY_SWEEP_CURSOR', 'V1_EXPIRING', Expiry(resource, table, now=now), 1500)]
    for sort_key, index, worker, reserve in passes:
        if remaining() <= reserve + 3000:
            continue
        try:
            key = {'PK': 'V1#CONTROL', 'SK': sort_key}
            current = resource.Table(table).get_item(Key=key, ConsistentRead=True).get('Item', {})
            overdue = _oldest_overdue(resource, table, index, now())
            # Leave a bounded transaction timeout to checkpoint the last attempted row.
            result = worker.sweep(cursor=current.get('cursor'), page_size=5, max_pages=2,
                                  can_continue=lambda: remaining() > reserve + 3000)
            _save_cursor(resource, table, key, current, result['cursor'])
        except Exception:
            counts['failed'] += 1
            continue
        counts['examined'] += result['examined']
        counts['failed'] += result['failed']
        counts['pages'] += result['pages']
        if index == 'V1_PENDING':
            counts['recovered'] = result['recovered']
            counts['leaseExamined'] = result['examined']
            counts['leaseOldestOverdueSeconds'] = overdue
        else:
            counts['expiredDeleted'] = result['deleted']
            counts['expiryExamined'] = result['examined']
            counts['expiryOldestOverdueSeconds'] = overdue
        counts['oldestOverdueSeconds'] = max(counts['oldestOverdueSeconds'], overdue)
    return counts


def _sweep(context):
    import boto3
    from botocore.config import Config
    resource = boto3.resource('dynamodb', region_name='us-east-1',
                              config=Config(connect_timeout=0.2, read_timeout=0.3, retries={'total_max_attempts': 1}))
    counts = run_passes(resource, os.environ['AUTHORITY_TABLE_NAME'], context, now=lambda: int(time.time()))
    print(json.dumps({'event': 'url_lease_recovery', **counts}))
    return counts
