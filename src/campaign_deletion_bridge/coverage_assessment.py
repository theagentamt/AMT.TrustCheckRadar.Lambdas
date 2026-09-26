"""Read-only retained-period diagnostics; never an account-erasure proof.

No handler calls this candidate. Strong reads are observations, not a snapshot or
an atomic absence guarantee. Completion helpers remain closed independently.
"""
import base64
import re
from copy import deepcopy
from uuid import UUID

from progress import get, integer, key, plain, wire
from service import PERIOD_SECONDS, RECOVERY_SECONDS, TOKEN_DOMAIN, parse_deletion_record
from shared_campaign_locators import load_inventory, validate_locator
from shared_campaign_locators import period as period_fence
from tombstone import validate as validate_tombstone


KEY_FIELDS = {'PK', 'SK', 'keyArn', 'status', 'periodId', 'retireAfterEpoch'}


class _Unverified(Exception):
    def __init__(self, reason):
        self.reason = reason


def _need(condition, reason):
    if not condition:
        raise _Unverified(reason)


def _result():
    return {'schemaVersion': 1, 'scope': 'READ_ONLY_LOCATOR_COVERAGE',
            'complete': False, 'receiptEligible': False, 'rangeExamined': False,
            'periodsExpected': 0, 'periodsExamined': 0, 'observedEmptyPeriods': 0,
            'pendingPeriods': 0, 'unverifiedPeriods': 0, 'pagesRead': 0,
            'reasons': []}


def _cursor(value):
    if value is None:
        return True
    if type(value) is not str or not re.fullmatch(r'LOCATOR#(?:EVENT|CANDIDATE)#[0-9a-f-]{36}', value):
        return False
    try:
        ident = value.rsplit('#', 1)[1]
        parsed = UUID(ident)
        return parsed.version == 4 and str(parsed) == ident
    except (ValueError, TypeError, AttributeError):
        return False


def _state(tomb, environment, partition, operation_id):
    try:
        validate_tombstone(tomb, environment, partition)
        integer(tomb.get('locatorCleanupRevision'), 1)
        state = tomb['locatorCleanupState']
        _need(type(state) is dict, 'REPAIR_STATE_UNVERIFIED')
        phase = state.get('phase')
        _need(phase in ('SEEK', 'DELETE', 'RECOMPUTE'), 'REPAIR_STATE_UNVERIFIED')
        fields = {'operationId', 'phase', 'cursor'}
        if phase != 'SEEK':
            fields |= {'locator', 'nextCursor'}
        _need(set(state) == fields, 'REPAIR_STATE_UNVERIFIED')
        op = UUID(state['operationId'])
        _need(op.version == 4 and str(op) == state['operationId'], 'REPAIR_STATE_UNVERIFIED')
        for cursor in (state['cursor'], state.get('nextCursor')):
            _need(_cursor(cursor), 'REPAIR_STATE_UNVERIFIED')
        if phase != 'SEEK':
            validate_locator(state['locator'], environment, partition)
        return state['operationId'] == operation_id and phase == 'SEEK' and state['cursor'] is None
    except _Unverified:
        raise
    except Exception:
        raise _Unverified('REPAIR_STATE_UNVERIFIED') from None


def _key_record(record, period, account, region):
    _need(type(record) is dict, 'PERIOD_KEY_MISSING')
    if record.get('status') == 'RETIRED':
        raise _Unverified('PERIOD_RETIREMENT_UNPROVEN')
    try:
        valid = (set(record) == KEY_FIELDS | period_fence.ADMISSION_FIELDS and record['PK'] == f'PERIOD#{period}'
                 and record['SK'] == 'HMAC_KEY' and record['status'] == 'ENABLED'
                 and integer(record['periodId']) == period
                 and integer(record['retireAfterEpoch']) == (period + 1) * PERIOD_SECONDS + RECOVERY_SECONDS
                 and type(record['keyArn']) is str and re.fullmatch(
                     rf'arn:aws:kms:{re.escape(region)}:{account}:key/[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}', record['keyArn']))
        _need(valid, 'PERIOD_KEY_UNVERIFIED')
    except _Unverified:
        raise
    except Exception:
        raise _Unverified('PERIOD_KEY_UNVERIFIED') from None
    return record['keyArn']


