"""Minimized purchase-period accounting; no account identity or submitted content.

The approved deadline is the verified access window plus seven days; funded
period identity and counters remain unchanged by an access extension.
Every mutation below is a native action for a caller-owned atomic transaction.
"""
import re
import hashlib
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

    There is no expiry inference from event time. Future deadlines preserve
    reservations; already-due reservations require atomic receipt closure.
    Already-due zero-reservation rows are atomically
    deleted, requiring an explicitly qualified caller Delete permission.
    """
    observed = validate(observed_global, pointer, now, allow_expired=True)
    proposed = new_period_usage(pointer, observed['startEpoch'], observed['endEpoch'],
                                access_until_epoch=access_until_epoch)
    proposed.update({k: observed[k] for k in ('usedChecks', 'reservedChecks')})
    old_access = effective_access_end(observed)
    if access_until_epoch > old_access and now >= observed['expiresAt']:
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
    # This is historical admission evidence, not authority to retain or charge
    # beyond the current verified local/global deadline after shortening.
    admitted_access = integral(receipt.get('accessUntilEpoch', period['endEpoch']))
    if admitted_access is None or not period['startEpoch'] <= admitted_access:
        raise AuthorityError('PURCHASE_USAGE_MISMATCH')
    return period


_PENDING_FIELDS = {'PK', 'SK', 'recordType', 'checkId', 'payloadHmac', 'clientCheckId',
    'projectionScope', 'state', 'chargedChecks', 'receiptId', 'processingOutcome',
    'basis', 'grantRevision', 'authorityRevision', 'policyVersion', 'periodSK',
    'executionToken', 'settleByEpoch', 'GSI1PK', 'GSI1SK', 'retentionDeadlineEpoch',
    'purchaseUsageKey'}


def due_reservation_actions(resource, table, period, observed_global, access_until_epoch, *, now):
    """Prepare bounded, zero-charge closure; caller atomically fences ACCESS and PERIOD.

    All current reservations must be accounted for. An incomplete query or a
    malformed matching receipt never permits global deletion. Other periods and
    their reservations remain untouched. No provider response is reconstructed.
    """
    pointer = validate_period(period)
    observed = validate(observed_global, pointer, now, allow_expired=True)
    proposed = new_period_usage(pointer, period['startEpoch'], period['endEpoch'],
                                access_until_epoch=access_until_epoch)
    count = integral(period['reservedChecks'])
    if (proposed['expiresAt'] > now or not 1 <= count <= 16
            or any(observed[k] != period[k] for k in ('startEpoch', 'endEpoch', 'usedChecks', 'reservedChecks', 'limit'))
            or effective_access_end(observed) != effective_access_end(period)):
        raise AuthorityError('PURCHASE_USAGE_RECONCILIATION_REQUIRED')
    partition = period['PK']
    pending, cursor = [], None
    for _ in range(8):
        args = {'KeyConditionExpression': 'PK = :pk AND begins_with(SK, :check)',
                'ExpressionAttributeValues': {':pk': partition, ':check': 'CHECK#'},
                'ConsistentRead': True, 'Limit': 100}
        if cursor:
            args['ExclusiveStartKey'] = cursor
        response = resource.Table(table).query(**args)
        for row in response.get('Items', []):
            if row.get('state') == 'SETTLED':
                continue
            if row.get('periodSK') != period['SK'] and row.get('purchaseUsageKey') != pointer:
                continue
            optional = {'accessUntilEpoch', 'messageTransportVersion', 'recoveryTransportVersion'}
            if (not _PENDING_FIELDS <= set(row) or set(row) - _PENDING_FIELDS - optional
                    or row.get('PK') != partition or row.get('periodSK') != period['SK']
                    or row.get('recordType') != 'V1_CHECK_RECEIPT' or row.get('policyVersion') != OWNER_POLICY
                    or row.get('state') != 'ADMITTED' or row.get('basis') != 'paid'
                    or row.get('purchaseUsageKey') != pointer or integral(row.get('grantRevision')) != period['grantRevision']
                    or not isinstance(row.get('checkId'), str)
                    or not re.fullmatch(r'v1_[A-Za-z0-9]{1,8}_[0-9a-f]{8}_[0-9a-f]{16}_[0-9a-f]{24}', row['checkId'])
                    or row.get('SK') != 'CHECK#' + row['checkId']
                    or not isinstance(row.get('executionToken'), str) or not re.fullmatch(r'[0-9a-f]{32}', row['executionToken'])
                    or not isinstance(row.get('payloadHmac'), str) or not re.fullmatch(r'[0-9a-f]{64}', row['payloadHmac'])
                    or any(row.get(k) is not None for k in ('chargedChecks', 'receiptId', 'processingOutcome'))
                    or integral(row.get('authorityRevision')) is None or row['authorityRevision'] < 1
                    or integral(row.get('settleByEpoch')) is None or row['settleByEpoch'] < 0
                    or integral(row.get('retentionDeadlineEpoch')) is None or row['retentionDeadlineEpoch'] < row['settleByEpoch']
                    or integral(row.get('accessUntilEpoch', period['endEpoch'])) is None
                    or row.get('accessUntilEpoch', period['endEpoch']) < period['startEpoch']
                    or row.get('GSI1PK') != 'V1_PENDING'
                    or row.get('GSI1SK') != f'{int(row["settleByEpoch"]):012d}#{partition}#{row["checkId"]}'
                    or row.get('projectionScope') not in ('full_url', 'origin_only', 'sanitized_message', 'recovery_clarification')):
                raise AuthorityError('PURCHASE_USAGE_RECONCILIATION_REQUIRED')
            scope = row['projectionScope']
            client = row['clientCheckId']
            if (client is not None and (not isinstance(client, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', client))
                    or scope in ('full_url', 'origin_only') and ('messageTransportVersion' in row or 'recoveryTransportVersion' in row)
                    or scope == 'sanitized_message' and (client is None or 'recoveryTransportVersion' in row
                        or row.get('messageTransportVersion', '1.0.0-message-candidate.2') != '1.0.0-message-candidate.2')
                    or scope == 'recovery_clarification' and (client is None or 'messageTransportVersion' in row)):
                raise AuthorityError('PURCHASE_USAGE_RECONCILIATION_REQUIRED')
            if scope == 'recovery_clarification':
                from shared_recovery_contract.constants import VERSION
                if row.get('recoveryTransportVersion') != VERSION:
                    raise AuthorityError('PURCHASE_USAGE_RECONCILIATION_REQUIRED')
            pending.append(row)
            if len(pending) > count:
                raise AuthorityError('PURCHASE_USAGE_RECONCILIATION_REQUIRED')
        cursor = response.get('LastEvaluatedKey')
        if not cursor:
            break
    if cursor or len(pending) != count:
        raise AuthorityError('PURCHASE_USAGE_RECONCILIATION_REQUIRED')
    inflight_key = {'PK': partition, 'SK': 'INFLIGHT'}
    inflight = resource.Table(table).get_item(Key=inflight_key, ConsistentRead=True).get('Item')
    if (not isinstance(inflight, dict) or set(inflight) != {'PK', 'SK', 'activeCount'}
            or integral(inflight.get('activeCount')) is None or inflight['activeCount'] < count):
        raise AuthorityError('PURCHASE_USAGE_RECONCILIATION_REQUIRED')
    actions = [{'Delete': {'TableName': table, 'Key': pointer, **exact_condition(observed)}},
               {'Put': {'TableName': table, 'Item': inflight | {'activeCount': inflight['activeCount'] - count},
                        **exact_condition(inflight)}}]
    for row in pending:
        condition = exact_condition(row)
        deadline = row['retentionDeadlineEpoch']
        key = {k: row[k] for k in ('PK', 'SK')}
        if deadline <= now:
            actions.append({'Delete': {'TableName': table, 'Key': key, **condition}})
            continue
        settled = row | {'state': 'SETTLED', 'chargedChecks': 0, 'processingOutcome': 'failed',
                         'receiptId': 'expired_' + hashlib.sha256((partition + '\0' + row['checkId']).encode()).hexdigest()[:32],
                         'expiresAt': deadline, 'GSI1PK': 'V1_EXPIRING',
                         'GSI1SK': f'{int(deadline):012d}#{partition}#{row["SK"]}'}
        if row['projectionScope'] == 'recovery_clarification':
            try:
                from shared_recovery_contract.constants import VERSION
                from shared_recovery_contract.usage import usage
                if row.get('recoveryTransportVersion') != VERSION:
                    raise ValueError()
                settled.update(resultSummary=usage(row.get('clientCheckId'), 'failed'), assessmentEpoch=now)
            except Exception:
                raise AuthorityError('PURCHASE_USAGE_RECONCILIATION_REQUIRED') from None
        actions.append({'Put': {'TableName': table, 'Item': settled, **condition}})
    return actions
