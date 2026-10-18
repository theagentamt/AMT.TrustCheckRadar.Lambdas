"""Trusted receipt-local ingestion; no endpoint, standalone locator or dispatch.

Only a server-created worker context and existing check proof are accepted.
The projection lives on its owning receipt, so existing partition deletion owns
it and the original operations deadline controls both copies of linkage.
"""
import hashlib
import hmac
import re
from copy import deepcopy

from botocore.exceptions import ClientError

from .core import Authority, AuthorityError, OWNER_POLICY, SUBJECT_PATTERN, TrustedWorkerContext, integral
from .expiry import Expiry
from .inventory import inventory_condition
from .purchase_usage import exact_condition
from .operations_binding import ReceiptBinding
from shared_operations import retirement_metadata
from shared_operations.projection import capture_projection, validate_capture


class ReceiptIngestion:
    def __init__(self, authority, *, can_continue=lambda: True):
        if not isinstance(authority, Authority):
            raise AuthorityError('OPERATIONS_INGESTION_CONFIGURATION_INVALID')
        self.a, self.client, self.can_continue = authority, authority.client, can_continue
        self.guard = Expiry(authority.ddb, authority.s.authority_table, now=authority.now)
        self.guard._get = lambda key: self._get(self.a.s.authority_table, key)

    def _continue(self):
        try:
            if self.can_continue() is True: return
        except Exception:
            pass
        raise AuthorityError('OPERATIONS_INGESTION_TIME_BUDGET')

    def _clock(self):
        try:
            value = self.a.now()
            if type(value) is not int or not 0 <= value <= 10**13:
                raise ValueError()
            return value
        except Exception:
            raise AuthorityError('OPERATIONS_INGESTION_CLOCK_UNAVAILABLE') from None

    def _get(self, table, key):
        self._continue()
        try:
            response = self.a.ddb.Table(table).get_item(Key=key, ConsistentRead=True)
            if (type(response) is not dict or type(response.get('ResponseMetadata')) is not dict
                    or response['ResponseMetadata'].get('HTTPStatusCode') != 200
                    or response.get('Item') is not None and type(response['Item']) is not dict):
                raise ValueError()
            return response.get('Item')
        except Exception:
            raise AuthorityError('OPERATIONS_INGESTION_READ_UNAVAILABLE') from None

    def capture(self, worker, check_id, *, binding=None):
        return self._capture(worker, check_id, binding=binding)

    def start_attempt(self, worker, check_id, execution_token):
        """Record one internal start; acknowledgement is not a dispatch permit."""
        return self._capture(worker, check_id, attempt=('start', execution_token))

    def finish_attempt(self, worker, check_id, execution_token, attempt_ref, *, outcome, duration_ms):
        """Record only a matching internal finish; no provider/cost inference."""
        return self._capture(worker, check_id,
            attempt=('finish', execution_token, attempt_ref, outcome, duration_ms))

    def _capture(self, worker, check_id, *, binding=None, attempt=None):
        if self.a.s.operations_producer_enabled is not True:
            raise AuthorityError('OPERATIONS_INGESTION_DISABLED')
        if not isinstance(worker, TrustedWorkerContext):
            raise AuthorityError('TRUSTED_WORKER_REQUIRED')
        account = worker.account
        if not isinstance(account, str) or not SUBJECT_PATTERN.fullmatch(account):
            raise AuthorityError('INVALID_ACCOUNT')
        observed = self._clock()
        key_id, _, _, _ = self.a._token_parts(check_id)
        partition = self.a._partition(account, key_id)
        key = {'PK': partition, 'SK': 'CHECK#' + check_id}
        row = self._get(self.a.s.authority_table, key)
        if row is None:
            raise AuthorityError('OPERATIONS_INGESTION_SOURCE_UNAVAILABLE')
        try:
            if (row.get('recordType') != 'V1_CHECK_RECEIPT' or row.get('policyVersion') != OWNER_POLICY
                    or row.get('PK') != partition or row.get('SK') != key['SK'] or row.get('checkId') != check_id
                    or not isinstance(row.get('payloadHmac'), str) or not re.fullmatch(r'[0-9a-f]{64}', row['payloadHmac'])):
                raise ValueError()
            self.a._verify_token(account, check_id, row['payloadHmac'], allow_expired=True)
        except Exception:
            raise AuthorityError('OPERATIONS_INGESTION_SOURCE_INVALID') from None
        if binding is not None:
            try:
                if type(binding) is not ReceiptBinding or not binding.matches(row):
                    raise ValueError()
            except Exception:
                raise AuthorityError('OPERATIONS_INGESTION_SOURCE_INVALID') from None
        if attempt is not None:
            token = attempt[1]
            if (type(token) is not str or not re.fullmatch(r'[0-9a-f]{32}', token)
                    or type(row.get('executionToken')) is not str
                    or not re.fullmatch(r'[0-9a-f]{32}', row['executionToken'])
                    or not hmac.compare_digest(row['executionToken'], token)):
                raise AuthorityError('OPERATIONS_INGESTION_SOURCE_INVALID')
        if 'operationsReceipt' not in row:
            if 'operationsProjection' in row:
                try: orphan = validate_capture(row['operationsProjection'])
                except Exception: raise AuthorityError('OPERATIONS_INGESTION_SOURCE_INVALID') from None
                if (observed < orphan['reference']['expiresAtEpoch']
                        or orphan['reference']['environment'] != self.a.s.operations_environment
                        or binding is not None and orphan['reference']['service'] != binding.family):
                    raise AuthorityError('OPERATIONS_INGESTION_SOURCE_INVALID')
                return self._retire_expired(partition, key['SK'])
            return {'decision': 'NOT_RECORDED', 'projection': None}
        try:
            metadata = retirement_metadata(row)
            if (metadata['environment'] != self.a.s.operations_environment
                    or binding is not None and metadata['service'] != binding.family):
                raise ValueError()
            expiry = integral(row.get('retentionDeadlineEpoch'))
            if (expiry is None or expiry < metadata['expiresAtEpoch']
                    or row.get('state') == 'SETTLED' and integral(row.get('expiresAt')) != expiry):
                raise ValueError()
        except Exception:
            raise AuthorityError('OPERATIONS_INGESTION_SOURCE_INVALID') from None
        due_sort = None
        if row.get('state') == 'SETTLED':
            due_sort = f'{min(expiry, metadata["expiresAtEpoch"]):012d}#{partition}#{key["SK"]}'
            full_sort = f'{expiry:012d}#{partition}#{key["SK"]}'
            if row.get('GSI1PK') != 'V1_EXPIRING' or row.get('GSI1SK') not in (due_sort, full_sort):
                raise AuthorityError('OPERATIONS_INGESTION_SOURCE_INVALID')
        elif row.get('state') == 'ADMITTED':
            settle_by = integral(row.get('settleByEpoch'))
            if (settle_by is None or settle_by < 0 or row.get('GSI1PK') != 'V1_PENDING'
                    or row.get('GSI1SK') != f'{settle_by:012d}#{partition}#{check_id}'):
                raise AuthorityError('OPERATIONS_INGESTION_SOURCE_INVALID')
        if observed >= metadata['expiresAtEpoch']:
            return self._retire_expired(partition, key['SK'])
        try:
            projected = capture_projection(row, now_epoch=observed)
            prior = validate_capture(row['operationsProjection']) if 'operationsProjection' in row else None
            if attempt is not None:
                projected = self._attempt_projection(row, projected, attempt, observed)
            if prior is not None and (prior['reference'] != metadata
                    or prior['sourceState'] == 'SETTLED' and projected['sourceState'] != 'SETTLED'
                    or any(event not in projected['events'] for event in prior['events'])):
                raise ValueError()
        except Exception:
            raise AuthorityError('OPERATIONS_INGESTION_SOURCE_INVALID') from None
        inventory = self.guard._inventory(key_id)
        expected_keys = {kid: hashlib.sha256(material).hexdigest() for kid, material in self.a.s.hmac_keys.items()}
        if inventory['issuedKeys'] != expected_keys:
            raise AuthorityError('OPERATIONS_INGESTION_INVENTORY_UNAVAILABLE')
        access_key = {'PK': partition, 'SK': 'ACCESS'}
        access = self._get(self.a.s.authority_table, access_key)
        if (access is None or access.get('recordType') != 'V1_ACCESS_AUTHORITY'
                or access.get('state') not in ('ACTIVE', 'INACTIVE', 'REVOKED')
                or access.get('policyVersion') != OWNER_POLICY or integral(access.get('schemaVersion')) != 1
                or integral(access.get('revision')) is None or access['revision'] < 1):
            raise AuthorityError('OPERATIONS_INGESTION_ACCOUNT_UNAVAILABLE')
        profile_key = {'PK': 'USER#' + account, 'SK': 'PROFILE'}
        profile = self._get(self.a.s.users_table, profile_key)
        deletion_key = {'PK': 'ACCOUNT#' + account, 'SK': 'ACCOUNT_DELETION'}
        deletion = self._get(self.a.s.deletion_table, deletion_key)
        if (profile is None or profile.get('sub') != account or profile.get('status') != 'ACTIVE'
                or profile.get('ageVerified') is not True or deletion is not None):
            raise AuthorityError('OPERATIONS_INGESTION_ACCOUNT_UNAVAILABLE')
        dispatch_time = self._clock()
        if dispatch_time < observed:
            raise AuthorityError('OPERATIONS_INGESTION_CLOCK_UNAVAILABLE')
        if dispatch_time >= metadata['expiresAtEpoch']:
            return {'decision': 'EXPIRED', 'projection': None}
        if attempt is not None and row['state'] == 'ADMITTED':
            if integral(row.get('settleByEpoch')) is None or dispatch_time >= row['settleByEpoch']:
                raise AuthorityError('OPERATIONS_INGESTION_SOURCE_INVALID')
        condition = exact_condition(row)
        for optional in ('operationsProjection', 'operationsTerminalReasonCode', 'assessmentEpoch',
                         'chargedChecks', 'receiptId', 'processingOutcome', 'clientCheckId', 'projectionScope',
                         'urlTransportVersion', 'messageTransportVersion', 'recoveryTransportVersion'):
            if optional not in row:
                condition['ConditionExpression'] += f' AND attribute_not_exists({optional})'
        condition['ConditionExpression'] += ' AND operationsReceipt.expiresAtEpoch > :dispatchTime'
        condition['ExpressionAttributeValues'][':dispatchTime'] = dispatch_time
        if attempt is not None and row['state'] == 'ADMITTED':
            condition['ConditionExpression'] += ' AND settleByEpoch > :dispatchTime'
        action = {'TableName': self.a.s.authority_table, 'Key': key, **condition}
        unchanged = prior == projected and (due_sort is None or row['GSI1SK'] == due_sort)
        if unchanged:
            source_action = {'ConditionCheck': action}
        else:
            action.update(UpdateExpression='SET operationsProjection = :projection')
            action['ExpressionAttributeValues'][':projection'] = projected
            if due_sort is not None:
                action['UpdateExpression'] += ', GSI1SK = :dueSort'
                action['ExpressionAttributeValues'][':dueSort'] = due_sort
            source_action = {'Update': action}
        transaction = [inventory_condition(self.a.s.authority_table, inventory),
            {'ConditionCheck': {'TableName': self.a.s.authority_table, 'Key': access_key, **exact_condition(access)}},
            self.a._check(self.a.s.users_table, profile_key,
                '#state = :active AND ageVerified = :yes AND #subject = :owner',
                {':active': 'ACTIVE', ':yes': True, ':owner': account}, {'#state': 'status', '#subject': 'sub'}),
            self.a._check(self.a.s.deletion_table, deletion_key, 'attribute_not_exists(PK)'), source_action]
        self._continue()
        try:
            response = self.client.transact_write_items(TransactItems=transaction)
            if (type(response) is not dict or type(response.get('ResponseMetadata')) is not dict
                    or response['ResponseMetadata'].get('HTTPStatusCode') != 200):
                raise ValueError()
        except ClientError as error:
            try:
                reasons = error.response.get('CancellationReasons')
                conditional = (error.response.get('Error', {}).get('Code') == 'TransactionCanceledException'
                    and type(reasons) is list and len(reasons) == len(transaction)
                    and all(type(r) is dict and r.get('Code') in ('None', 'ConditionalCheckFailed') for r in reasons)
                    and any(r.get('Code') == 'ConditionalCheckFailed' for r in reasons))
            except Exception:
                conditional = False
            if conditional:
                raise AuthorityError('OPERATIONS_INGESTION_CONFLICT') from None
            raise AuthorityError('OPERATIONS_INGESTION_WRITE_UNKNOWN') from None
        except Exception:
            raise AuthorityError('OPERATIONS_INGESTION_WRITE_UNKNOWN') from None
        finished = self._clock()
        if finished < dispatch_time:
            raise AuthorityError('OPERATIONS_INGESTION_CLOCK_UNAVAILABLE')
        if finished >= metadata['expiresAtEpoch']:
            return {'decision': 'EXPIRED', 'projection': None}
        if attempt is not None and row['state'] == 'ADMITTED' and finished >= row['settleByEpoch']:
            raise AuthorityError('OPERATIONS_INGESTION_TIME_BUDGET')
        return {'decision': 'REVALIDATED' if unchanged else 'CAPTURED', 'projection': deepcopy(projected)}

    def _attempt_projection(self, row, projected, attempt, observed):
        from shared_operations.attempts import start_internal_attempt, finish_internal_attempt, duration_bucket
        prior = [event for event in projected['events'] if event['eventType'].startswith('attempt_')]
        if attempt[0] == 'start':
            if row['state'] != 'ADMITTED':
                raise ValueError()
            if not prior:
                prior = [start_internal_attempt(projected['reference'], observed_at=observed)]
        else:
            _, _, reference, outcome, duration = attempt
            if (not prior or type(reference) is not str or reference != prior[0]['attemptRef']
                    or outcome not in ('succeeded', 'failed')):
                raise ValueError()
            if len(prior) == 2:
                if prior[1]['attemptOutcome'] != outcome or prior[1]['durationBucket'] != duration_bucket(duration):
                    raise ValueError()
            else:
                prior.append(finish_internal_attempt(projected['reference'], prior[0], observed_at=observed,
                    outcome=outcome, duration_ms=duration))
        check_events = [event for event in projected['events'] if not event['eventType'].startswith('attempt_')]
        if observed < prior[-1]['eventTimeEpoch']:
            raise ValueError()
        return validate_capture({**projected, 'schemaVersion': 2, 'events': check_events + prior})

    def _retire_expired(self, partition, sort_key):
        from .operations_retirement import OperationsRetirement
        self._continue()
        OperationsRetirement(self.a.ddb, self.a.s.authority_table, now=self.a.now,
            can_continue=self.can_continue).retire(partition, sort_key)
        return {'decision': 'EXPIRED', 'projection': None}
