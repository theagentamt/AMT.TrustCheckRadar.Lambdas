"""Minimized purchase-period accounting; no account identity or submitted content.

The approved deadline is the verified access window plus seven days; funded
period identity and counters remain unchanged by an access extension.
Every mutation below is a native action for a caller-owned atomic transaction.
"""
import re
from .core import AuthorityError, OWNER_POLICY, PAID_COMPLETED_CHECKS, integral

RETENTION_SECONDS = 604800
V1_FIELDS = {'PK', 'SK', 'recordType', 'schemaVersion', 'policyVersion', 'startEpoch',
          'endEpoch', 'limit', 'usedChecks', 'reservedChecks', 'expiresAt', 'GSI1PK', 'GSI1SK'}
FIELDS = V1_FIELDS | {'accessUntilEpoch'}


def key(root_digest, order_digest):
    if any(not isinstance(x, str) or not re.fullmatch(r'[0-9a-f]{64}', x) for x in (root_digest, order_digest)):
        raise AuthorityError('PURCHASE_USAGE_INVALID')
    return {'PK': 'V1#PURCHASE_USAGE#' + root_digest, 'SK': 'PERIOD#' + order_digest}


def valid_key(value):
    if (not isinstance(value, dict) or set(value) != {'PK', 'SK'}
            or not isinstance(value['PK'], str) or not re.fullmatch(r'V1#PURCHASE_USAGE#[0-9a-f]{64}', value['PK'])
            or not isinstance(value['SK'], str) or not re.fullmatch(r'PERIOD#[0-9a-f]{64}', value['SK'])):
        raise AuthorityError('PURCHASE_USAGE_INVALID')
    return dict(value)


def _bounds(start, end):
    if integral(start) is None or integral(end) is None or not 0 <= start < end:
        raise AuthorityError('PURCHASE_USAGE_INVALID')


def effective_access_end(period):
    if not isinstance(period, dict):
        raise AuthorityError('PURCHASE_USAGE_INVALID')
    start = integral(period.get('startEpoch'))
    end = integral(period.get('endEpoch'))
    access = integral(period.get('accessUntilEpoch', period.get('endEpoch')))
    if start is None or end is None or access is None or not 0 <= start < end or access < start:
        raise AuthorityError('PURCHASE_USAGE_INVALID')
    return access


def usage_deadline(period):
    return effective_access_end(period) + RETENTION_SECONDS


def new_period_usage(pointer, start, end, *, access_until_epoch=None):
    pointer = valid_key(pointer)
    _bounds(start, end)
    access = end if access_until_epoch is None else access_until_epoch
    effective_access_end({'startEpoch': start, 'endEpoch': end, 'accessUntilEpoch': access})
    deadline = access + RETENTION_SECONDS
    return pointer | {'recordType': 'V1_PURCHASE_USAGE', 'schemaVersion': 2, 'policyVersion': OWNER_POLICY,
                      'startEpoch': start, 'endEpoch': end, 'accessUntilEpoch': access, 'limit': PAID_COMPLETED_CHECKS,
                      'usedChecks': 0, 'reservedChecks': 0, 'expiresAt': deadline,
                      'GSI1PK': 'V1_EXPIRING', 'GSI1SK': f'{int(deadline):012d}#{pointer["PK"]}#{pointer["SK"]}'}


def validate(row, pointer, now, *, allow_expired=False):
    pointer = valid_key(pointer)
    version = integral(row.get('schemaVersion')) if isinstance(row, dict) else None
    fields = V1_FIELDS if version == 1 else FIELDS
    if (not isinstance(row, dict) or version not in (1, 2) or set(row) != fields
            or any(row.get(k) != v for k, v in pointer.items())
            or row.get('recordType') != 'V1_PURCHASE_USAGE'
            or row.get('policyVersion') != OWNER_POLICY or integral(row.get('limit')) != PAID_COMPLETED_CHECKS
            or any(integral(row.get(k)) is None for k in ('startEpoch', 'endEpoch', 'usedChecks', 'reservedChecks', 'expiresAt'))):
        raise AuthorityError('PURCHASE_USAGE_INVALID')
    _bounds(row['startEpoch'], row['endEpoch'])
    if (row['usedChecks'] < 0 or row['reservedChecks'] < 0
            or row['usedChecks'] + row['reservedChecks'] > PAID_COMPLETED_CHECKS
            or row['expiresAt'] != usage_deadline(row)
            or row['GSI1PK'] != 'V1_EXPIRING'
            or row['GSI1SK'] != f'{int(row["expiresAt"]):012d}#{pointer["PK"]}#{pointer["SK"]}'
            or type(now) is not int or now < 0
            or not allow_expired and now >= row['expiresAt']):
        raise AuthorityError('PURCHASE_USAGE_INVALID')
    return row


