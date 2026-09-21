"""Bounded HMAC-only expired-lease cleanup; no identity/provider access."""
import re
import hashlib
from .core import AuthorityError, OWNER_POLICY, integral


class Recovery:
    def __init__(self, resource, table, *, now):
        self.ddb, self.table, self.now = resource, table, now
        self.client = resource.meta.client
        if self.client.meta.config.retries.get('total_max_attempts') != 1:
            raise AuthorityError('SDK_RETRY_CONFIGURATION_UNAVAILABLE')

    def expire(self, partition, proof):
        if not isinstance(partition, str) or not re.fullmatch(r'V1#[A-Za-z0-9]{1,8}#[0-9a-f]{64}', partition):
            raise AuthorityError('RECOVERY_REFERENCE_INVALID')
        if not isinstance(proof, str) or not re.fullmatch(r'v1_[A-Za-z0-9]{1,8}_[0-9a-f]{8}_[0-9a-f]{16}_[0-9a-f]{24}', proof):
            raise AuthorityError('RECOVERY_REFERENCE_INVALID')
        key = {'PK': partition, 'SK': 'CHECK#' + proof}
        row = self.ddb.Table(self.table).get_item(Key=key, ConsistentRead=True).get('Item')
        if not row or row.get('state') == 'SETTLED': return False
        now = self.now()
        if row.get('recordType') != 'V1_CHECK_RECEIPT' or row.get('policyVersion') != OWNER_POLICY or row.get('settleByEpoch', now + 1) > now:
            return False
        items = [{'ConditionCheck': {'TableName': self.table, 'Key': {'PK': partition, 'SK': 'ACCESS'},
                    'ConditionExpression': 'recordType = :type AND attribute_exists(#state) AND #state <> :deleting',
                    'ExpressionAttributeNames': {'#state': 'state'},
                    'ExpressionAttributeValues': {':type': 'V1_ACCESS_AUTHORITY', ':deleting': 'DELETING'}}}]
        if row.get('periodSK'):
            items.append({'Update': {'TableName': self.table, 'Key': {'PK': partition, 'SK': row['periodSK']},
                'UpdateExpression': 'ADD reservedChecks :minus_one',
                'ConditionExpression': 'recordType = :type AND reservedChecks >= :one AND grantRevision = :revision AND policyVersion = :policy',
                'ExpressionAttributeValues': {':type': 'V1_ALLOWANCE_PERIOD', ':minus_one': -1, ':one': 1, ':revision': row['grantRevision'], ':policy': OWNER_POLICY}}})
        items.append({'Update': {'TableName': self.table, 'Key': {'PK': partition, 'SK': 'INFLIGHT'},
            'UpdateExpression': 'ADD activeCount :minus_one', 'ConditionExpression': 'activeCount >= :one',
            'ExpressionAttributeValues': {':minus_one': -1, ':one': 1}}})
        items.append({'Update': {'TableName': self.table, 'Key': key,
            'UpdateExpression': 'SET #state = :settled, chargedChecks = :zero, processingOutcome = :failed, receiptId = :receipt, expiresAt = :expiry, GSI1PK = :index, GSI1SK = :sort',
            'ConditionExpression': '#state = :admitted AND settleByEpoch <= :now AND executionToken = :token AND policyVersion = :policy',
            'ExpressionAttributeNames': {'#state': 'state'},
            'ExpressionAttributeValues': {':settled': 'SETTLED', ':zero': 0, ':failed': 'failed', ':receipt': 'expired_' + hashlib.sha256((partition + '\0' + proof).encode()).hexdigest()[:32], ':expiry': row['retentionDeadlineEpoch'], ':admitted': 'ADMITTED', ':now': now, ':token': row['executionToken'], ':policy': OWNER_POLICY, ':index':'V1_EXPIRING', ':sort':f'{int(row["retentionDeadlineEpoch"]):012d}#{partition}#{key["SK"]}'}}})
        if row.get('projectionScope') == 'recovery_clarification':
            # Background reconciliation retains usage only, with no narrative or
            # model dependency. Other historical scopes keep their exact shape.
            try:
                from shared_recovery_contract.constants import VERSION
                from shared_recovery_contract.usage import usage
                if row.get('recoveryTransportVersion') != VERSION: raise ValueError()
                summary = usage(row.get('clientCheckId'), 'failed')
            except Exception: raise AuthorityError('RECOVERY_REFERENCE_INVALID') from None
            update = items[-1]['Update']
            update['UpdateExpression'] += ', resultSummary = :summary, assessmentEpoch = :assessed'
            update['ExpressionAttributeValues'].update({':summary': summary, ':assessed': now})
        try: self.client.transact_write_items(TransactItems=items)
        except Exception: raise AuthorityError('RECOVERY_TRANSACTION_UNCERTAIN') from None
        return True

    def sweep(self, *, cursor=None, page_size=25, max_pages=4, can_continue=lambda:True):
        if type(page_size) is not int or not 1 <= page_size <= 25 or type(max_pages) is not int or not 1 <= max_pages <= 4:
            raise AuthorityError('RECOVERY_CONFIGURATION_INVALID')
        counts = {'examined': 0, 'recovered': 0, 'failed': 0, 'pages': 0, 'oldestOverdueSeconds': 0}
        for _ in range(max_pages):
            if not can_continue():break
            args = {'IndexName': 'GSI1', 'KeyConditionExpression': 'GSI1PK = :pending AND GSI1SK <= :deadline',
                    'ExpressionAttributeValues': {':pending': 'V1_PENDING', ':deadline': f'{self.now():012d}~'}, 'Limit': page_size}
            if cursor: args['ExclusiveStartKey'] = cursor
            result = self.ddb.Table(self.table).query(**args)
            counts['pages'] += 1
            for item in result.get('Items', []):
                if not can_continue():return counts | {'cursor':cursor}
                cursor={k:item[k] for k in ('PK','SK','GSI1PK','GSI1SK')}
                counts['examined'] += 1
                deadline=integral(item.get('settleByEpoch'))
                if deadline is not None:counts['oldestOverdueSeconds']=max(counts['oldestOverdueSeconds'],max(0,self.now()-deadline))
                try: counts['recovered'] += int(self.expire(item['PK'], item['checkId']))
                except (AuthorityError, KeyError, TypeError): counts['failed'] += 1
            # Advance even after a poison item; persisted cursor resumes next batch.
            cursor = result.get('LastEvaluatedKey')
            if not cursor: break
        return counts | {'cursor': cursor}
