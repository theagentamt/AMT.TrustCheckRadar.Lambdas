"""Selective physical removal of expired operations fields, not accounting.

Uses the existing authority inventory, partition and bounded cleanup queues.
There is no new subject locator, tombstone, resource, schedule or permission.
"""
import re

from .core import AuthorityError, OWNER_POLICY, integral
from .expiry import Expiry, PARTITION, PROOF
from .inventory import inventory_condition


class OperationsRetirement:
    def __init__(self, resource, table, *, now):
        self.ddb, self.table, self.now = resource, table, now
        self.guard = Expiry(resource, table, now=now)
        # Apply this helper's acknowledged-read boundary to inventory too,
        # without changing the ordinary full-expiry reader.
        self.guard._get = self._get
        self.client = resource.meta.client

    def _get(self, key):
        try:
            response = self.ddb.Table(self.table).get_item(Key=key, ConsistentRead=True)
            if (type(response) is not dict or type(response.get('ResponseMetadata')) is not dict
                    or response['ResponseMetadata'].get('HTTPStatusCode') != 200):
                raise ValueError()
            value = response.get('Item')
            if value is not None and type(value) is not dict:
                raise ValueError()
            return value
        except Exception: raise AuthorityError('OPERATIONS_RETIREMENT_READ_UNAVAILABLE') from None

    def retire(self, partition, sort_key):
        match = PARTITION.fullmatch(partition) if isinstance(partition, str) else None
        if not match or not isinstance(sort_key, str) or not re.fullmatch('CHECK#' + PROOF, sort_key):
            raise AuthorityError('OPERATIONS_RETIREMENT_REFERENCE_INVALID')
        key = {'PK': partition, 'SK': sort_key}
        row = self._get(key)
        if row is None or 'operationsReceipt' not in row:
            return False
        try:
            from shared_operations import retirement_metadata
            metadata = retirement_metadata(row)
        except Exception: raise AuthorityError('OPERATIONS_RETIREMENT_RECORD_INVALID') from None
        try: now = self.now()
        except Exception: raise AuthorityError('OPERATIONS_RETIREMENT_CLOCK_UNAVAILABLE') from None
        if type(now) is not int or now < 0:
            raise AuthorityError('OPERATIONS_RETIREMENT_CLOCK_UNAVAILABLE')
        state = row.get('state')
        if (row.get('recordType') != 'V1_CHECK_RECEIPT' or row.get('policyVersion') != OWNER_POLICY
                or state not in ('ADMITTED', 'SETTLED')):
            raise AuthorityError('OPERATIONS_RETIREMENT_RECORD_INVALID')
        expression = 'REMOVE operationsReceipt, operationsTerminalReasonCode'
        values = {':type': 'V1_CHECK_RECEIPT', ':policy': OWNER_POLICY, ':state': state,
                  ':metadata': metadata, ':index': row.get('GSI1PK'), ':sort': row.get('GSI1SK')}
        if state == 'SETTLED':
            expiry = integral(row.get('expiresAt'))
            if (expiry is None or expiry <= 0 or integral(row.get('retentionDeadlineEpoch')) != expiry
                    or row.get('GSI1PK') != 'V1_EXPIRING'
                    or row.get('GSI1SK') != f'{min(expiry, metadata["expiresAtEpoch"]):012d}#{partition}#{sort_key}'):
                raise AuthorityError('OPERATIONS_RETIREMENT_RECORD_INVALID')
            # Keep the full receipt/History expiry and every non-operations field.
            expression = 'SET GSI1SK = :receiptSort ' + expression
            values.update({':expiry': expiry, ':receiptSort': f'{expiry:012d}#{partition}#{sort_key}'})
        else:
            # Pending rows still belong to lease recovery; do not reschedule or
            # settle them merely to erase the operations link.
            if (row.get('GSI1PK') != 'V1_PENDING' or not isinstance(row.get('GSI1SK'), str)
                    or not re.fullmatch(r'[0-9]{12}#' + re.escape(partition) + '#' + PROOF, row['GSI1SK'])):
                raise AuthorityError('OPERATIONS_RETIREMENT_RECORD_INVALID')
        if metadata['expiresAtEpoch'] > now:
            return False
        try: inventory = self.guard._inventory(match.group(1))
        except AuthorityError: raise
        except Exception: raise AuthorityError('OPERATIONS_RETIREMENT_INVENTORY_UNAVAILABLE') from None
        access_key = {'PK': partition, 'SK': 'ACCESS'}
        access = self._get(access_key)
        access_condition = {'TableName': self.table, 'Key': access_key}
        if access is None:
            access_condition['ConditionExpression'] = 'attribute_not_exists(PK)'
        elif (access.get('recordType') == 'V1_ACCESS_AUTHORITY'
              and access.get('state') in ('ACTIVE', 'INACTIVE', 'REVOKED', 'DELETING')):
            access_condition.update(ConditionExpression='recordType = :type AND #state = :state',
                ExpressionAttributeNames={'#state': 'state'},
                ExpressionAttributeValues={':type': 'V1_ACCESS_AUTHORITY', ':state': access['state']})
        else:
            raise AuthorityError('OPERATIONS_RETIREMENT_ACCESS_UNAVAILABLE')
        condition = ('recordType = :type AND policyVersion = :policy AND #state = :state '
                     'AND operationsReceipt = :metadata AND GSI1PK = :index AND GSI1SK = :sort')
        if state == 'SETTLED':
            condition += ' AND expiresAt = :expiry AND retentionDeadlineEpoch = :expiry'
        action = {'TableName': self.table, 'Key': key, 'UpdateExpression': expression,
                  'ConditionExpression': condition, 'ExpressionAttributeNames': {'#state': 'state'},
                  'ExpressionAttributeValues': values}
        try:
            response = self.client.transact_write_items(TransactItems=[inventory_condition(self.table, inventory),
                {'ConditionCheck': access_condition}, {'Update': action}])
            if (type(response) is not dict or type(response.get('ResponseMetadata')) is not dict
                    or response['ResponseMetadata'].get('HTTPStatusCode') != 200):
                raise ValueError()
        except Exception:
            # No retry or upsert: an acknowledged absence on a deliberate read
            # resolves replay, but an ambiguous successful response is not counted.
            try: latest = self._get(key)
            except Exception: raise AuthorityError('OPERATIONS_RETIREMENT_TRANSACTION_UNCERTAIN') from None
            if latest is None or 'operationsReceipt' not in latest:
                return False
            raise AuthorityError('OPERATIONS_RETIREMENT_TRANSACTION_UNCERTAIN') from None
        return True
