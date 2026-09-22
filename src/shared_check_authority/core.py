"""Transactional V1 admission and complete-only allowance settlement.

All configuration/authority data is server-owned. Runtime activation is controlled by the
Dev adapters and infrastructure. No legacy entitlement fallback exists.
"""
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import re
import secrets
from decimal import Decimal

from shared_history.security import jwt_subject, SUBJECT_PATTERN, FINGERPRINT_PATTERN
from shared_history.errors import HistoryError

OWNER_POLICY = 'owner-2026-09-20-v1'
PAID_COMPLETED_CHECKS = 200
TRIAL_COMPLETED_CHECKS = 10
TRIAL_SECONDS = 7 * 24 * 60 * 60
VIRUS_TOTAL_ENABLED = False
CHARGEABLE_OUTCOMES = {'complete'}
OUTCOMES = {'inconclusive', 'complete', 'partial', 'failed', 'blocked', 'invalid_input', 'unsupported', 'unavailable'}


class AuthorityError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def integral(value):
    if isinstance(value, bool): return None
    if isinstance(value, int): return value
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value(): return int(value)
    return None


@dataclass(frozen=True)
class Settings:
    users_table: str
    devices_table: str
    deletion_table: str
    authority_table: str
    cognito_issuer: str
    cognito_app_client_id: str
    cognito_required_scope: str
    policy_version: str
    active_key_id: str
    hmac_keys: dict = field(repr=False)
    operation_validity_seconds: int
    worker_settlement_seconds: int
    reconciliation_seconds: int
    receipt_retention_seconds: int
    counter_retention_seconds: int
    attempt_window_seconds: int
    attempts_per_window: int
    max_inflight: int
    enabled: bool = False

    def validate(self):
        texts = (self.users_table, self.devices_table, self.deletion_table, self.authority_table,
                 self.cognito_issuer, self.cognito_app_client_id, self.cognito_required_scope)
        nums = (self.operation_validity_seconds, self.worker_settlement_seconds,
                self.reconciliation_seconds, self.receipt_retention_seconds,
                self.counter_retention_seconds, self.attempt_window_seconds,
                self.attempts_per_window, self.max_inflight)
        if (self.enabled is not True or any(not isinstance(x, str) or not x.strip() for x in texts)
                or self.policy_version != OWNER_POLICY
                or any(type(x) is not int or x <= 0 for x in nums)
                or self.receipt_retention_seconds < (self.operation_validity_seconds + self.worker_settlement_seconds + self.reconciliation_seconds)
                or self.counter_retention_seconds < self.receipt_retention_seconds
                or self.counter_retention_seconds < self.attempt_window_seconds
                or not isinstance(self.hmac_keys, dict) or not self.hmac_keys
                or self.active_key_id not in self.hmac_keys
                or any(not isinstance(k, str) or not re.fullmatch(r'[A-Za-z0-9]{1,8}', k)
                       or not isinstance(v, bytes) or len(v) < 32 for k, v in self.hmac_keys.items())):
            raise AuthorityError('POLICY_CONFIGURATION_UNAVAILABLE')


@dataclass(frozen=True)
class TrustedWorkerContext:
    """Created only by a future authenticated worker adapter, never client JSON."""
    account: str = field(repr=False)