def assess_account_coverage(command, *, environment, aws_account_id, aws_region,
                            table_name, deletion_ledger_table_name, dynamodb, kms,
                            locator_manifest_sha256, locator_inventory_revision,
                            now_epoch, max_periods=8, max_pages=32, page_size=25,
                            remaining_ms=None):
    """Bounded, sanitized diagnostic of every inventory-covered retained period.

    Never skips old periods based on age, key status or TTL. Fixed command and
    marker changes invalidate the whole observation. Even an observed empty
    range cannot authorize a receipt or certify candidate/aggregate anonymity.
    Caller-supplied clients are used only for GetItem, Query and GenerateMac.
    """
    result = _result()
    reasons = set()
    snapshots = []
    try:
        command = deepcopy(command)
        _need(environment in ('dev', 'uat', 'prod') and type(aws_account_id) is str
              and re.fullmatch(r'[0-9]{12}', aws_account_id)
              and type(aws_region) is str and re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-[0-9]', aws_region)
              and type(now_epoch) is int and now_epoch > 0
              and type(max_periods) is int and 1 <= max_periods <= 32
              and type(max_pages) is int and 1 <= max_pages <= 64
              and type(page_size) is int and 1 <= page_size <= 100
              and all(type(v) is str and re.fullmatch(r'[A-Za-z0-9_.-]{3,255}', v)
                      for v in (table_name, deletion_ledger_table_name)), 'CONFIGURATION_UNVERIFIED')
        try:
            parsed = parse_deletion_record({'eventName': 'INSERT', 'dynamodb': {'NewImage': wire(command)}},
                                           environment=environment, schema_version=1)
            _need(parsed is not None and parsed['eventType'] == 'account.deletion.requested'
                  and 0 < parsed['occurredAtEpoch'] <= now_epoch
                  and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:@-]{0,199}', parsed['accountId']), 'COMMAND_UNVERIFIED')
        except Exception:
            raise _Unverified('COMMAND_UNVERIFIED') from None
        stored = get(dynamodb, deletion_ledger_table_name, command['PK'], command['SK'])
        _need(stored == command, 'COMMAND_CHANGED')
        snapshots.append((deletion_ledger_table_name, command['PK'], command['SK'], stored))
        try:
            inventory = load_inventory(dynamodb, table_name, environment,
                locator_manifest_sha256, locator_inventory_revision, now_epoch)
        except Exception:
            raise _Unverified('INVENTORY_UNVERIFIED') from None
        _need(inventory['approvedAtEpoch'] < command['occurredAtEpoch'], 'COMMAND_PREDATES_INVENTORY')
        snapshots.append((table_name, inventory['PK'], inventory['SK'], inventory))
        first = int(inventory['minimumPeriodId'])
        last = command['occurredAtEpoch'] // PERIOD_SECONDS
        _need(first <= last, 'PERIOD_RANGE_UNVERIFIED')
        result['periodsExpected'] = last - first + 1
        if result['periodsExpected'] > max_periods:
            raise _Unverified('PERIOD_BUDGET_EXCEEDED')
        for period in range(first, last + 1):
            if remaining_ms is not None and remaining_ms() < 3000:
                reasons.add('TIME_BUDGET_EXCEEDED')
                break
            if result['pagesRead'] >= max_pages:
                reasons.add('PAGE_BUDGET_EXCEEDED')
                break
            result['periodsExamined'] += 1
            try:
                record = get(dynamodb, table_name, f'PERIOD#{period}', 'HMAC_KEY')
                arn = _key_record(record, period, aws_account_id, aws_region)
                period_fence.validate(record,period,locator_manifest_sha256,locator_inventory_revision,now_epoch)
                snapshots.append((table_name, record['PK'], record['SK'], record))
                try:
                    response = kms.generate_mac(KeyId=arn, Message=TOKEN_DOMAIN + command['accountId'].encode(),
                                                MacAlgorithm='HMAC_SHA_256')
                    _need(response.get('KeyId') == arn and response.get('MacAlgorithm') == 'HMAC_SHA_256'
                          and type(response.get('Mac')) is bytes and len(response['Mac']) == 32, 'PERIOD_KEY_UNAVAILABLE')
                except Exception:
                    raise _Unverified('PERIOD_KEY_UNAVAILABLE') from None
                token = base64.urlsafe_b64encode(response['Mac']).decode().rstrip('=')
                partition = f'CONTRIB#{period}#{token}'
                tomb = get(dynamodb, table_name, partition, 'TOMBSTONE')
                _need(tomb is not None, 'PARTITION_NOT_FENCED')
                idle = _state(tomb, environment, partition, command['operationId'])
                _need(tomb['createdAtEpoch'] <= command['occurredAtEpoch'], 'REPAIR_STATE_UNVERIFIED')
                snapshots.append((table_name, partition, 'TOMBSTONE', tomb))
                cursor = None
                found = False
                while True:
                    if result['pagesRead'] >= max_pages:
                        raise _Unverified('PAGE_BUDGET_EXCEEDED')
                    if remaining_ms is not None and remaining_ms() < 3000:
                        raise _Unverified('TIME_BUDGET_EXCEEDED')
                    args = {'TableName': table_name, 'KeyConditionExpression': 'PK = :pk',
                            'ExpressionAttributeValues': wire({':pk': partition}),
                            'ConsistentRead': True, 'Limit': page_size}
                    if cursor is not None:
                        args['ExclusiveStartKey'] = cursor
                    page = dynamodb.query(**args)
                    result['pagesRead'] += 1
                    _need(type(page.get('Items')) is list and len(page['Items']) <= page_size, 'PARTITION_UNVERIFIED')
                    for raw in page['Items']:
                        item = plain(raw)
                        if item.get('SK') == 'TOMBSTONE':
                            _need(item == tomb, 'OBSERVATION_CHANGED')
                        else:
                            try:
                                validate_locator(item, environment, partition)
                            except Exception:
                                raise _Unverified('PARTITION_UNVERIFIED') from None
                            found = True
                    next_cursor = page.get('LastEvaluatedKey')
                    if not next_cursor:
                        break
                    decoded = plain(next_cursor)
                    _need(set(decoded) == {'PK', 'SK'} and decoded['PK'] == partition
                          and type(decoded['SK']) is str and (decoded['SK'] == 'TOMBSTONE'
                          or _cursor(decoded['SK']))
                          and next_cursor != cursor, 'PARTITION_UNVERIFIED')
                    cursor = next_cursor
                if found or not idle:
                    result['pendingPeriods'] += 1
                    reasons.add('LOCATORS_OR_REPAIR_PENDING')
                else:
                    result['observedEmptyPeriods'] += 1
            except _Unverified as err:
                result['unverifiedPeriods'] += 1
                reasons.add(err.reason)
            except Exception:
                result['unverifiedPeriods'] += 1
                reasons.add('EVIDENCE_UNAVAILABLE')
        result['rangeExamined'] = result['periodsExamined'] == result['periodsExpected'] and not result['unverifiedPeriods']
        for table, pk, sk, observed in snapshots:
            _need(remaining_ms is None or remaining_ms() >= 3000, 'TIME_BUDGET_EXCEEDED')
            _need(get(dynamodb, table, pk, sk) == observed, 'OBSERVATION_CHANGED')
    except _Unverified as err:
        reasons.add(err.reason)
        result['rangeExamined'] = False
    except Exception:
        reasons.add('EVIDENCE_UNAVAILABLE')
        result['rangeExamined'] = False
    reasons.add('COMPLETION_PROOF_UNQUALIFIED')
    result['reasons'] = sorted(reasons)
    return result
