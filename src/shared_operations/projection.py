"""Strict receipt-local captured facts, never a caller authentication mechanism."""
from copy import deepcopy

from .receipts import _metadata, _int, _fail, _REASONS, receipt_events
from .attempts import validate_attempt_events


def capture_projection(row, *, now_epoch):
    facts = receipt_events(row, now_epoch=now_epoch)
    if facts['coverage'] in ('NOT_RECORDED', 'EXPIRED'):
        _fail()
    prior = validate_capture(row['operationsProjection']) if 'operationsProjection' in row else None
    attempts = [] if prior is None else [event for event in prior['events'] if event['eventType'].startswith('attempt_')]
    if prior is not None and prior['reference'] != _metadata(row.get('operationsReceipt')):
        _fail()
    attempts = validate_attempt_events(_metadata(row.get('operationsReceipt')), attempts,
        terminal_time=_int(row['assessmentEpoch']) if row.get('state') == 'SETTLED' else None)
    if any(event['eventTimeEpoch'] > _int(now_epoch) for event in attempts):
        _fail()
    return validate_capture({'schemaVersion': 2 if attempts else 1, 'sourceState': row.get('state'),
        'reference': _metadata(row.get('operationsReceipt')), **facts, 'events': facts['events'] + attempts})


def validate_capture(value):
    """Validate one captured snapshot, not caller-owned mutable references."""
    try:
        value = deepcopy(value)
    except Exception:
        _fail()
    if type(value) is not dict or set(value) != {'schemaVersion', 'sourceState', 'reference', 'coverage', 'events'}:
        _fail()
    version = _int(value['schemaVersion'])
    if version not in (1, 2) or value['sourceState'] not in ('ADMITTED', 'SETTLED'):
        _fail()
    meta = _metadata(value['reference'])
    events = value['events']
    if type(events) is not list or not 1 <= len(events) <= 4 or any(type(e) is not dict for e in events):
        _fail()
    count = 2 if len(events) >= 2 and events[1].get('eventType') == 'check_terminal' else 1
    attempts = events[count:]
    if (version == 1 and attempts or version == 2 and not attempts):
        _fail()
    events = events[:count]
    accepted = receipt_events({'recordType': 'V1_CHECK_RECEIPT', 'state': 'ADMITTED',
        'basis': meta['accessClass'], 'operationsReceipt': meta}, now_epoch=meta['acceptedAtEpoch'])['events'][0]
    if (_int(events[0].get('lifecycleRevision')) != 1
            or _int(events[0].get('eventTimeEpoch')) != meta['acceptedAtEpoch'] or events[0] != accepted):
        _fail()
    coverage = value['coverage']
    if value['sourceState'] == 'ADMITTED':
        if coverage != 'ACCEPTED_ONLY' or len(events) != 1:
            _fail()
    elif len(events) == 1:
        if coverage != 'TERMINAL_REASON_NOT_RECORDED':
            _fail()
    else:
        event = events[1]
        try:
            outcome, reason = event.get('processingOutcome'), event.get('reasonCode')
            charge, assessed = _int(event.get('chargedChecks')), _int(event.get('eventTimeEpoch'))
            if (coverage != 'CHECK_LIFECYCLE_RECORDED' or outcome not in _REASONS
                    or reason not in _REASONS[outcome] or _int(event.get('lifecycleRevision')) != 2
                    or charge != int(outcome == 'complete' and meta['accessClass'] != 'complimentary')
                    or not meta['acceptedAtEpoch'] <= assessed < meta['expiresAtEpoch']):
                _fail()
            terminal = {**accepted, 'eventType': 'check_terminal', 'eventId': meta['terminalEventId'],
                'eventTimeEpoch': assessed, 'lifecycleRevision': 2, 'processingOutcome': outcome,
                'accountingState': 'charged' if charge else 'not_charged', 'chargedChecks': charge,
                'reasonCode': reason}
            if event != terminal:
                _fail()
        except (TypeError, KeyError):
            _fail()
    normalized_attempts = validate_attempt_events(meta, attempts,
        terminal_time=_int(events[1]['eventTimeEpoch']) if len(events) == 2 else None)
    return {'schemaVersion': version, 'sourceState': value['sourceState'], 'reference': meta,
            'coverage': coverage, 'events': events + normalized_attempts}
