"""Private Dev assessment; never an entitlement or consumer API implementation."""
import re
import time
from urllib.parse import urlsplit

from url_redirect_resolver.resolver import ResolutionStop, parse_target

MAX_LOOKUPS = 6
WARNINGS = {'UNENCRYPTED_CONNECTION', 'HTTPS_TO_HTTP_REDIRECT'}


class Unavailable(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class Budget:
    def __init__(self, seconds, clock=time.monotonic):
        self.clock = clock
        self.end = clock() + max(0, min(seconds, 32))

    def remaining(self, ceiling=32):
        value = self.end - self.clock()
        if value <= 0:
            raise Unavailable('TIME_BUDGET_EXCEEDED')
        return min(value, ceiling)


def base_result(check_id=None):
    return {'schemaVersion': 1, 'checkId': check_id, 'verdict': 'unknown',
            'processingOutcome': 'invalid_input', 'coverage': 'not_assessed',
            'reasonCodes': ['INVALID_REQUEST'], 'transportWarnings': [],
            'threatTypes': [], 'lookupCount': 0, 'providerCallCount': 0, 'observedHopCount': 0,
            'scope': 'HTTP_REDIRECTS_AND_GOOGLE_LOOKUP', 'consumerAccessEnabled': False}


def validate_request(event):
    if (not isinstance(event, dict) or set(event) != {'schemaVersion', 'checkId', 'url', 'scope'}
            or type(event.get('schemaVersion')) is not int or event['schemaVersion'] != 1
            or not isinstance(event.get('checkId'), str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', event['checkId'])
            or event.get('scope') not in ('full_url', 'origin_only')
            or not isinstance(event.get('url'), str) or '#' in event['url']):
        raise Unavailable('INVALID_REQUEST')
    target = parse_target(event['url'])
    if event['scope'] == 'origin_only':
        parsed = urlsplit(target.url)
        if parsed.path != '/' or parsed.query or '?' in target.url:
            raise Unavailable('INVALID_REQUEST')
    return target


def validate_resolution(value, check_id, initial):
    required = {'schemaVersion', 'checkId', 'resolutionStatus', 'hops', 'lastObservedUrl',
                'httpChainComplete', 'warnings', 'reasonCodes', 'requestCount', 'scope'}
    if (not isinstance(value, dict) or set(value) != required
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['checkId'] != check_id or value['scope'] != 'HTTP_REDIRECTS_ONLY'
            or value['resolutionStatus'] not in ('http_chain_complete', 'partial', 'blocked', 'invalid_input')
            or type(value['httpChainComplete']) is not bool
            or type(value['requestCount']) is not int or not 0 <= value['requestCount'] <= 6
            or not isinstance(value['hops'], list) or len(value['hops']) > 6
            or not isinstance(value['warnings'], list) or len(value['warnings']) > 2
            or any(not isinstance(x, str) or x not in WARNINGS for x in value['warnings'])
            or not isinstance(value['reasonCodes'], list) or not 1 <= len(value['reasonCodes']) <= 8
            or any(not isinstance(x, str) or not re.fullmatch(r'[A-Z_]{1,80}', x) for x in value['reasonCodes'])):
        raise Unavailable('RESOLVER_RESPONSE_INVALID')
    hops = value['hops']
    if value['requestCount'] < len(hops):
        raise Unavailable('RESOLVER_RESPONSE_INVALID')
    for i, hop in enumerate(hops):
        if (not isinstance(hop, dict) or set(hop) != {'url', 'httpStatus'}
                or type(hop['httpStatus']) is not int or not 100 <= hop['httpStatus'] <= 599):
            raise Unavailable('RESOLVER_RESPONSE_INVALID')
        try:
            if parse_target(hop['url']).url != hop['url'] or '#' in hop['url']:
                raise Unavailable('RESOLVER_RESPONSE_INVALID')
        except (ResolutionStop, TypeError):
            raise Unavailable('RESOLVER_RESPONSE_INVALID') from None
        if i == 0 and hop['url'] != initial:
            raise Unavailable('RESOLVER_RESPONSE_INVALID')
        if i < len(hops) - 1 and hop['httpStatus'] not in (301, 302, 303, 307, 308):
            raise Unavailable('RESOLVER_RESPONSE_INVALID')
    if len({hop['url'] for hop in hops}) != len(hops):
        raise Unavailable('RESOLVER_RESPONSE_INVALID')
    complete = value['resolutionStatus'] == 'http_chain_complete'
    if (complete != value['httpChainComplete']
            or value['lastObservedUrl'] != (hops[-1]['url'] if hops else None)
            or complete and (not hops or value['requestCount'] != len(hops) or not 200 <= hops[-1]['httpStatus'] < 300)):
        raise Unavailable('RESOLVER_RESPONSE_INVALID')
    return value


def assess(event, dependencies, budget):
    result = base_result()
    try:
        target = validate_request(event)
    except ResolutionStop as stopped:
        result.update(processingOutcome=stopped.status, reasonCodes=[stopped.reason])
        return result
    except (Unavailable, TypeError, ValueError):
        return result
    result['checkId'] = event['checkId']
    result['processingOutcome'] = 'unavailable'
    result['reasonCodes'] = ['RESOLVER_UNAVAILABLE']
    try:
        budget.remaining()
        resolution = validate_resolution(dependencies.resolve(event['checkId'], target.url, budget), event['checkId'], target.url)
        result['observedHopCount'] = len(resolution['hops'])
        result['transportWarnings'] = sorted(set(resolution['warnings']))
        # Caller/origin metadata is not permission to submit a sensitive destination.
        urls = list(dict.fromkeys(hop['url'] for hop in resolution['hops']))
        if not urls:
            result.update(processingOutcome=resolution['resolutionStatus'] if resolution['resolutionStatus'] in ('blocked', 'invalid_input') else 'unavailable', reasonCodes=['NO_ELIGIBLE_OBSERVATION'])
            return result
        result.update(processingOutcome='partial', coverage='limited', reasonCodes=['HTTP_CHAIN_INCOMPLETE'])
        for url in urls:
            budget.remaining()
            result['lookupCount'] += 1  # attempted provider checks, never a charge count
            threats = dependencies.lookup(url, budget)
            if threats:
                result.update(verdict='high_risk', threatTypes=sorted(set(result['threatTypes']) | set(threats)), reasonCodes=['KNOWN_THREAT_MATCH'])
                # Match is sufficient for avoidance. Don't contact more providers.
                return result
        if resolution['httpChainComplete'] and event['scope'] == 'full_url':
            result.update(verdict='no_known_threat_detected', processingOutcome='complete',
                          coverage='supported_checks_complete', reasonCodes=['NO_LIST_MATCH', 'BROWSER_NAVIGATION_NOT_EVALUATED'])
        elif event['scope'] == 'origin_only':
            result['reasonCodes'] = ['ORIGIN_ONLY_NOT_FULL_LINK']
    except Unavailable as error:
        result['reasonCodes'] = [error.reason]
    except Exception:
        # Never serialize SDK, URL, TLS, HTTP, or secret-bearing exception text.
        result['reasonCodes'] = ['INTERNAL_ERROR']
    finally:
        result['providerCallCount'] = dependencies.provider_call_count
    return result
