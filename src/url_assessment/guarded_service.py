"""Reputation-gated redirect traversal. Target network work remains isolated."""
import time
from assessment_service import Budget, Unavailable
from shared_lookup_freshness import evidence, epoch, current, wall_clock
from shared_url_guarded import SCOPE, supported_step
from url_redirect_resolver.resolver import (ResolutionStop, parse_target, join_location,
    has_control_or_format, MAX_REDIRECTS)


def base_result(check_id=None):
    return {'schemaVersion': 3, 'checkId': check_id, 'verdict': 'unknown',
            'processingOutcome': 'invalid_input', 'coverage': 'not_assessed',
            'reasonCodes': ['INVALID_REQUEST'], 'transportWarnings': [], 'threatTypes': [],
            'lookupCount': 0, 'providerCallCount': 0, 'observedHopCount': 0,
            'scope': SCOPE, 'consumerAccessEnabled': False, 'lookupObservedAt': None,
            'lookupValidUntil': None, 'matchScope': None, 'targetContactStatus': 'not_contacted'}


class TargetBudget:
    """Aggregate DNS + isolated target-RPC time, never reset on redirects."""
    def __init__(self, outer, clock=time.monotonic):
        self.outer, self.clock, self.left = outer, clock, 10.0

    def call(self, operation):
        seconds = min(self.left, self.outer.remaining(10))
        if seconds < .1:
            raise Unavailable('TIME_BUDGET_EXCEEDED')
        started = self.clock()
        try:
            return operation(Budget(seconds, self.clock))
        finally:
            self.left -= max(0, self.clock() - started)


def assess(event, dependencies, budget, *, now=wall_clock, clock=time.monotonic):
    result = base_result()
    try:
        # Reuse the exact request/projection validation, with an explicit new protocol.
        from assessment_service import validate_request, validate_request_envelope
        if type(event) is not dict or type(event.get('schemaVersion')) is not int or event['schemaVersion'] != 2:
            return result
        legacy_envelope = event | {'schemaVersion': 1}
        validate_request_envelope(legacy_envelope)
        # Operation identity is trusted after envelope validation, before any
        # URL policy rejection. Rejections must settle this exact reservation.
        result['checkId'] = event['checkId']
        target = validate_request(legacy_envelope)
    except ResolutionStop as stopped:
        result.update(processingOutcome=stopped.status, reasonCodes=[stopped.reason])
        return result
    except (Unavailable, TypeError, ValueError):
        return result
    result['checkId'] = event['checkId']
    result.update(processingOutcome='unavailable', reasonCodes=['HTTP_CHAIN_INCOMPLETE'])
    target_budget = TargetBudget(budget, clock)
    visited, previous_scheme = set(), None
    try:
        for index in range(MAX_REDIRECTS + 1):
            budget.remaining()
            if target.url in visited:
                break
            visited.add(target.url)
            # Check all public addresses before sending a candidate to Google.
            # The worker repeats DNS validation and pins its own connection.
            target_budget.call(lambda b: dependencies.validate_destination(target, b))
            result['lookupCount'] += 1
            observation = evidence(dependencies.lookup(target.url, budget))
            if result['lookupObservedAt'] is None or epoch(observation['observedAt']) < epoch(result['lookupObservedAt']):
                result['lookupObservedAt'] = observation['observedAt']
            if observation['threatTypes']:
                if not current(observation['observedAt'], observation['validUntil'], now()):
                    raise Unavailable('PROVIDER_RESPONSE_INVALID')
                result.update(verdict='high_risk', processingOutcome='partial', coverage='limited',
                              reasonCodes=['KNOWN_THREAT_MATCH'], threatTypes=sorted(observation['threatTypes']),
                              lookupObservedAt=observation['observedAt'], lookupValidUntil=observation['validUntil'],
                              matchScope=('origin_only' if event['scope'] == 'origin_only' else
                                          'redirect_destination' if index else 'full_submitted_url'))
                return result
            if event['scope'] == 'origin_only':
                result.update(processingOutcome='partial', coverage='limited', reasonCodes=['ORIGIN_ONLY_NOT_FULL_LINK'])
                return result
            # Once dispatched, lack of a valid response cannot prove no contact.
            result['targetContactStatus'] = 'unknown'
            step = target_budget.call(lambda b: dependencies.step(event['checkId'], target.url, b))
            if not supported_step(step, event['checkId'], target.url):
                raise Unavailable('RESOLVER_RESPONSE_INVALID')
            if not step['hops']:
                if step['requestCount'] == 0:
                    result['targetContactStatus'] = 'contacted' if result['observedHopCount'] else 'not_contacted'
                raise Unavailable('HTTP_CHAIN_INCOMPLETE')
            result['observedHopCount'] += 1
            result.update(targetContactStatus='contacted', processingOutcome='partial', coverage='limited')
            warnings = result['transportWarnings']
            if target.scheme == 'http' and 'UNENCRYPTED_CONNECTION' not in warnings:
                warnings.append('UNENCRYPTED_CONNECTION')
            if previous_scheme == 'https' and target.scheme == 'http' and 'HTTPS_TO_HTTP_REDIRECT' not in warnings:
                warnings.append('HTTPS_TO_HTTP_REDIRECT')
            status = step['hops'][0]['httpStatus']
            if status not in (301, 302, 303, 307, 308):
                if 200 <= status < 300:
                    result.update(verdict='no_known_threat_detected', processingOutcome='complete',
                                  coverage='supported_checks_complete',
                                  reasonCodes=['NO_LIST_MATCH', 'BROWSER_NAVIGATION_NOT_EVALUATED'])
                return result
            location = step['location']
            if (index == MAX_REDIRECTS or not location or has_control_or_format(location)
                    or any(ord(c) <= 32 or ord(c) == 127 for c in location) or '\\' in location):
                return result
            previous_scheme = target.scheme
            target = parse_target(join_location(target.url, location))
    except ResolutionStop as error:
        # Parse/public-IP failures are finite; network detail never leaves the private boundary.
        from shared_url_guarded import PARSE_REASONS
        result.update(reasonCodes=[error.reason if error.reason in PARSE_REASONS else 'HTTP_CHAIN_INCOMPLETE'])
        if result['observedHopCount'] == 0 and result['lookupCount'] == 0:
            result['processingOutcome'] = error.status
    except Unavailable as error:
        from shared_url_guarded import REASONS
        result['reasonCodes'] = [error.reason if error.reason in REASONS else 'INTERNAL_ERROR']
    except Exception:
        result['reasonCodes'] = ['INTERNAL_ERROR']
    finally:
        result['providerCallCount'] = dependencies.provider_call_count
    return result