def read(resource, table, pointer, *, now, allow_expired=False):
    pointer = valid_key(pointer)
    row = resource.Table(table).get_item(Key=pointer, ConsistentRead=True).get('Item')
    if row is None:
        raise AuthorityError('PURCHASE_USAGE_UNAVAILABLE')
    return validate(row, pointer, now, allow_expired=allow_expired)


def exact_condition(row):
    fields = sorted(row)
    return {'ConditionExpression': ' AND '.join(f'#f{i} = :v{i}' for i in range(len(fields))),
            'ExpressionAttributeNames': {f'#f{i}': name for i, name in enumerate(fields)},
            'ExpressionAttributeValues': {f':v{i}': row[name] for i, name in enumerate(fields)}}


def access_window_actions(table, pointer, observed_global, access_until_epoch, *, now):
    """Trusted fresh-proof caller only; compose with local period/source fencing.

    There is no expiry inference from event time. Shortening cannot discard an
    unresolved reservation. Already-due zero-reservation rows are atomically
    deleted, requiring an explicitly qualified caller Delete permission.
    """
    observed = validate(observed_global, pointer, now, allow_expired=True)
    proposed = new_period_usage(pointer, observed['startEpoch'], observed['endEpoch'],
                                access_until_epoch=access_until_epoch)
    proposed.update({k: observed[k] for k in ('usedChecks', 'reservedChecks')})
    old_access = effective_access_end(observed)
    if access_until_epoch > old_access and now >= observed['expiresAt']:
        raise AuthorityError('PURCHASE_USAGE_RECONCILIATION_REQUIRED')
    if access_until_epoch < old_access and observed['reservedChecks'] != 0:
        raise AuthorityError('PURCHASE_USAGE_RECONCILIATION_REQUIRED')
    condition = exact_condition(observed)
    if proposed['expiresAt'] <= now:
        if observed['reservedChecks'] != 0:
            raise AuthorityError('PURCHASE_USAGE_RECONCILIATION_REQUIRED')
        return [{'Delete': {'TableName': table, 'Key': valid_key(pointer), **condition}}]
    if proposed == observed:
        return [{'ConditionCheck': {'TableName': table, 'Key': valid_key(pointer), **condition}}]
    return [{'Put': {'TableName': table, 'Item': proposed, **condition}}]


def funding_actions(table, pointer, start, end, observed_global, *, now, access_until_epoch=None):
    access = end if access_until_epoch is None else access_until_epoch
    initial = new_period_usage(pointer, start, end, access_until_epoch=access)
    if type(now) is not int or access < end or not start <= now < access:
        raise AuthorityError('PURCHASE_USAGE_INVALID')
    if observed_global is None:
        if access != end:
            raise AuthorityError('PURCHASE_USAGE_RECONCILIATION_REQUIRED')
        return ([{'Put': {'TableName': table, 'Item': initial, 'ConditionExpression': 'attribute_not_exists(PK)'}}],
                {'usedChecks': 0, 'reservedChecks': 0})
    observed = validate(observed_global, pointer, now)
    if observed['startEpoch'] != start or observed['endEpoch'] != end:
        raise AuthorityError('PURCHASE_USAGE_INVALID')
    return (access_window_actions(table, pointer, observed, access, now=now),
            {name: observed[name] for name in ('usedChecks', 'reservedChecks')})


