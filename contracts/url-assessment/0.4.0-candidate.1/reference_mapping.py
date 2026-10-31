"""Executable public mapping for the guarded URL candidate.

Access and accounting snapshots are mandatory independent authoritative inputs;
never derive them from private provider counters, verdicts, or transport status.
"""
from copy import deepcopy
import json
from pathlib import Path
import re
import time
from shared_lookup_freshness import validate as validate_times, epoch, current

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).parent
VERSION = '0.4.0-candidate.1'
REASON_MAP = {
    'INVALID_REQUEST': 'INVALID_URL', 'INVALID_URL': 'INVALID_URL',
    'AMBIGUOUS_URL': 'INVALID_URL', 'INVALID_URL_ENCODING': 'INVALID_URL',
    'INVALID_AUTHORITY': 'INVALID_URL', 'AMBIGUOUS_AUTHORITY': 'INVALID_URL',
    'INVALID_HOST': 'INVALID_URL', 'AMBIGUOUS_ADDRESS': 'INVALID_URL',
    'EXCESSIVE_URL_ENCODING': 'INVALID_URL',
    'UNSUPPORTED_SCHEME': 'UNSUPPORTED_URL', 'UNSUPPORTED_PORT': 'UNSUPPORTED_URL',
    'UNSUPPORTED_ADDRESS_ENCODING': 'UNSUPPORTED_URL',
    'NON_PUBLIC_DESTINATION': 'DESTINATION_BLOCKED',
    'SENSITIVE_LINK_NOT_FETCHED': 'SENSITIVE_URL_BLOCKED',
    'NO_ELIGIBLE_OBSERVATION': 'URL_NOT_FULLY_CHECKED',
    'HTTP_CHAIN_INCOMPLETE': 'URL_NOT_FULLY_CHECKED',
    'ORIGIN_ONLY_NOT_FULL_LINK': 'ORIGIN_ONLY_CHECKED',
    'KNOWN_THREAT_MATCH': 'KNOWN_THREAT_MATCH', 'NO_LIST_MATCH': 'NO_LIST_MATCH',
    'BROWSER_NAVIGATION_NOT_EVALUATED': 'BROWSER_NAVIGATION_NOT_EVALUATED',
    'PROVIDER_UNAVAILABLE': 'PROVIDER_TEMPORARILY_UNAVAILABLE',
    'PROVIDER_RATE_LIMITED': 'PROVIDER_RATE_LIMITED',
    'PROVIDER_AUTHORIZATION_FAILED': 'SERVICE_UNAVAILABLE',
    'PROVIDER_RESPONSE_INVALID': 'SERVICE_UNAVAILABLE',
    'SECRET_UNAVAILABLE': 'SERVICE_UNAVAILABLE',
    'CONFIGURATION_UNAVAILABLE': 'SERVICE_UNAVAILABLE',
    'RESOLVER_UNAVAILABLE': 'SERVICE_UNAVAILABLE',
    'RESOLVER_RESPONSE_INVALID': 'SERVICE_UNAVAILABLE',
    'TIME_BUDGET_EXCEEDED': 'PROCESSING_TIMEOUT',
    'INTERNAL_ERROR': 'SERVICE_UNAVAILABLE', 'DEV_ONLY_DISABLED': 'SERVICE_UNAVAILABLE',
}