class Authority:
    def __init__(self, settings, dynamodb_resource, *, now, nonce=lambda: secrets.token_hex(8)):
        settings.validate()
        self.s, self.ddb, self.now, self.nonce = settings, dynamodb_resource, now, nonce
        self.client = dynamodb_resource.meta.client
        if self.client.meta.config.retries.get("total_max_attempts") != 1:
            raise AuthorityError("SDK_RETRY_CONFIGURATION_UNAVAILABLE")

    def _get(self, table, key):
        return self.ddb.Table(table).get_item(Key=key, ConsistentRead=True).get('Item')

    def _mac(self, key_id, purpose, value):
        try: key = self.s.hmac_keys[key_id]
        except KeyError: raise AuthorityError('KEY_VERSION_UNAVAILABLE') from None
        return hmac.new(key, (purpose + '\0' + value).encode('utf-8'), hashlib.sha256).hexdigest()

    def _partition(self, account, key_id):
        return f'V1#{key_id}#{self._mac(key_id, "account", account)}'

    def deletion_partitions(self, account):
        """Inventory for a future deletion/rekey bridge: every retained key, no URL."""
        if not isinstance(account, str) or not SUBJECT_PATTERN.fullmatch(account):
            raise AuthorityError('INVALID_ACCOUNT')
        return [self._partition(account, k) for k in sorted(self.s.hmac_keys)]

    def _payload(self, account, payload, key_id, client_check_id=None):
        if isinstance(payload, dict) and payload.get('entryPoint') == 'recovery':
            try:
                from shared_recovery_contract.constants import VERSION, POLICY, PLAYBOOK
                from shared_recovery_contract.validation import validate_intent
                if payload.get('recoveryTransportVersion') != VERSION: raise ValueError()
                intent = {k:v for k,v in payload.items() if k != 'recoveryTransportVersion'}
                validate_intent(intent)
                if not isinstance(client_check_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', client_check_id): raise ValueError()
                bound = {'version':VERSION, 'policyVersion':POLICY, 'playbookVersion':PLAYBOOK,
                         'clientCheckId':client_check_id, 'intent':intent}
                encoded = json.dumps(bound, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
                return self._mac(key_id, 'recovery-payload-v1', account + '\0' + encoded)
            except Exception: raise AuthorityError('INPUT_REJECTED') from None
        if isinstance(payload,dict) and payload.get('entryPoint')=='message':
            try:
                from shared_message_contract import validate_intent, VERSION
                from shared_message_contract.validation_v2 import VERSION as V2, POLICY as P2
                version=payload.get('messageTransportVersion',VERSION)
                if version not in (VERSION,V2) or ('messageTransportVersion' in payload and version!=V2):raise ValueError()
                intent={k:v for k,v in payload.items() if k!='messageTransportVersion'}
                validate_intent(intent)
                if client_check_id is None or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',client_check_id):
                    raise ValueError()
                bound={'version':version,'clientCheckId':client_check_id,'intent':intent}
                if version==V2:bound['policyVersion']=P2
                encoded=json.dumps(bound,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
                return self._mac(key_id,'message-payload-v2' if version==V2 else 'message-payload-v1',account+'\0'+encoded)
            except Exception:raise AuthorityError('INPUT_REJECTED') from None
        # Hash the complete already-validated request intent, with exact URL/query
        # order/projection preserved. No URL normalization or raw persistence.
        if not isinstance(payload, dict) or set(payload) != {'entryPoint', 'language', 'target'}:
            raise AuthorityError('INPUT_REJECTED')
        if payload['entryPoint'] not in ('standalone_url', 'message_url', 'qr_url') or payload['language'] not in ('en', 'es'):
            raise AuthorityError('INPUT_REJECTED')
        target = payload['target']
        try:
            url_bytes = target.get('url', '').encode('utf-8') if isinstance(target, dict) else b''
        except (UnicodeError, AttributeError):
            raise AuthorityError('INPUT_REJECTED') from None
        if (not isinstance(target, dict) or set(target) != {'url', 'scope', 'withheldComponents'}
                or not isinstance(target['url'], str) or not 1 <= len(url_bytes) <= 2048
                or target['scope'] not in ('full_url', 'origin_only')
                or not isinstance(target['withheldComponents'], list)
                or any(x not in ('path', 'query', 'fragment') for x in target['withheldComponents'])):
            raise AuthorityError('INPUT_REJECTED')
        try: encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError, UnicodeError): raise AuthorityError('INPUT_REJECTED') from None
        if client_check_id is not None and (not isinstance(client_check_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', client_check_id)):
            raise AuthorityError('INPUT_REJECTED')
        bound = json.dumps({'clientCheckId': client_check_id, 'intent': encoded}, separators=(',', ':'), ensure_ascii=False)
        return self._mac(key_id, 'payload', account + '\0' + bound)

    def _account(self, event):
        try: account = jwt_subject(event, self.s, now=self.now)
        except (HistoryError, TypeError, ValueError, AttributeError):
            raise AuthorityError('AUTHENTICATION_REQUIRED') from None
        self._assert_account(account)
        return account

    def _assert_account(self, account):
        if not isinstance(account, str) or not SUBJECT_PATTERN.fullmatch(account):
            raise AuthorityError('INVALID_ACCOUNT')
        profile = self._get(self.s.users_table, {'PK': f'USER#{account}', 'SK': 'PROFILE'})
        deletion = self._get(self.s.deletion_table, {'PK': f'ACCOUNT#{account}', 'SK': 'ACCOUNT_DELETION'})
        if not profile or profile.get('sub') != account or profile.get('status') != 'ACTIVE' or profile.get('ageVerified') is not True or deletion is not None:
            raise AuthorityError('ACCOUNT_UNAVAILABLE')
        return account

    def _device(self, event, account):
        headers = event.get('headers')
        values = [v for k, v in headers.items() if isinstance(k, str) and k.lower() == 'x-device-binding-fingerprint'] if isinstance(headers, dict) else []
        if len(values) != 1 or not isinstance(values[0], str) or not FINGERPRINT_PATTERN.fullmatch(values[0]):
            raise AuthorityError('ACTIVE_DEVICE_REQUIRED')
        fingerprint = values[0]
        pointer = self._get(self.s.devices_table, {'PK': f'USER#{account}', 'SK': 'ACTIVE_BINDING'})
        version = integral(pointer.get('stateVersion')) if pointer else None
        if (not pointer or pointer.get('recordType') != 'ACTIVE_BINDING_POINTER'
                or version is None or version < 1 or not isinstance(pointer.get('bindingFingerprint'), str)
                or not hmac.compare_digest(pointer['bindingFingerprint'], fingerprint)):
            raise AuthorityError('ACTIVE_DEVICE_REQUIRED')
        binding = self._get(self.s.devices_table, {'PK': f'USER#{account}', 'SK': f'DEVICE#{fingerprint}'})
        if not binding or binding.get('accountId') != account or binding.get('status') != 'ACTIVE' or binding.get('bindingFingerprint') != fingerprint:
            raise AuthorityError('ACTIVE_DEVICE_REQUIRED')
        return fingerprint, version

    def _grant(self, partition, *, allow_exhausted=False):
        grant = self._get(self.s.authority_table, {'PK': partition, 'SK': 'ACCESS'})
        now = self.now()
        if (not grant or grant.get('recordType') != 'V1_ACCESS_AUTHORITY' or integral(grant.get('schemaVersion')) != 1
                or grant.get('state') != 'ACTIVE' or grant.get('policyVersion') != OWNER_POLICY
                or grant.get('basis') not in ('paid', 'trial', 'complimentary')
                or integral(grant.get('revision')) is None or integral(grant['revision']) < 1
                or integral(grant.get('validFromEpoch')) is None or grant['validFromEpoch'] > now
                or not (grant['basis'] == 'complimentary' and grant.get('validUntilEpoch', 'missing') is None)
                and (integral(grant.get('validUntilEpoch')) is None or grant['validUntilEpoch'] <= now)):
            raise AuthorityError('EXTERNAL_ACCESS_UNAVAILABLE')
        if grant['basis'] == 'complimentary': return grant, None
        if integral(grant.get('periodRevision')) is None or integral(grant['periodRevision']) < 1:
            raise AuthorityError('AUTHORITY_STATE_INVALID')
        period_id = grant.get('periodId')
        if not isinstance(period_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', period_id):
            raise AuthorityError('AUTHORITY_STATE_INVALID')
        period = self._get(self.s.authority_table, {'PK': partition, 'SK': 'PERIOD#' + period_id})
        limit = PAID_COMPLETED_CHECKS if grant['basis'] == 'paid' else TRIAL_COMPLETED_CHECKS
        if (not period or period.get('recordType') != 'V1_ALLOWANCE_PERIOD'
                or period.get('policyVersion') != OWNER_POLICY or period.get('grantRevision') != integral(grant.get('periodRevision'))
                or period.get('limit') != limit or integral(period.get('usedChecks')) is None
                or integral(period.get('reservedChecks')) is None or period['usedChecks'] < 0 or period['reservedChecks'] < 0
                or period['usedChecks'] + period['reservedChecks'] > limit
                or integral(period.get('startEpoch')) is None or integral(period.get('endEpoch')) is None
                or not period['startEpoch'] <= now < period['endEpoch']):
            raise AuthorityError('AUTHORITY_STATE_INVALID')
        if grant['basis'] == 'trial' and (grant.get('activationKind') != 'explicit' or integral(grant.get('activatedAtEpoch')) is None
                or grant['validFromEpoch'] != grant['activatedAtEpoch'] or grant['validUntilEpoch'] != grant['activatedAtEpoch'] + TRIAL_SECONDS):
            raise AuthorityError('AUTHORITY_STATE_INVALID')
        if not allow_exhausted and period['usedChecks'] + period['reservedChecks'] >= limit:
            raise AuthorityError('ALLOWANCE_EXHAUSTED')
        return grant, period

    def _check(self, table, key, expression, values=None, names=None):
        operation = {'TableName': table, 'Key': key, 'ConditionExpression': expression}
        if values: operation['ExpressionAttributeValues'] = values
        if names: operation['ExpressionAttributeNames'] = names
        return {'ConditionCheck': operation}

    def _account_conditions(self, account):
        from .inventory import verified_inventory, inventory_condition
        inventory = verified_inventory(self.ddb, self.s.authority_table, self.s.hmac_keys)
        return [inventory_condition(self.s.authority_table, inventory), self._check(self.s.users_table, {'PK': f'USER#{account}', 'SK': 'PROFILE'},
                           '#s = :active AND ageVerified = :yes AND #sub = :account',
                           {':active': 'ACTIVE', ':yes': True, ':account': account}, {'#s': 'status', '#sub': 'sub'}),
                self._check(self.s.deletion_table, {'PK': f'ACCOUNT#{account}', 'SK': 'ACCOUNT_DELETION'}, 'attribute_not_exists(PK)')]

    def _authority_conditions(self, account, partition, device, grant):
        fingerprint, version = device
        end_expression = 'validUntilEpoch = :end'
        end_values = {':end': grant['validUntilEpoch']}
        if grant['validUntilEpoch'] is not None:
            end_expression += ' AND validUntilEpoch > :now'
        period_expression = ''
        if grant['basis'] != 'complimentary':
            period_expression = ' AND periodId = :period AND periodRevision = :periodRevision'
            end_values.update({':period': grant['periodId'], ':periodRevision': grant['periodRevision']})
        return self._account_conditions(account) + [
            self._check(self.s.devices_table, {'PK': f'USER#{account}', 'SK': 'ACTIVE_BINDING'},
                        'recordType = :type AND stateVersion = :version AND bindingFingerprint = :fingerprint',
                        {':type': 'ACTIVE_BINDING_POINTER', ':version': version, ':fingerprint': fingerprint}),
            self._check(self.s.devices_table, {'PK': f'USER#{account}', 'SK': 'DEVICE#' + fingerprint},
                        '#s = :active AND accountId = :account AND bindingFingerprint = :fingerprint',
                        {':active': 'ACTIVE', ':account': account, ':fingerprint': fingerprint}, {'#s': 'status'}),
            self._check(self.s.authority_table, {'PK': partition, 'SK': 'ACCESS'},
                        'recordType = :type AND schemaVersion = :schema AND revision = :revision AND #s = :active AND basis = :basis AND policyVersion = :policy AND validFromEpoch = :start AND validFromEpoch <= :now AND ' + end_expression + period_expression,
                        {':type': 'V1_ACCESS_AUTHORITY', ':schema': 1, ':revision': grant['revision'], ':active': 'ACTIVE', ':basis': grant['basis'], ':policy': OWNER_POLICY, ':start': grant['validFromEpoch'], ':now': self.now(), **end_values}, {'#s': 'state'})]

    def _transact(self, items):
        # Resource client performs native Python serialization. No automatic retry:
        # an uncertain commit requires a same-ID read, never a new provider call.
        try: self.client.transact_write_items(TransactItems=items)
        except Exception:
            raise AuthorityError('TRANSACTION_UNCERTAIN') from None

    def _attempt(self, account, partition):
        window = self.now() - self.now() % self.s.attempt_window_seconds
        op = {'TableName': self.s.authority_table, 'Key': {'PK': partition, 'SK': f'ATTEMPT#{window}'},
              'UpdateExpression': 'SET expiresAt = :expires, recordType = :type, GSI1PK = :index, GSI1SK = :sort ADD attempts :one',
              'ConditionExpression': 'attribute_not_exists(attempts) OR attempts < :cap',
              'ExpressionAttributeValues': {':expires': window + self.s.counter_retention_seconds, ':one': 1, ':cap': self.s.attempts_per_window, ':type':'V1_ATTEMPT_COUNTER', ':index':'V1_EXPIRING', ':sort':f'{window + self.s.counter_retention_seconds:012d}#{partition}#ATTEMPT#{window}'}}
        try:
            self._transact(self._account_conditions(account) + [{'Update': op}])
        except AuthorityError:
            current = self._get(self.s.authority_table, op['Key'])
            if current and current.get('attempts', 0) >= self.s.attempts_per_window:
                raise AuthorityError('RATE_LIMITED') from None
            raise

    def _token_parts(self, check_id):
        if not isinstance(check_id, str) or not re.fullmatch(r'v1_[A-Za-z0-9]{1,8}_[0-9a-f]{8}_[0-9a-f]{16}_[0-9a-f]{24}', check_id):
            raise AuthorityError('OPERATION_PROOF_REQUIRED')
        _, key_id, expiry, nonce, tag = check_id.split('_')
        if key_id not in self.s.hmac_keys: raise AuthorityError('KEY_VERSION_UNAVAILABLE')
        return key_id, int(expiry, 16), nonce, tag

    def _verify_token(self, account, check_id, digest, *, allow_expired=False):
        key_id, expiry, nonce, tag = self._token_parts(check_id)
        expected = self._mac(key_id, 'operation', account + '\0' + digest + '\0' + f'{expiry:08x}' + '\0' + nonce)[:24]
        if not hmac.compare_digest(tag, expected): raise AuthorityError('CHECK_ID_CONFLICT')
        if not allow_expired and expiry <= self.now(): raise AuthorityError('OPERATION_EXPIRED')
        return key_id

    def prepare(self, event, payload, preparation_id, *, client_check_id=None, count_attempt=True):
        """Candidate lifecycle only: no provider, reservation, or customer charge."""
        account = self._account(event)
        key_id = self.s.active_key_id
        partition = self._partition(account, key_id)
        if count_attempt: self._attempt(account, partition)  # adapter may already count malformed intents
        device = self._device(event, account)
        grant, _ = self._grant(partition)
        digest = self._payload(account, payload, key_id, client_check_id)
        if not isinstance(preparation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', preparation_id): raise AuthorityError('INPUT_REJECTED')
        # The public client identity owns its preparation row; legacy internal
        # calls without a client identity retain their explicit preparation ID.
        key = {'PK': partition, 'SK': 'PREPARE#' + (client_check_id or preparation_id)}
        prior = self._get(self.s.authority_table, key)
        if prior:
            if not hmac.compare_digest(str(prior.get('payloadHmac', '')), digest): raise AuthorityError('CHECK_ID_CONFLICT')
            self._verify_token(account, prior['checkId'], digest)
            return prior['checkId']
        expiry = self.now() + self.s.operation_validity_seconds
        nonce = self.nonce()
        if not re.fullmatch(r'[0-9a-f]{16}', nonce) or expiry > 0xffffffff: raise AuthorityError('POLICY_CONFIGURATION_UNAVAILABLE')
        tag = self._mac(key_id, 'operation', account + '\0' + digest + '\0' + f'{expiry:08x}' + '\0' + nonce)[:24]
        check_id = f'v1_{key_id}_{expiry:08x}_{nonce}_{tag}'
        row = key | {'recordType': 'V1_PREPARATION', 'payloadHmac': digest, 'checkId': check_id, 'admissionState':'OPEN', 'clientCheckId':client_check_id,
                     'expiresAt': self.now() + self.s.receipt_retention_seconds,
                     'GSI1PK':'V1_EXPIRING','GSI1SK':f'{self.now() + self.s.receipt_retention_seconds:012d}#{partition}#{key["SK"]}'}
        if payload.get('entryPoint')=='message':row['projectionScope']='sanitized_message'
        if payload.get('entryPoint')=='recovery':row['projectionScope']='recovery_clarification'
        if payload.get('messageTransportVersion'):row['messageTransportVersion']=payload['messageTransportVersion']
        if payload.get('recoveryTransportVersion'):row['recoveryTransportVersion']=payload['recoveryTransportVersion']
        try:
            self._transact(self._authority_conditions(account, partition, device, grant) + [{'Put': {'TableName': self.s.authority_table, 'Item': row, 'ConditionExpression': 'attribute_not_exists(PK)'}}])
        except AuthorityError:
            prior = self._get(self.s.authority_table, key)
            if prior and hmac.compare_digest(str(prior.get('payloadHmac', '')), digest):
                self._verify_token(account, prior['checkId'], digest)
                return prior['checkId']
            raise
        return check_id

    def _receipt_public(self, row):
        return ({'recoveryTransportVersion':row['recoveryTransportVersion']} if 'recoveryTransportVersion' in row else {}) | ({'messageTransportVersion':row['messageTransportVersion']} if 'messageTransportVersion' in row else {}) | {k: row.get(k) for k in ('checkId', 'clientCheckId', 'projectionScope', 'state', 'chargedChecks', 'receiptId', 'processingOutcome', 'resultSummary', 'assessmentEpoch')} | {'expiresAt': row.get('retentionDeadlineEpoch', row.get('expiresAt'))}

    def admit(self, event, payload, check_id, *, client_check_id=None, count_attempt=True):
        account = self._account(event)
        key_id, _, _, _ = self._token_parts(check_id)
        partition = self._partition(account, key_id)
        if count_attempt: self._attempt(account, partition)
        digest = self._payload(account, payload, key_id, client_check_id)
        self._verify_token(account, check_id, digest)
        key = {'PK': partition, 'SK': 'CHECK#' + check_id}
        prior = self._get(self.s.authority_table, key)
        if prior:
            if not hmac.compare_digest(str(prior.get('payloadHmac', '')), digest): raise AuthorityError('CHECK_ID_CONFLICT')
            return {'admitted': False, 'receipt': self._receipt_public(prior)}
        if key_id != self.s.active_key_id: raise AuthorityError('KEY_ROTATION_PENDING')
        device = self._device(event, account)
        grant, period = self._grant(partition)
        token = secrets.token_hex(16)
        row = key | {'recordType': 'V1_CHECK_RECEIPT', 'checkId': check_id, 'payloadHmac': digest,
                     'clientCheckId': client_check_id, 'projectionScope': payload['target']['scope'],
                     'state': 'ADMITTED', 'chargedChecks': None, 'receiptId': None, 'processingOutcome': None,
                     'basis': grant['basis'], 'grantRevision': period['grantRevision'] if period else None,
                     'authorityRevision': grant['revision'], 'policyVersion': OWNER_POLICY,
                     'periodSK': period['SK'] if period else None, 'executionToken': token,
                     'settleByEpoch': self.now() + self.s.worker_settlement_seconds,
                     'GSI1PK': 'V1_PENDING',
                     'GSI1SK': f'{self.now() + self.s.worker_settlement_seconds:012d}#{partition}#{check_id}',
                     'retentionDeadlineEpoch': self.now() + self.s.receipt_retention_seconds}
        if payload.get('messageTransportVersion'):row['messageTransportVersion']=payload['messageTransportVersion']
        if payload.get('recoveryTransportVersion'):row['recoveryTransportVersion']=payload['recoveryTransportVersion']
        items = self._authority_conditions(account, partition, device, grant)
        if client_check_id is not None:
            items.append(self._check(self.s.authority_table, {'PK':partition,'SK':'PREPARE#'+client_check_id},
                'recordType = :type AND admissionState = :open AND checkId = :proof AND payloadHmac = :digest AND clientCheckId = :client AND expiresAt > :now',
                {':type':'V1_PREPARATION',':open':'OPEN',':proof':check_id,':digest':digest,':client':client_check_id,':now':self.now()}))
        if period:
            items.append({'Update': {'TableName': self.s.authority_table, 'Key': {'PK': partition, 'SK': period['SK']},
                'UpdateExpression': 'ADD reservedChecks :one',
                'ConditionExpression': 'usedChecks = :used AND reservedChecks = :reserved AND grantRevision = :revision AND policyVersion = :policy AND endEpoch > :now',
                'ExpressionAttributeValues': {':one': 1, ':used': period['usedChecks'], ':reserved': period['reservedChecks'], ':revision': period['grantRevision'], ':policy': OWNER_POLICY, ':now': self.now()}}})
        items.append({'Update': {'TableName': self.s.authority_table, 'Key': {'PK': partition, 'SK': 'INFLIGHT'},
            'UpdateExpression': 'REMOVE expiresAt ADD activeCount :one',
            'ConditionExpression': 'attribute_not_exists(activeCount) OR activeCount < :cap',
            'ExpressionAttributeValues': {':one': 1, ':cap': self.s.max_inflight}}})
        items.append({'Put': {'TableName': self.s.authority_table, 'Item': row, 'ConditionExpression': 'attribute_not_exists(PK)'}})
        try: self._transact(items)
        except AuthorityError:
            prior = self._get(self.s.authority_table, key)
            if prior and hmac.compare_digest(str(prior.get('payloadHmac', '')), digest):
                return {'admitted': False, 'receipt': self._receipt_public(prior)}
            inflight = self._get(self.s.authority_table, {'PK': partition, 'SK': 'INFLIGHT'})
            if inflight and inflight.get('activeCount', 0) >= self.s.max_inflight:
                raise AuthorityError('RATE_LIMITED') from None
            raise
        return {'admitted': True, 'executionToken': token, 'receipt': self._receipt_public(row)}

    def reconcile(self, event, check_id, *, client_check_id=None, expected_scope=None, expected_message_version=None, expected_recovery_version=None):
        account = self._account(event)  # no device/subscription gate or write/provider
        key_id, _, _, _ = self._token_parts(check_id)
        row = self._get(self.s.authority_table, {'PK': self._partition(account, key_id), 'SK': 'CHECK#' + check_id})
        if expected_scope is not None:
            if expected_scope not in ('url','sanitized_message','recovery_clarification'):raise AuthorityError('INPUT_REJECTED')
            candidate=row
            if candidate is None and isinstance(client_check_id,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',client_check_id):
                candidate=self._get(self.s.authority_table,{'PK':self._partition(account,key_id),'SK':'PREPARE#'+client_check_id})
            actual_scope = candidate.get('projectionScope') if candidate else None
            if actual_scope not in ('sanitized_message','recovery_clarification'): actual_scope = 'url'
            if candidate and actual_scope != expected_scope:
                raise AuthorityError('CHECK_ID_CONFLICT')
        if expected_message_version is not None:
            from shared_message_contract import VERSION as V1
            from shared_message_contract.validation_v2 import VERSION as V2
            if expected_message_version not in (V1,V2):raise AuthorityError('INPUT_REJECTED')
            candidate=row
            if candidate is None and isinstance(client_check_id,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',client_check_id):
                candidate=self._get(self.s.authority_table,{'PK':self._partition(account,key_id),'SK':'PREPARE#'+client_check_id})
            if candidate and candidate.get('messageTransportVersion',V1)!=expected_message_version:raise AuthorityError('CHECK_ID_CONFLICT')
        if expected_recovery_version is not None:
            from shared_recovery_contract.constants import VERSION
            if expected_recovery_version != VERSION: raise AuthorityError('INPUT_REJECTED')
            candidate = row
            if candidate is None and isinstance(client_check_id,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',client_check_id):
                candidate=self._get(self.s.authority_table,{'PK':self._partition(account,key_id),'SK':'PREPARE#'+client_check_id})
            if candidate and candidate.get('recoveryTransportVersion') != VERSION: raise AuthorityError('CHECK_ID_CONFLICT')
        if not row:
            return self._close_unadmitted(account,check_id,client_check_id)
        if row.get('retentionDeadlineEpoch', row.get('expiresAt', 0)) <= self.now(): return {'state': 'UNKNOWN', 'chargedChecks': None}
        self._verify_token(account, check_id, row['payloadHmac'], allow_expired=True)
        return self._receipt_public(row)

    def _close_unadmitted(self, account, proof, client_check_id):
        unknown={'state':'UNKNOWN','chargedChecks':None}
        if not isinstance(client_check_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',client_check_id):return unknown
        key_id,admission_expiry,_,_=self._token_parts(proof)
        if admission_expiry>self.now():return unknown
        partition=self._partition(account,key_id)
        key={'PK':partition,'SK':'PREPARE#'+client_check_id}
        preparation=self._get(self.s.authority_table,key)
        if (not preparation or preparation.get('recordType')!='V1_PREPARATION'
                or preparation.get('clientCheckId')!=client_check_id or preparation.get('checkId')!=proof
                or integral(preparation.get('expiresAt')) is None or preparation['expiresAt']<=self.now()
                or preparation.get('admissionState') not in ('OPEN','CLOSED')):return unknown
        self._verify_token(account,proof,preparation['payloadHmac'],allow_expired=True)
        check_key={'PK':partition,'SK':'CHECK#'+proof}
        items=self._account_conditions(account)+[
            self._check(self.s.authority_table,check_key,'attribute_not_exists(PK)'),
            {'Update':{'TableName':self.s.authority_table,'Key':key,
                'UpdateExpression':'SET admissionState = :closed',
                'ConditionExpression':'recordType = :type AND admissionState = :previous AND checkId = :proof AND payloadHmac = :digest AND clientCheckId = :client AND expiresAt = :expiry AND expiresAt > :now',
                'ExpressionAttributeValues':{':closed':'CLOSED',':previous':preparation['admissionState'],':type':'V1_PREPARATION',':proof':proof,':digest':preparation['payloadHmac'],':client':client_check_id,':expiry':preparation['expiresAt'],':now':self.now()}}}]
        try:self._transact(items)
        except AuthorityError:
            latest=self._get(self.s.authority_table,check_key)
            if latest and latest.get('retentionDeadlineEpoch',0)>self.now():
                self._verify_token(account,proof,latest['payloadHmac'],allow_expired=True)
                return self._receipt_public(latest)
            # An uncertain successful close is not asserted as zero without a
            # subsequent successful authoritative reconciliation transaction.
            raise
        return {'state':'NOT_STARTED','chargedChecks':0,'clientCheckId':client_check_id,'expiresAt':preparation['expiresAt']}

    def recover_expired(self, event, check_id):
        """Authenticated account control only: expire a lease without provider replay."""
        account = self._account(event)
        return self._settle(TrustedWorkerContext(account), check_id, None, 'failed', expired_recovery=True)

    def settle(self, worker, check_id, execution_token, processing_outcome, *, result_summary=None):
        return self._settle(worker, check_id, execution_token, processing_outcome, expired_recovery=False, result_summary=result_summary)

    def _settle(self, worker, check_id, execution_token, processing_outcome, *, expired_recovery, result_summary=None):
        """Internal worker boundary: account/outcome must come from trusted dispatch.

        Never bind directly to client JSON. The adapter must authenticate the
        worker, preserve admitted account ownership, and validate its result.
        Settlement uses the execution proof, independent of expired user JWTs.
        """
        if not isinstance(worker, TrustedWorkerContext): raise AuthorityError("TRUSTED_WORKER_REQUIRED")
        account = worker.account
        self._assert_account(account)
        key_id, _, _, _ = self._token_parts(check_id)
        partition = self._partition(account, key_id)
        key = {'PK': partition, 'SK': 'CHECK#' + check_id}
        row = self._get(self.s.authority_table, key)
        if not row or row.get('retentionDeadlineEpoch', row.get('expiresAt', 0)) <= self.now(): raise AuthorityError('RECONCILIATION_REQUIRED')
        self._verify_token(account, check_id, row['payloadHmac'], allow_expired=True)
        if not expired_recovery and (not isinstance(execution_token, str) or not hmac.compare_digest(str(row.get('executionToken', '')), execution_token)):
            raise AuthorityError('EXECUTION_OWNER_MISMATCH')
        if not isinstance(processing_outcome, str) or processing_outcome not in OUTCOMES: raise AuthorityError('OUTCOME_UNSUPPORTED')
        if row['state'] == 'SETTLED': return self._receipt_public(row)
        if expired_recovery:
            if row.get('settleByEpoch', 0) > self.now(): raise AuthorityError('OPERATION_PENDING')
            execution_token = row['executionToken']
        elif row.get('settleByEpoch', 0) <= self.now():
            raise AuthorityError('RECONCILIATION_REQUIRED')
        charge = int(processing_outcome in CHARGEABLE_OUTCOMES and row['basis'] != 'complimentary')
        MESSAGE_V2='1.0.0-message-candidate.2'  # No message-package dependency for URL/lease workers.
        if row.get('messageTransportVersion')==MESSAGE_V2 and processing_outcome=='complete' and result_summary is None:
            raise AuthorityError('RESULT_SUMMARY_INVALID')
        if row.get('projectionScope') == 'recovery_clarification':
            try:
                from shared_recovery_contract.usage import usage
                from shared_recovery_contract.constants import VERSION
                if row.get('recoveryTransportVersion') != VERSION: raise ValueError()
                if not expired_recovery:
                    from shared_recovery_contract.validation import validate_outcome
                    validate_outcome(result_summary, row.get('clientCheckId'), processing_outcome)
                result_summary = usage(row.get('clientCheckId'), processing_outcome)
            except Exception: raise AuthorityError('RESULT_SUMMARY_INVALID') from None
        elif result_summary is not None:
            if row.get('projectionScope')=='sanitized_message':
                try:
                    from shared_message_contract import validate_summary
                    if row.get('messageTransportVersion')==MESSAGE_V2:
                        from shared_message_contract.validation_v2 import validate_summary
                    result_summary=validate_summary(result_summary,row.get('clientCheckId'),processing_outcome)
                except Exception:raise AuthorityError('RESULT_SUMMARY_INVALID') from None
            else:
                from .summary import validate_summary
                result_summary = validate_summary(result_summary, row.get('clientCheckId'), processing_outcome)
        receipt_id = self._mac(key_id, 'receipt', check_id)[:32]
        items = self._account_conditions(account)
        if row['periodSK']:
            items.append({'Update': {'TableName': self.s.authority_table, 'Key': {'PK': partition, 'SK': row['periodSK']},
                'UpdateExpression': 'ADD reservedChecks :minus_one, usedChecks :charge',
                'ConditionExpression': 'reservedChecks >= :one AND grantRevision = :revision AND policyVersion = :policy',
                'ExpressionAttributeValues': {':minus_one': -1, ':charge': charge, ':one': 1, ':revision': row['grantRevision'], ':policy': row['policyVersion']}}})
        items.append({'Update': {'TableName': self.s.authority_table, 'Key': {'PK': partition, 'SK': 'INFLIGHT'},
            'UpdateExpression': 'ADD activeCount :minus_one', 'ConditionExpression': 'activeCount >= :one',
            'ExpressionAttributeValues': {':minus_one': -1, ':one': 1}}})
        items.append({'Update': {'TableName': self.s.authority_table, 'Key': key,
            'UpdateExpression': 'SET #s = :settled, chargedChecks = :charge, receiptId = :receipt, processingOutcome = :outcome, expiresAt = :expiry, resultSummary = :summary, assessmentEpoch = :assessed, GSI1PK = :index, GSI1SK = :sort',
            'ConditionExpression': '#s = :admitted AND executionToken = :token AND policyVersion = :policy AND settleByEpoch ' + ('<= :now' if expired_recovery else '> :now'),
            'ExpressionAttributeNames': {'#s': 'state'},
            'ExpressionAttributeValues': {':settled': 'SETTLED', ':charge': charge, ':receipt': receipt_id, ':outcome': processing_outcome,
                                          ':admitted': 'ADMITTED', ':token': execution_token, ':policy': OWNER_POLICY, ':now': self.now(), ':expiry': row['retentionDeadlineEpoch'], ':summary': result_summary, ':assessed': self.now(), ':index':'V1_EXPIRING', ':sort':f'{int(row["retentionDeadlineEpoch"]):012d}#{partition}#{key["SK"]}'}}})
        try: self._transact(items)
        except AuthorityError:
            latest = self._get(self.s.authority_table, key)
            if latest and latest.get('state') == 'SETTLED': return self._receipt_public(latest)
            raise
        return self._receipt_public(self._get(self.s.authority_table, key))
