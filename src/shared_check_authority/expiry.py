"""Explicit bounded expiry of minimized V1 records; TTL is only a fallback."""
import re

from .core import AuthorityError, OWNER_POLICY, integral
from .inventory import INVENTORY_KEY, inventory_condition

PARTITION = re.compile(r'V1#([A-Za-z0-9]{1,8})#[0-9a-f]{64}')
PROOF = r'v1_[A-Za-z0-9]{1,8}_[0-9a-f]{8}_[0-9a-f]{16}_[0-9a-f]{24}'
TYPES = {'PREPARE': 'V1_PREPARATION', 'ATTEMPT': 'V1_ATTEMPT_COUNTER', 'CHECK': 'V1_CHECK_RECEIPT'}


class Expiry:
    def __init__(self, resource, table, *, now):
        self.ddb, self.table, self.now = resource, table, now
        self.client = resource.meta.client
        if self.client.meta.config.retries.get('total_max_attempts') != 1:
            raise AuthorityError('SDK_RETRY_CONFIGURATION_UNAVAILABLE')

    def _get(self, key):
        return self.ddb.Table(self.table).get_item(Key=key, ConsistentRead=True).get('Item')

    def _inventory(self, key_id=None):
        row = self._get(INVENTORY_KEY)
        if (not row or set(row) != {'PK', 'SK', 'recordType', 'schemaVersion', 'revision', 'coverage', 'issuedKeys'}
                or row.get('recordType') != 'V1_HMAC_KEY_INVENTORY' or integral(row.get('schemaVersion')) != 1
                or integral(row.get('revision')) is None or row['revision'] < 1 or row.get('coverage') != 'VERIFIED_COMPLETE'
                or not isinstance(row.get('issuedKeys'), dict) or not 1 <= len(row['issuedKeys']) <= 4
                or key_id is not None and key_id not in row['issuedKeys']
                or any(not isinstance(k, str) or not re.fullmatch(r'[A-Za-z0-9]{1,8}', k)
                       or not isinstance(v, str) or not re.fullmatch(r'[0-9a-f]{64}', v) for k, v in row['issuedKeys'].items())):
            raise AuthorityError('EXPIRY_INVENTORY_UNAVAILABLE')
        return row

    def expire(self, partition, sort_key):
        if isinstance(partition, str) and partition.startswith('V1#PURCHASE_USAGE#'):
            return self._expire_purchase_usage(partition, sort_key)
        match = PARTITION.fullmatch(partition) if isinstance(partition, str) else None
        if not match or not isinstance(sort_key, str):
            raise AuthorityError('EXPIRY_REFERENCE_INVALID')
        if re.fullmatch(r'PREPARE#[A-Za-z0-9_-]{1,64}', sort_key):
            family = 'PREPARE'
        elif re.fullmatch(r'ATTEMPT#[0-9]{1,12}', sort_key):
            family = 'ATTEMPT'
        elif re.fullmatch('CHECK#' + PROOF, sort_key):
            family = 'CHECK'
        else:
            raise AuthorityError('EXPIRY_REFERENCE_INVALID')
        key = {'PK': partition, 'SK': sort_key}
        row = self._get(key)
        if row is None:
            return False
        expiry = integral(row.get('expiresAt'))
        if expiry is None or expiry <= 0 or expiry > self.now():
            return False
        index_sort = f'{expiry:012d}#{partition}#{sort_key}'
        if (row.get('recordType') != TYPES[family] or row.get('GSI1PK') != 'V1_EXPIRING'
                or row.get('GSI1SK') != index_sort
                or (family == 'CHECK' and (row.get('state') != 'SETTLED' or row.get('policyVersion') != OWNER_POLICY))):
            raise AuthorityError('EXPIRY_RECORD_INVALID')
        inventory = self._inventory(match.group(1))
        access = self._get({'PK': partition, 'SK': 'ACCESS'})
        guard = {'TableName': self.table, 'Key': {'PK': partition, 'SK': 'ACCESS'}}
        if access is None:
            if family == 'CHECK':
                raise AuthorityError('EXPIRY_ACCESS_UNAVAILABLE')
            guard['ConditionExpression'] = 'attribute_not_exists(PK)'
        else:
            if access.get('recordType') == 'V1_ACCESS_AUTHORITY' and access.get('state') == 'DELETING':
                return False  # The account-deletion worker owns this partition.
            if access.get('recordType') != 'V1_ACCESS_AUTHORITY' or access.get('state') not in ('ACTIVE', 'INACTIVE', 'REVOKED'):
                raise AuthorityError('EXPIRY_ACCESS_UNAVAILABLE')
            guard.update(ConditionExpression='recordType = :type AND #state = :state',
                         ExpressionAttributeNames={'#state': 'state'},
                         ExpressionAttributeValues={':type': 'V1_ACCESS_AUTHORITY', ':state': access['state']})
        deletion = {'TableName': self.table, 'Key': key,
                    'ConditionExpression': 'recordType = :type AND expiresAt = :expiry AND expiresAt <= :now AND GSI1PK = :index AND GSI1SK = :sort',
                    'ExpressionAttributeValues': {':type': TYPES[family], ':expiry': expiry, ':now': self.now(),
                                                  ':index': 'V1_EXPIRING', ':sort': index_sort}}
        if family == 'CHECK':
            deletion['ConditionExpression'] += ' AND #state = :settled AND policyVersion = :policy'
            deletion['ExpressionAttributeNames'] = {'#state': 'state'}
            deletion['ExpressionAttributeValues'].update({':settled': 'SETTLED', ':policy': OWNER_POLICY})
        try:
            self.client.transact_write_items(TransactItems=[inventory_condition(self.table, inventory),
                                                           {'ConditionCheck': guard}, {'Delete': deletion}])
        except Exception:
            # An uncertain successful delete or concurrent expiry extension is a
            # no-op on retry; identity/inventory failures remain observable.
            latest = self._get(key)
            if latest is None:
                return False
            latest_expiry = integral(latest.get('expiresAt'))
            if latest_expiry is not None and latest_expiry != expiry:
                return False
            current_access = self._get({'PK': partition, 'SK': 'ACCESS'})
            if current_access and current_access.get('state') == 'DELETING':
                return False
            raise AuthorityError('EXPIRY_TRANSACTION_UNCERTAIN') from None
        return True

    def _expire_purchase_usage(self, partition, sort_key):
        from .purchase_usage import valid_key, validate, exact_condition
        key = valid_key({'PK': partition, 'SK': sort_key})
        row = self._get(key)
        if row is None:
            return False
        validate(row, key, self.now(), allow_expired=True)
        if row['expiresAt'] > self.now():
            return False
        inventory = self._inventory()
        # The accounting deadline is final even if abandoned reservations remain:
        # every original receipt's logical charging window has ended by this point.
        deletion = {'TableName': self.table, 'Key': key, **exact_condition(row)}
        try:
            self.client.transact_write_items(TransactItems=[inventory_condition(self.table, inventory), {'Delete': deletion}])
        except Exception:
            if self._get(key) is None:
                return False
            raise AuthorityError('EXPIRY_TRANSACTION_UNCERTAIN') from None
        return True

    def sweep(self, *, cursor=None, page_size=25, max_pages=4, can_continue=lambda: True):
        if type(page_size) is not int or not 1 <= page_size <= 25 or type(max_pages) is not int or not 1 <= max_pages <= 4:
            raise AuthorityError('EXPIRY_CONFIGURATION_INVALID')
        counts = {'examined': 0, 'deleted': 0, 'failed': 0, 'pages': 0, 'oldestOverdueSeconds': 0}
        for _ in range(max_pages):
            if not can_continue():
                break
            args = {'IndexName': 'GSI1', 'KeyConditionExpression': 'GSI1PK = :expiring AND GSI1SK <= :deadline',
                    'ExpressionAttributeValues': {':expiring': 'V1_EXPIRING', ':deadline': f'{self.now():012d}~'}, 'Limit': page_size}
            if cursor:
                args['ExclusiveStartKey'] = cursor
            page = self.ddb.Table(self.table).query(**args)
            counts['pages'] += 1
            for item in page.get('Items', []):
                if not can_continue():
                    return counts | {'cursor': cursor}
                counts['examined'] += 1
                expiry = integral(item.get('expiresAt'))
                if expiry is not None:
                    counts['oldestOverdueSeconds'] = max(counts['oldestOverdueSeconds'], max(0, self.now() - expiry))
                try:
                    counts['deleted'] += int(self.expire(item['PK'], item['SK']))
                except (AuthorityError, KeyError, TypeError):
                    counts['failed'] += 1
                # Checkpoint every attempted item, including poison rows; never skip
                # unprocessed items if the Lambda deadline interrupts this page.
                cursor = {k: item[k] for k in ('PK', 'SK', 'GSI1PK', 'GSI1SK')}
            cursor = page.get('LastEvaluatedKey')
            if not cursor:
                break
        return counts | {'cursor': cursor}
