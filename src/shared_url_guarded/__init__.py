"""Pure validation for the versioned guarded URL profile; no AWS imports."""
import re
from shared_lookup_freshness import validate as validate_times

TRANSPORT = '1.0.0-candidate.3'
CONTRACT = '0.4.0-candidate.1'
SCOPE = 'GUARDED_HTTP_AND_GOOGLE_LOOKUP'
KEYS = {'schemaVersion', 'checkId', 'verdict', 'processingOutcome', 'coverage',
        'reasonCodes', 'transportWarnings', 'threatTypes', 'lookupCount',
        'providerCallCount', 'observedHopCount', 'scope', 'consumerAccessEnabled',
        'lookupObservedAt', 'lookupValidUntil', 'matchScope', 'targetContactStatus'}
THREATS = {'MALWARE', 'SOCIAL_ENGINEERING', 'UNWANTED_SOFTWARE'}
PARSE_REASONS = {'INVALID_REQUEST', 'INVALID_URL', 'AMBIGUOUS_URL', 'INVALID_URL_ENCODING',
                 'INVALID_AUTHORITY', 'AMBIGUOUS_AUTHORITY', 'INVALID_HOST',
                 'AMBIGUOUS_ADDRESS', 'EXCESSIVE_URL_ENCODING', 'UNSUPPORTED_SCHEME',
                 'UNSUPPORTED_PORT', 'UNSUPPORTED_ADDRESS_ENCODING',
                 'NON_PUBLIC_DESTINATION', 'SENSITIVE_LINK_NOT_FETCHED'}
REASONS = PARSE_REASONS | {'HTTP_CHAIN_INCOMPLETE', 'ORIGIN_ONLY_NOT_FULL_LINK',
    'KNOWN_THREAT_MATCH', 'NO_LIST_MATCH', 'BROWSER_NAVIGATION_NOT_EVALUATED',
    'PROVIDER_UNAVAILABLE', 'PROVIDER_RATE_LIMITED', 'PROVIDER_AUTHORIZATION_FAILED',
    'PROVIDER_RESPONSE_INVALID', 'SECRET_UNAVAILABLE', 'CONFIGURATION_UNAVAILABLE',
    'RESOLVER_UNAVAILABLE', 'RESOLVER_RESPONSE_INVALID', 'TIME_BUDGET_EXCEEDED',
    'INTERNAL_ERROR', 'DEV_ONLY_DISABLED'}


def supported(value, expected_id, request_scope):
    if (type(value) is not dict or set(value) != KEYS or type(value['schemaVersion']) is not int
            or value['schemaVersion'] != 3 or value['scope'] != SCOPE
            or value['consumerAccessEnabled'] is not False or request_scope not in ('full_url', 'origin_only')):
        return False
    if value['checkId'] != expected_id and not (value['checkId'] is None and value['verdict'] == 'unknown'):
        return False
    if (value['verdict'] not in ('unknown', 'high_risk', 'no_known_threat_detected')
            or value['processingOutcome'] not in ('invalid_input', 'blocked', 'unavailable', 'partial', 'complete')
            or value['coverage'] not in ('not_assessed', 'limited', 'supported_checks_complete')
            or value['targetContactStatus'] not in ('not_contacted', 'contacted', 'unknown')
            or value['matchScope'] not in (None, 'full_submitted_url', 'origin_only', 'redirect_destination')):
        return False
    for key, choices, maximum in (('reasonCodes', REASONS, 2),
            ('transportWarnings', {'UNENCRYPTED_CONNECTION', 'HTTPS_TO_HTTP_REDIRECT'}, 2),
            ('threatTypes', THREATS, 3)):
        items = value[key]
        if (type(items) is not list or len(items) > maximum
                or any(type(x) is not str or x not in choices for x in items) or len(set(items)) != len(items)):
            return False
    if not value['reasonCodes']:
        return False
    if any(type(value[k]) is not int or not 0 <= value[k] <= 6
           for k in ('lookupCount', 'providerCallCount', 'observedHopCount')):
        return False
    lookups, calls, hops = (value[k] for k in ('lookupCount', 'providerCallCount', 'observedHopCount'))
    if not calls <= lookups or not hops <= lookups <= hops + 1:
        return False
    if (value['targetContactStatus'] == 'not_contacted' and hops != 0
            or value['targetContactStatus'] == 'contacted' and hops == 0):
        return False
    if request_scope == 'origin_only' and (hops != 0 or lookups > 1 or value['targetContactStatus'] != 'not_contacted'):
        return False
    if value['verdict'] == 'high_risk':
        wanted = ('origin_only' if request_scope == 'origin_only' else
                  ('full_submitted_url' if hops == 0 else 'redirect_destination'))
        if (not value['threatTypes'] or calls != lookups or lookups != hops + 1
                or value['matchScope'] != wanted or value['processingOutcome'] != 'partial'
                or value['coverage'] != 'limited' or value['reasonCodes'] != ['KNOWN_THREAT_MATCH']
                or value['targetContactStatus'] != ('contacted' if hops else 'not_contacted')):
            return False
    elif value['threatTypes'] or value['matchScope'] is not None or 'KNOWN_THREAT_MATCH' in value['reasonCodes']:
        return False
    if value['verdict'] == 'no_known_threat_detected':
        if (request_scope != 'full_url' or value['processingOutcome'] != 'complete'
                or value['coverage'] != 'supported_checks_complete' or not 0 < calls == lookups == hops
                or value['targetContactStatus'] != 'contacted'
                or set(value['reasonCodes']) != {'NO_LIST_MATCH', 'BROWSER_NAVIGATION_NOT_EVALUATED'}):
            return False
    elif value['processingOutcome'] == 'complete':
        return False
    try:
        if value['lookupObservedAt'] is None:
            return value['lookupValidUntil'] is None and value['verdict'] == 'unknown'
        validate_times(value['lookupObservedAt'], value['lookupValidUntil'], matched=value['verdict'] == 'high_risk')
        return True
    except (ValueError, TypeError, OverflowError):
        return False


def supported_step(value, expected_id, expected_url):
    keys = {'schemaVersion', 'checkId', 'resolutionStatus', 'hops', 'location',
            'reasonCodes', 'requestCount', 'scope'}
    if (type(value) is not dict or set(value) != keys or type(value['schemaVersion']) is not int
            or value['schemaVersion'] != 2 or value['checkId'] != expected_id
            or value['scope'] != 'HTTP_SINGLE_HOP_ONLY' or type(value['requestCount']) is not int
            or value['requestCount'] not in (0, 1) or type(value['hops']) is not list
            or len(value['hops']) > 1 or type(value['reasonCodes']) is not list
            or len(value['reasonCodes']) > 1
            or any(type(x) is not str or not re.fullmatch('[A-Z_]{1,80}', x) for x in value['reasonCodes'])):
        return False
    location = value['location']
    try:
        if location is not None and (type(location) is not str or len(location.encode('utf-8')) > 2048):
            return False
    except UnicodeError:
        return False
    if value['resolutionStatus'] == 'http_response':
        if value['requestCount'] != 1 or len(value['hops']) != 1 or value['reasonCodes']:
            return False
        hop = value['hops'][0]
        return (type(hop) is dict and set(hop) == {'url', 'httpStatus'} and hop['url'] == expected_url
                and type(hop['httpStatus']) is int and 100 <= hop['httpStatus'] <= 599)
    return (value['resolutionStatus'] in ('blocked', 'invalid_input', 'partial')
            and not value['hops'] and location is None and len(value['reasonCodes']) == 1)
