"""One closed internal evaluator envelope, not vendor dispatch, usage or price.

All facts stay inside the owning receipt's existing seven-day projection.
No content, account, execution token or new lookup key belongs in these events.
"""
import hashlib
import re
import secrets

from .receipts import _int, _metadata, _fail, receipt_events

_COMPONENTS = {'url_check': 'url_assessment', 'message_check': 'message_evaluator',
               'recovery_clarification': 'recovery_evaluator'}
_BUCKETS = {'lt_250ms', '250_999ms', '1_4s', '5_14s', '15s_plus'}


def _base(metadata):
    meta = _metadata(metadata)
    accepted = receipt_events({'recordType': 'V1_CHECK_RECEIPT', 'state': 'ADMITTED',
        'basis': meta['accessClass'], 'operationsReceipt': meta}, now_epoch=meta['acceptedAtEpoch'])['events'][0]
    return {**accepted, 'lifecycleRevision': None, 'accountingState': None,
            'chargedChecks': None, 'accessClass': None, 'attemptKind': 'internal_component',
            'component': _COMPONENTS[meta['service']], 'attemptOrdinal': 1}


def _finish_id(attempt_ref):
    return 'op1_' + hashlib.sha256(('operations-internal-finish:' + attempt_ref).encode('ascii')).hexdigest()[:32]


def duration_bucket(duration_ms):
    if type(duration_ms) is not int or not 0 <= duration_ms <= 10**13:
        _fail()
    if duration_ms < 250: return 'lt_250ms'
    if duration_ms < 1000: return '250_999ms'
    if duration_ms < 5000: return '1_4s'
    if duration_ms < 15000: return '5_14s'
    return '15s_plus'


def validate_attempt_events(metadata, events, *, terminal_time=None):
    """Validate stored shape/context only; actual authority is the caller's CAS."""
    meta = _metadata(metadata)
    if type(events) is not list or len(events) > 2:
        _fail()
    if not events: return []
    try:
        start = events[0]
        ref, event_id = start['attemptRef'], start['eventId']
        epoch = _int(start['eventTimeEpoch'])
        if (type(start) is not dict or type(ref) is not str or not re.fullmatch(r'at1_[0-9a-f]{32}', ref)
                or type(event_id) is not str or not re.fullmatch(r'op1_[0-9a-f]{32}', event_id)
                or _int(start.get('attemptOrdinal')) != 1
                or _finish_id(ref) in (meta['acceptedEventId'], meta['terminalEventId'])
                or event_id in (meta['acceptedEventId'], meta['terminalEventId'], _finish_id(ref))
                or not meta['acceptedAtEpoch'] <= epoch < meta['expiresAtEpoch']
                or terminal_time is not None and epoch > terminal_time):
            _fail()
        expected = {**_base(meta), 'eventType': 'attempt_started', 'eventId': event_id,
                    'eventTimeEpoch': epoch, 'attemptRef': ref}
        if start != expected: _fail()
        result = [expected]
        if len(events) == 2:
            finish = events[1]
            end = _int(finish['eventTimeEpoch'])
            outcome, bucket = finish['attemptOutcome'], finish['durationBucket']
            if (type(outcome) is not str or outcome not in ('succeeded', 'failed')
                    or _int(finish.get('attemptOrdinal')) != 1 or type(bucket) is not str or bucket not in _BUCKETS
                    or not epoch <= end < meta['expiresAtEpoch']):
                _fail()
            expected_finish = {**expected, 'eventType': 'attempt_finished', 'eventId': _finish_id(ref),
                'eventTimeEpoch': end, 'attemptOutcome': outcome, 'durationBucket': bucket,
                'reasonCode': 'NONE' if outcome == 'succeeded' else 'SERVICE_UNAVAILABLE'}
            if finish != expected_finish: _fail()
            result.append(expected_finish)
        return result
    except (TypeError, KeyError, ValueError):
        _fail()


def start_internal_attempt(metadata, *, observed_at, random_hex=lambda: secrets.token_hex(16)):
    meta = _metadata(metadata)
    event = {**_base(meta), 'eventType': 'attempt_started', 'eventId': 'op1_' + random_hex(),
             'eventTimeEpoch': _int(observed_at), 'attemptRef': 'at1_' + random_hex()}
    return validate_attempt_events(meta, [event])[0]


def finish_internal_attempt(metadata, start, *, observed_at, outcome, duration_ms):
    first = validate_attempt_events(metadata, [start])[0]
    value = {**first, 'eventType': 'attempt_finished', 'eventId': _finish_id(first['attemptRef']),
             'eventTimeEpoch': _int(observed_at), 'attemptOutcome': outcome,
             'durationBucket': duration_bucket(duration_ms),
             'reasonCode': 'NONE' if outcome == 'succeeded' else 'SERVICE_UNAVAILABLE'}
    return validate_attempt_events(metadata, [first, value])[1]
