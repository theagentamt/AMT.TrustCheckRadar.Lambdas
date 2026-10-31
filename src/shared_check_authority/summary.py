"""Allowlisted private assessment summary only; no URL-bearing fields."""
from .core import AuthorityError
import re


def validate_summary(value, client_id, outcome, *, request_scope=None):
    if type(value) is dict and value.get('schemaVersion') == 3:
        from shared_url_guarded import supported
        scopes = (request_scope,) if request_scope is not None else ('full_url', 'origin_only')
        if (value.get('checkId') != client_id or value.get('processingOutcome') != outcome
                or not any(supported(value, client_id, scope) for scope in scopes)):
            raise AuthorityError('RESULT_SUMMARY_INVALID')
        return value
    keys={'schemaVersion','checkId','verdict','processingOutcome','coverage','reasonCodes','transportWarnings','threatTypes','lookupCount','providerCallCount','observedHopCount','scope','consumerAccessEnabled'}
    modern=isinstance(value,dict) and value.get('schemaVersion')==2
    expected=keys|{'lookupObservedAt','lookupValidUntil'} if modern else keys
    if not isinstance(value,dict) or set(value)!=expected or value['checkId']!=client_id or value['processingOutcome']!=outcome:
        raise AuthorityError('RESULT_SUMMARY_INVALID')
    if type(value['schemaVersion']) is not int or value['schemaVersion'] not in (1,2) or value['consumerAccessEnabled'] is not False or value['scope']!='HTTP_REDIRECTS_AND_GOOGLE_LOOKUP':
        raise AuthorityError('RESULT_SUMMARY_INVALID')
    if value['verdict'] not in ('unknown','high_risk','no_known_threat_detected') or value['coverage'] not in ('not_assessed','limited','supported_checks_complete'):
        raise AuthorityError('RESULT_SUMMARY_INVALID')
    for key in ('reasonCodes','transportWarnings','threatTypes'):
        if not isinstance(value[key],list) or len(value[key])>3 or any(not isinstance(x,str) or not re.fullmatch('[A-Z_]{1,80}',x) for x in value[key]):
            raise AuthorityError('RESULT_SUMMARY_INVALID')
    if any(type(value[k]) is not int or not 0<=value[k]<=6 for k in ('lookupCount','providerCallCount','observedHopCount')):
        raise AuthorityError('RESULT_SUMMARY_INVALID')
    if modern:
        try:
            from shared_lookup_freshness import validate
            if value['lookupObservedAt'] is None:
                if value['lookupValidUntil'] is not None or value['verdict']!='unknown':raise ValueError()
            else:validate(value['lookupObservedAt'],value['lookupValidUntil'],matched=value['verdict']=='high_risk')
        except (ValueError,TypeError,OverflowError):raise AuthorityError('RESULT_SUMMARY_INVALID') from None
    return value
