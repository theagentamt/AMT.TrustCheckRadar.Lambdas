"""Bounded deterministic daily reduction; never dispatches, sends or changes allowances."""
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .contract import canonical, require, validate

SERVICES = ('message_check', 'url_check', 'recovery_clarification')
OUTCOMES = ('complete', 'partial', 'inconclusive', 'failed', 'blocked',
            'invalid_input', 'unsupported', 'unavailable')
MAX_FACTS = 10000


def day_bounds(day):
    require(type(day) is date, 'OPERATIONS_PERIOD_INVALID')
    zone = ZoneInfo('America/Chicago')
    try:
        start = datetime.combine(day, time.min, zone)
        end = datetime.combine(day + timedelta(days=1), time.min, zone)
        return int(start.timestamp()), int(end.timestamp())
    except (ValueError, OverflowError, OSError):
        from .contract import OperationsError
        raise OperationsError('OPERATIONS_PERIOD_INVALID') from None


def _service():
    return {'received': 0, **{k: 0 for k in OUTCOMES}, 'pending': 0,
            'deductedChecks': 0, 'noDeductionChecks': 0, 'settlementUnknownChecks': 0,
            'complimentaryCompletedChecks': 0}


def _attempts():
    return {'started': 0, 'succeeded': 0, 'failed': 0, 'denied': 0, 'unresolved': 0,
            'costKnownProviderAttempts': 0, 'costUnknownProviderAttempts': 0,
            'knownEstimatedCostMicrousd': 0, 'knownProviderRequestUnits': 0,
            'requestUnitsUnknownProviderAttempts': 0}