def validate(schema_name, value):
    schema = json.loads((ROOT / schema_name).read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def retry_for(access, accounting, *, same_check_replay_authorized, invalid=False, compatibility=False, terminal=False):
    if terminal:
        disposition = 'do_not_retry'
    elif compatibility:
        disposition = 'after_client_update'
    elif access['state'] == 'sign_in_required':
        disposition = 'after_sign_in'
    elif accounting['requiresReconciliation']:
        disposition = 'reconcile_same_check'
    elif invalid:
        disposition = 'after_input_review'
    elif terminal:
        disposition = 'do_not_retry'
    elif access['state'] == 'device_action_required':
        disposition = 'after_device_action'
    elif access['state'] in ('subscription_required', 'allowance_exhausted'):
        disposition = 'after_access_change'
    elif same_check_replay_authorized and access['externalChecksAllowed'] and accounting['state'] == 'not_charged':
        disposition = 'retry_same_check'
    else:
        disposition = 'do_not_retry'
    return {'disposition': disposition, 'afterSeconds': None, 'automaticRetryAllowed': False}


def supported_private(value, expected_id, request_scope):
    from shared_url_guarded import supported
    return supported(value, expected_id, request_scope)


def map_private(value, *, expected_check_id, request_scope, assessed_at, access, accounting, same_check_replay_authorized, now=None):
    """Require authenticated-operation identity + authority facts from future backend."""
    if not isinstance(expected_check_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', expected_check_id):
        raise ValueError('A trusted operation identity is required')
    if request_scope not in ('full_url', 'origin_only'):
        raise ValueError('A trusted request projection is required')
    if type(same_check_replay_authorized) is not bool:
        raise ValueError('An explicit trusted operation replay policy is required')
    validate('access.schema.json', access)
    validate('accounting.schema.json', accounting)
    common = {'contractVersion': VERSION, 'checkId': expected_check_id,
              'access': deepcopy(access), 'accounting': deepcopy(accounting)}
    if not supported_private(value, expected_check_id, request_scope):
        result = common | {'kind': 'request_error', 'error': {'code': 'SERVICE_UNAVAILABLE', 'messageKey': 'error.service'},
                           'nextAction': 'use_built_in_help', 'retry': retry_for(access, accounting, same_check_replay_authorized=False)}
        validate('error.schema.json', result)
        return result
    now = time.time() if now is None else now
    stale = None
    if value['verdict'] == 'high_risk':
        stale = None if current(value['lookupObservedAt'], value['lookupValidUntil'], now) else 'PROVIDER_EVIDENCE_EXPIRED'
    if value['lookupObservedAt'] is not None and epoch(value['lookupObservedAt']) > epoch(assessed_at):
        raise ValueError('OBSERVATION_AFTER_ASSESSMENT')
    codes = list(dict.fromkeys(REASON_MAP[x] for x in value['reasonCodes']))
    verdict = value['verdict']
    processing = value['processingOutcome']
    evidence = []
    message = 'url.unavailable'
    action = 'use_built_in_help'
    if verdict == 'high_risk':
        message, action = ('url.known_threat_partial' if processing == 'partial' else 'url.known_threat'), 'avoid_link'
        evidence = [{'source': 'google_web_risk_lookup', 'outcome': 'match',
                     'targetScope': value['matchScope'], 'threatTypes': sorted(set(value['threatTypes']))}]
    elif verdict == 'no_known_threat_detected':
        message, action = 'url.no_known_threat', 'verify_independently'
        evidence = [{'source': 'google_web_risk_lookup', 'outcome': 'no_match', 'targetScope': 'full_submitted_url'},
                    {'source': 'http_redirect_resolver', 'outcome': 'completed', 'targetScope': 'observed_http_chain'}]
    elif 'ORIGIN_ONLY_CHECKED' in codes:
        message, action = 'url.origin_only', 'verify_independently'
        evidence = [{'source': 'google_web_risk_lookup', 'outcome': 'no_match', 'targetScope': 'origin_only'}]
    elif 'INVALID_URL' in codes:
        message, action, processing = 'url.invalid', 'review_input', 'invalid_input'
    elif 'UNSUPPORTED_URL' in codes:
        message, action, processing = 'url.unsupported', 'review_input', 'unsupported'
    elif 'SENSITIVE_URL_BLOCKED' in codes:
        message, action, processing = 'url.sensitive', 'review_input', 'blocked'
    elif 'DESTINATION_BLOCKED' in codes:
        message, action, processing = 'url.destination_blocked', 'review_input', 'blocked'
    elif 'PROVIDER_RATE_LIMITED' in codes:
        message = 'url.rate_limited'
    elif 'PROCESSING_TIMEOUT' in codes:
        message = 'url.timeout'
    for item in evidence:
        if item['source']=='google_web_risk_lookup':
            item.setdefault('threatTypes',[])
            item.update(observedAt=value['lookupObservedAt'], validUntil=value['lookupValidUntil'],
                        freshness='observation_only' if item['outcome']=='no_match' else ('expired' if stale else 'current'))
    if stale:
        verdict, processing, action = 'unknown', 'partial', 'use_built_in_help'
        message = 'url.evidence_expired' if stale=='PROVIDER_EVIDENCE_EXPIRED' else 'url.evidence_unverified'
        codes=[stale]
    limitations = list(codes)
    result = common | {'kind': 'assessment_result', 'assessedAt': assessed_at, 'verdict': verdict,
                       'processingOutcome': processing,
                       'uncertainty': {'coverage': value['coverage'], 'limitations': limitations},
                       'evidence': evidence, 'reasonCodes': codes,
                       'transportWarnings': sorted(set(value['transportWarnings'])),
                       'nextAction': action, 'messageKey': message,
                       'retry': retry_for(access, accounting, same_check_replay_authorized=same_check_replay_authorized, invalid=processing in ('invalid_input', 'unsupported', 'blocked'),
                                          terminal=bool(stale) or verdict in ('high_risk', 'no_known_threat_detected'))}
    validate('result.schema.json', result)
    return result
