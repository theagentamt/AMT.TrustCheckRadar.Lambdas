"""Read-only compatibility. Missing evidence never creates work or allowance."""
from datetime import datetime
from decimal import Decimal
import hashlib
import hmac
import json
from uuid import UUID

from errors import AppError
from shared_history.contracts import _assessment, _json_compatible, response_from_history_item
from shared_history.security import assert_authoritative_account_active, device_binding_fingerprint


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def payload_digest(payload):
    return digest(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True))


def integer(value):
    return int(value) if not isinstance(value, bool) and isinstance(value, (int, Decimal)) and value == int(value) else None


def blocked(code='LEGACY_RECONCILIATION_REQUIRED'):
    raise AppError(code, 'This legacy request requires reconciliation. No new check was started or charged.', retryable=False)


def _epoch(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return int(parsed.timestamp()) if parsed.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def completed_shape(row):
    required = {'PK', 'SK', 'status', 'payloadHash', 'completedAt', 'expiresAt', 'ttl',
                'response', 'createdAt', 'updatedAt', 'resultReadyAt'}
    optional = {'historyAuthorization', 'campaignAuthorization', 'statisticsEventId'}
    if not required <= set(row) or not set(row) <= required | optional:
        return False
    times = [_epoch(row.get(key)) for key in ('createdAt', 'resultReadyAt', 'completedAt', 'updatedAt')]
    if any(value is None for value in times) or not times[0] <= times[1] <= times[2] == times[3]:
        return False
    if 'historyAuthorization' in row:
        auth = row['historyAuthorization']
        if (not isinstance(auth, dict) or set(auth) != {'historyGeneration', 'recognitionGeneration', 'acceptedSequence', 'acceptedAtEpochMs'}
                or any(integer(value) is None or value < 0 for value in auth.values())
                or auth['acceptedSequence'] < 1 or auth['acceptedAtEpochMs'] > times[2] * 1000):
            return False
    if ('campaignAuthorization' in row) != ('statisticsEventId' in row):
        return False
    if 'campaignAuthorization' in row:
        auth = row['campaignAuthorization']
        try:
            for value in (row['statisticsEventId'], auth['consentEpochId']):
                parsed = UUID(value)
                if parsed.version != 4 or str(parsed) != value:
                    return False
        except (KeyError, TypeError, ValueError, AttributeError):
            return False
        if (not isinstance(auth, dict) or set(auth) != {'consentEpochId', 'noticeVersion', 'stateVersion'}
                or not isinstance(auth['noticeVersion'], str) or not 1 <= len(auth['noticeVersion']) <= 64
                or integer(auth['stateVersion']) is None or auth['stateVersion'] < 1):
            return False
    return True


class LegacyReplay:
    def __init__(self, *, resource, settings, abuse_table_name, now):
        self.resource, self.settings, self.now = resource, settings, now
        if not abuse_table_name or not settings.users_table_name or not settings.deletion_ledger_table_name or not settings.device_bindings_table_name:
            blocked('SERVER_UNAVAILABLE')
        self.abuse = resource.Table(abuse_table_name)
        self.users = resource.Table(settings.users_table_name)
        self.ledger = resource.Table(settings.deletion_ledger_table_name)
        self.devices = resource.Table(settings.device_bindings_table_name)

    @staticmethod
    def get(table, key):
        return table.get_item(Key=key, ConsistentRead=True).get('Item')

    def authority(self, event, account):
        assert_authoritative_account_active(account, self.users, self.ledger)
        fingerprint = device_binding_fingerprint(event)
        pointer = self.get(self.devices, {'PK': 'USER#' + account, 'SK': 'ACTIVE_BINDING'})
        if (not isinstance(pointer, dict) or pointer.get('recordType') != 'ACTIVE_BINDING_POINTER'
                or integer(pointer.get('stateVersion')) is None or pointer['stateVersion'] < 1
                or not isinstance(pointer.get('bindingFingerprint'), str)
                or pointer['bindingFingerprint'] == 'NONE'
                or not hmac.compare_digest(pointer['bindingFingerprint'], fingerprint)):
            blocked('DEVICE_BINDING_MISMATCH')
        binding = self.get(self.devices, {'PK': 'USER#' + account, 'SK': 'DEVICE#' + fingerprint})
        if (not isinstance(binding, dict) or binding.get('accountId') != account or binding.get('status') != 'ACTIVE'
                or not isinstance(binding.get('bindingFingerprint'), str)
                or not hmac.compare_digest(binding['bindingFingerprint'], fingerprint)):
            blocked('DEVICE_BINDING_MISMATCH')
        return pointer, binding

    def replay(self, event, account, payload):
        device_proof = self.authority(event, account)
        now = self.now()
        account_hash, request_id = digest(account), payload['requestId']
        key = {'PK': 'ANALYSIS#REQUEST#' + account_hash, 'SK': request_id}
        row = self.get(self.abuse, key)
        if row is None:
            blocked('LEGACY_MIGRATION_REQUIRED')
        if row.get('payloadHash') != payload_digest(payload):
            blocked('IDEMPOTENCY_CONFLICT')
        if row.get('status') == 'COMPLETED_ERASED':
            blocked('RESULT_UNAVAILABLE')
        if row.get('status') != 'COMPLETED' or not completed_shape(row):
            blocked()
        expiry, completed = integer(row.get('expiresAt')), _epoch(row.get('completedAt'))
        if (expiry is None or completed is None or not completed <= now < expiry
                or expiry > completed + 900 or integer(row.get('ttl')) != expiry
                or row.get('PK') != key['PK'] or row.get('SK') != request_id):
            blocked('RESULT_UNAVAILABLE')
        consumed_key = {'PK': 'ANALYSIS#CONSUMPTION#' + account_hash, 'SK': request_id}
        consumed = self.get(self.abuse, consumed_key)
        if (not consumed or set(consumed) != {'PK', 'SK', 'accountIdHash', 'consumptionType', 'createdAt', 'expiresAt', 'ttl'}
                or consumed.get('PK') != consumed_key['PK'] or consumed.get('SK') != request_id
                or consumed.get('accountIdHash') != account_hash or consumed.get('consumptionType') not in {'monthly', 'credit'}
                or consumed.get('createdAt') != row.get('completedAt')
                or integer(consumed.get('expiresAt')) != expiry or integer(consumed.get('ttl')) != expiry):
            blocked()
        response = row.get('response')
        if not isinstance(response, dict) or response.get('requestId') != request_id:
            blocked()
        # Reuse the strict bounded public assessment projection, never echo an
        # arbitrary stored map. No caller-supplied text enters this response.
        assessment = _json_compatible(_assessment(response, self.settings))
        public = {'requestId': request_id, **assessment}
        history_reads = self._history(account, request_id, row, public, now)
        # Strong before/after reads catch intervening cleanup and binding change.
        # They do not create a transaction-wide snapshot or promise revocation of
        # a response already released immediately before a later deletion.
        if self.get(self.abuse, key) != row or self.get(self.abuse, consumed_key) != consumed:
            blocked()
        for table, history_key, observed in history_reads:
            if self.get(table, history_key) != observed:
                blocked('RESULT_UNAVAILABLE')
        if self.authority(event, account) != device_proof:
            blocked('DEVICE_BINDING_MISMATCH')
        if self.now() >= expiry:
            blocked('RESULT_UNAVAILABLE')
        return public

    def _history(self, account, request_id, row, response, now):
        control_name, content_name = self.settings.control_table_name, self.settings.content_table_name
        if not control_name and not content_name:
            if row.get('historyAuthorization') is not None:
                blocked()
            return []
        if not control_name or not content_name:
            blocked('SERVER_UNAVAILABLE')
        control, content = self.resource.Table(control_name), self.resource.Table(content_name)
        state_key = {'PK': 'USER#' + account, 'SK': 'STATE'}
        locator_key = {'PK': 'USER#' + account, 'SK': 'REQUEST#' + request_id}
        state, locator = self.get(control, state_key), self.get(control, locator_key)
        # Configured History may contain erased/suppressed legacy copies even if
        # activation flags are now false. Never bypass these reads using flags.
        if not state or not locator:
            blocked('RESULT_UNAVAILABLE')
        generation = integer(state.get('historyGeneration'))
        if (state.get('accountStatus') != 'ACTIVE' or generation is None or generation < 0
                or locator.get('status') != 'ACTIVE' or locator.get('recordType') != 'REQUEST'
                or integer(locator.get('schemaVersion')) != 1 or locator.get('requestId') != request_id
                or locator.get('payloadHash') != row['payloadHash'] or integer(locator.get('historyGeneration')) != generation
                or integer(locator.get('contentExpiresAt')) is None or locator['contentExpiresAt'] <= now
                or integer(locator.get('expiresAt')) is None or locator['expiresAt'] <= now):
            blocked('RESULT_UNAVAILABLE')
        completed_ms = integer(locator.get('completedAtEpochMs'))
        expected_sk = f'COMPLETE#{completed_ms:013d}#{request_id}' if completed_ms is not None else None
        if expected_sk is None or locator.get('contentSortKey') != expected_sk:
            blocked()
        content_key = {'PK': f'USER#{account}#HISTORY#{generation}', 'SK': expected_sk}
        item = self.get(content, content_key)
        if (not item or item.get('PK') != content_key['PK'] or item.get('SK') != expected_sk
                or item.get('requestId') != request_id or integer(item.get('historyGeneration')) != generation
                or integer(item.get('schemaVersion')) != 1 or integer(item.get('recordVersion')) != 1
                or integer(item.get('completedAtEpochMs')) != completed_ms
                or integer(item.get('expiresAt')) != integer(locator['contentExpiresAt'])
                or response_from_history_item(item, self.settings) != response):
            blocked('RESULT_UNAVAILABLE')
        return [(control, state_key, state), (control, locator_key, locator), (content, content_key, item)]
