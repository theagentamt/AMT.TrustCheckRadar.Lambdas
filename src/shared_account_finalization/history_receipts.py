"""Terminal-safe History component evidence; never revive a completed fence."""
from .service import Finalizer, FinalizationError, completed_fence, integer, RECEIPT_FIELDS, RETENTION_SECONDS


def wire(row):
    result = {}
    for key, value in row.items():
        if isinstance(value, str):
            result[key] = {'S': value}
        elif integer(value) is not None:
            result[key] = {'N': str(int(value))}
        else:
            raise FinalizationError('HISTORY_COMMAND_INVALID')
    return result


def current_command(ledger, command, now):
    Finalizer._validate_command(command, now, command.get('environment'))
    current = ledger.get_item(Key={'PK': command['PK'], 'SK': command['SK']}, ConsistentRead=True).get('Item')
    if completed_fence(current, command['environment'], now, command):
        return 'TERMINAL'
    if current != command:
        raise FinalizationError('HISTORY_COMMAND_CHANGED')
    return 'REQUESTED'


def command_guard(table_name, command):
    fields = [(key, value) for key, value in command.items() if key not in ('PK', 'SK')]
    return {'ConditionCheck': {
        'TableName': table_name, 'Key': wire({'PK': command['PK'], 'SK': command['SK']}),
        'ConditionExpression': ' AND '.join(f'#c{i} = :c{i}' for i in range(len(fields))),
        'ExpressionAttributeNames': {f'#c{i}': key for i, (key, _) in enumerate(fields)},
        'ExpressionAttributeValues': wire({f':c{i}': value for i, (_, value) in enumerate(fields)}),
    }}


def _valid_receipt(receipt, command, now):
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_FIELDS:
        return False
    fixed = {'PK': command['PK'], 'SK': 'ACCOUNT_DELETION#HISTORY', 'schemaVersion': 1,
        'recordVersion': 1, 'environment': command['environment'],
        'eventType': 'account.deletion.component.completed', 'component': 'HISTORY',
        'status': 'COMPLETE', 'requestOccurredAtEpoch': command['occurredAtEpoch'],
        'operationId': command['operationId']}
    occurred = integer(receipt.get('occurredAtEpoch'))
    return (all(receipt.get(key) == value for key, value in fixed.items())
        and integer(receipt.get('schemaVersion')) == 1 and integer(receipt.get('recordVersion')) == 1
        and integer(receipt.get('requestOccurredAtEpoch')) == command['occurredAtEpoch']
        and occurred is not None and command['occurredAtEpoch'] <= occurred <= now
        and integer(receipt.get('retainUntilEpoch')) == occurred + RETENTION_SECONDS
        and receipt['retainUntilEpoch'] > now)


def write_receipt(*, ledger, client, table_name, command, now, guards=()):
    if current_command(ledger, command, now) == 'TERMINAL':
        return {'terminal': True, 'receiptCreated': False}
    key = {'PK': command['PK'], 'SK': 'ACCOUNT_DELETION#HISTORY'}
    existing = ledger.get_item(Key=key, ConsistentRead=True).get('Item')
    if existing is not None:
        if not _valid_receipt(existing, command, now):
            raise FinalizationError('HISTORY_RECEIPT_INVALID')
        # Existing evidence is never rewritten to slide its original deadline.
        return {'terminal': False, 'receiptCreated': False}
    receipt = {**key, 'schemaVersion': 1, 'recordVersion': 1,
        'environment': command['environment'], 'eventType': 'account.deletion.component.completed',
        'component': 'HISTORY', 'status': 'COMPLETE', 'occurredAtEpoch': now,
        'requestOccurredAtEpoch': command['occurredAtEpoch'], 'operationId': command['operationId'],
        'retainUntilEpoch': now + RETENTION_SECONDS}
    try:
        client.transact_write_items(TransactItems=[command_guard(table_name, command), *guards,
            {'Put': {'TableName': table_name, 'Item': wire(receipt),
                'ConditionExpression': 'attribute_not_exists(PK) AND attribute_not_exists(SK)'}}])
    except Exception:
        # A lost acknowledgement or concurrent finalization may have committed.
        # Re-read exact evidence, never retry an unconditional receipt Put.
        if current_command(ledger, command, now) == 'TERMINAL':
            return {'terminal': True, 'receiptCreated': False}
        existing = ledger.get_item(Key=key, ConsistentRead=True).get('Item')
        if _valid_receipt(existing, command, now):
            return {'terminal': False, 'receiptCreated': False}
        raise
    return {'terminal': False, 'receiptCreated': True}