def reduce_day(events, rejections, *, day, as_of, environment, source_complete, provider_coverage=None):
    """Coverage is supplied by a trusted bounded source reader, never inferred from silence.

    Input order and exact replays cannot change the result. Conflicts are excluded
    and counted; no arrival-order winner is invented. Reports retain no references.
    """
    start, end = day_bounds(day)
    require(type(as_of) is int and as_of >= start and environment in ('dev', 'uat', 'prod')
            and type(source_complete) is bool, 'OPERATIONS_PERIOD_INVALID')
    require(type(events) is list and type(rejections) is list
            and len(events) + len(rejections) <= MAX_FACTS, 'OPERATIONS_BATCH_BOUND')
    coverage = {'openai': 'unavailable', 'google_web_risk': 'unavailable'} if provider_coverage is None else provider_coverage
    require(type(coverage) is dict and set(coverage) == {'openai','google_web_risk'}
            and all(v in ('complete','unavailable') for v in coverage.values()), 'OPERATIONS_COVERAGE_INVALID')
    faults = defaultdict(int)
    facts, poisoned = {}, set()
    for raw in events:
        event = validate(raw)
        require(event['environment'] == environment, 'OPERATIONS_ENVIRONMENT_MISMATCH')
        if event['eventTimeEpoch'] > as_of:
            continue
        key = event['eventId']
        if key in facts and canonical(facts[key]) != canonical(event):
            poisoned.add(key)
        else:
            facts[key] = event
    faults['conflictingEventIdentity'] = len(poisoned)
    checks, attempts = defaultdict(dict), defaultdict(dict)
    conflicts = set()
    for key, event in facts.items():
        if key in poisoned:
            continue
        attempt = event['eventType'].startswith('attempt_')
        group = attempts[event['attemptRef']] if attempt else checks[event['logicalCheckRef']]
        phase = event['eventType']
        if phase in group and canonical(group[phase]) != canonical(event):
            conflicts.add(('attempt' if attempt else 'check', event['attemptRef'] if attempt else event['logicalCheckRef']))
        else:
            group[phase] = event
    faults['conflictingLifecycle'] = len(conflicts)
    totals = {name: _service() for name in SERVICES}
    for reference, group in checks.items():
        if ('check', reference) in conflicts:
            continue
        accepted, terminal = group.get('check_accepted'), group.get('check_terminal')
        if accepted is None:
            faults['terminalWithoutAcceptance'] += 1
            continue
        if terminal and (any(terminal[k] != accepted[k] for k in ('environment', 'service', 'inputKind', 'accessClass'))
                         or not accepted['eventTimeEpoch'] <= terminal['eventTimeEpoch'] < accepted['eventTimeEpoch'] + 7*86400):
            faults['incompatibleTerminal'] += 1
            terminal = None
        if not start <= accepted['eventTimeEpoch'] < end:
            continue
        row = totals[accepted['service']]
        row['received'] += 1
        if terminal is None:
            row['pending'] += 1
            row['settlementUnknownChecks'] += 1
        else:
            row[terminal['processingOutcome']] += 1
            row['deductedChecks' if terminal['chargedChecks'] else 'noDeductionChecks'] += 1
            row['complimentaryCompletedChecks'] += int(terminal['processingOutcome'] == 'complete'
                                                       and terminal['accessClass'] == 'complimentary')
    provider = {name: _attempts() for name in ('openai', 'google_web_risk')}
    internal = _attempts()
    for reference, group in attempts.items():
        if ('attempt', reference) in conflicts:
            continue
        begun, finished = group.get('attempt_started'), group.get('attempt_finished')
        if begun is None:
            faults['finishWithoutStart'] += 1
            continue
        accepted = checks.get(begun['logicalCheckRef'], {}).get('check_accepted')
        # An embedded URL attempt belongs to the original message, not a new check.
        linked = accepted and (begun['service'] == accepted['service'] and begun['inputKind'] == accepted['inputKind']
                               or accepted['service'] == 'message_check' and begun['service'] == 'url_check'
                               and begun['inputKind'] == 'message_url')
        if not linked or ('check', begun['logicalCheckRef']) in conflicts or not (
                accepted['eventTimeEpoch'] <= begun['eventTimeEpoch'] < accepted['eventTimeEpoch'] + 7*86400):
            faults['attemptWithoutAcceptance'] += 1
            continue
        immutable = ('logicalCheckRef', 'environment', 'service', 'inputKind', 'attemptKind',
                     'component', 'attemptOrdinal', 'providerFamily')
        if finished and (any(finished[k] != begun[k] for k in immutable)
                         or not begun['eventTimeEpoch'] <= finished['eventTimeEpoch'] < accepted['eventTimeEpoch'] + 7*86400):
            faults['incompatibleAttemptFinish'] += 1
            finished = None
        if not start <= begun['eventTimeEpoch'] < end:
            continue
        row = provider[begun['providerFamily']] if begun['attemptKind'] == 'provider_call' else internal
        row['started'] += 1
        row[finished['attemptOutcome'] if finished else 'unresolved'] += 1
        if begun['attemptKind'] == 'provider_call':
            cost = finished['estimatedCostMicrousd'] if finished else None
            units = finished['providerRequestUnits'] if finished else None
            row['costUnknownProviderAttempts' if cost is None else 'costKnownProviderAttempts'] += 1
            row['knownEstimatedCostMicrousd'] += cost or 0
            row['requestUnitsUnknownProviderAttempts'] += int(units is None)
            row['knownProviderRequestUnits'] += units or 0
    snapshots = {}
    for raw in rejections:
        event = validate(raw, rejection=True)
        require(event['environment'] == environment, 'OPERATIONS_ENVIRONMENT_MISMATCH')
        if event['windowStartEpoch'] > as_of:
            continue
        key = (event['service'], event['reasonCode'], event['windowStartEpoch'])
        snapshots[key] = max(snapshots.get(key, 0), event['rejectedCount'])
    rejected = {name: {} for name in SERVICES}
    for (service, reason, epoch), count in snapshots.items():
        if start <= epoch < end:
            rejected[service][reason] = rejected[service].get(reason, 0) + count
    for row in totals.values():
        row['outcomeFractions'] = {name: {'numerator': row[name], 'denominator': row['received'],
            'percentage': round(100*row[name]/row['received'], 2) if row['received'] else 'N/A'}
            for name in (*OUTCOMES, 'pending')}
    return {'schemaVersion': '1.0.0-operations-report-candidate.1', 'environment': environment,
            'localDate': day.isoformat(), 'timezone': 'America/Chicago', 'rangeStartEpoch': start,
            'rangeEndEpoch': end, 'asOfEpoch': as_of,
            'periodClosed': as_of >= end,
            'lifecycleSourceComplete': source_complete and not any(faults.values()),
            'sourceComplete': as_of >= end and source_complete and not any(faults.values()) and all(v=='complete' for v in coverage.values()),
            'providerTelemetryCoverage': dict(coverage),
            'localIntakeCoverage': 'unavailable', 'services': totals, 'providerAttempts': provider,
            'internalAttempts': internal, 'preAdmissionRejections': rejected,
            'reconciliationFaults': dict(sorted((k, v) for k, v in faults.items() if v)),
            'costBasis': 'known_estimates_only_not_invoice_total'}