def validate_period(period):
    if (not isinstance(period, dict) or period.get('recordType') != 'V1_ALLOWANCE_PERIOD'
            or period.get('policyVersion') != OWNER_POLICY or integral(period.get('limit')) != PAID_COMPLETED_CHECKS
            or integral(period.get('grantRevision')) is None or period['grantRevision'] < 1
            or any(integral(period.get(k)) is None for k in ('startEpoch', 'endEpoch', 'usedChecks', 'reservedChecks'))
            or period['usedChecks'] < 0 or period['reservedChecks'] < 0
            or period['usedChecks'] + period['reservedChecks'] > PAID_COMPLETED_CHECKS):
        raise AuthorityError('PURCHASE_USAGE_INVALID')
    _bounds(period['startEpoch'], period['endEpoch'])
    effective_access_end(period)
    return valid_key(period.get('purchaseUsageKey'))


def global_for_period(resource, table, period, *, now, allow_expired=False):
    pointer = validate_period(period)
    if allow_expired and now >= usage_deadline(period):
        # No future charge/restore may use this funded period. A physically
        # expired global row cannot prevent erasure of an old account receipt.
        return None
    observed = read(resource, table, pointer, now=now)
    if (any(observed[k] != period[k] for k in ('startEpoch', 'endEpoch', 'limit', 'usedChecks', 'reservedChecks'))
            or effective_access_end(observed) != effective_access_end(period)):
        raise AuthorityError('PURCHASE_USAGE_MISMATCH')
    return observed


def counter_action(table, observed, reserved_delta, used_delta, *, now):
    pointer = valid_key({k: observed.get(k) for k in ('PK', 'SK')})
    validate(observed, pointer, now)
    if (type(reserved_delta) is not int or type(used_delta) is not int or used_delta < 0
            or observed['reservedChecks'] + reserved_delta < 0
            or observed['usedChecks'] + used_delta + observed['reservedChecks'] + reserved_delta > PAID_COMPLETED_CHECKS
            or reserved_delta > 0 and now >= effective_access_end(observed)):
        raise AuthorityError('PURCHASE_USAGE_INVALID')
    return local_counter_action(table, observed, reserved_delta, used_delta)


def local_counter_action(table, row, reserved_delta, used_delta):
    condition = exact_condition(row)
    condition['ExpressionAttributeValues'].update({':reserveDelta': reserved_delta, ':usedDelta': used_delta})
    return {'Update': {'TableName': table, 'Key': {k: row[k] for k in ('PK', 'SK')},
                       'UpdateExpression': 'ADD reservedChecks :reserveDelta, usedChecks :usedDelta', **condition}}


def paired_counter_actions(resource, table, period, reserved_delta, used_delta, *, now, allow_expired=False):
    observed = global_for_period(resource, table, period, now=now, allow_expired=allow_expired)
    if observed is None and used_delta != 0:
        raise AuthorityError('PURCHASE_USAGE_INVALID')
    if period['reservedChecks'] + reserved_delta < 0:
        raise AuthorityError('PURCHASE_USAGE_INVALID')
    actions = [local_counter_action(table, period, reserved_delta, used_delta)]
    if observed is not None:
        actions.append(counter_action(table, observed, reserved_delta, used_delta, now=now))
    return actions


def period_for_receipt(resource, table, partition, receipt):
    if (receipt.get('recordType') != 'V1_CHECK_RECEIPT' or receipt.get('policyVersion') != OWNER_POLICY
            or receipt.get('basis') != 'paid' or receipt.get('state') != 'ADMITTED'
            or not isinstance(receipt.get('periodSK'), str)
            or not re.fullmatch(r'PERIOD#[A-Za-z0-9_-]{1,64}', receipt['periodSK'])
            or integral(receipt.get('grantRevision')) is None):
        raise AuthorityError('PURCHASE_USAGE_INVALID')
    pointer = valid_key(receipt.get('purchaseUsageKey'))
    period = resource.Table(table).get_item(Key={'PK': partition, 'SK': receipt['periodSK']}, ConsistentRead=True).get('Item')
    if validate_period(period) != pointer or period['grantRevision'] != receipt['grantRevision']:
        raise AuthorityError('PURCHASE_USAGE_MISMATCH')
    admitted_access = integral(receipt.get('accessUntilEpoch', period['endEpoch']))
    if admitted_access is None or not period['startEpoch'] <= admitted_access <= effective_access_end(period):
        raise AuthorityError('PURCHASE_USAGE_MISMATCH')
    return period
