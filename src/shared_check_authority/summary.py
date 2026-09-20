"""Allowlisted private assessment summary only; no URL-bearing fields."""
from .core import AuthorityError
import re


def validate_summary(value, client_id, outcome):
    keys={'schemaVersion','checkId','verdict','processingOutcome','coverage','reasonCodes','transportWarnings','threatTypes','lookupCount','providerCallCount','observedHopCount','scope','consumerAccessEnabled'}
    if not isinstance(value,dict) or set(value)!=keys or value['checkId']!=client_id or value['processingOutcome']!=outcome:
        raise AuthorityError('RESULT_SUMMARY_INVALID')
    if value['schemaVersion']!=1 or value['consumerAccessEnabled'] is not False or value['scope']!='HTTP_REDIRECTS_AND_GOOGLE_LOOKUP':
        raise AuthorityError('RESULT_SUMMARY_INVALID')
    if value['verdict'] not in ('unknown','high_risk','no_known_threat_detected') or value['coverage'] not in ('not_assessed','limited','supported_checks_complete'):
        raise AuthorityError('RESULT_SUMMARY_INVALID')
    for key in ('reasonCodes','transportWarnings','threatTypes'):
        if not isinstance(value[key],list) or len(value[key])>3 or any(not isinstance(x,str) or not re.fullmatch('[A-Z_]{1,80}',x) for x in value[key]):
            raise AuthorityError('RESULT_SUMMARY_INVALID')
    if any(type(value[k]) is not int or not 0<=value[k]<=6 for k in ('lookupCount','providerCallCount','observedHopCount')):
        raise AuthorityError('RESULT_SUMMARY_INVALID')
    return value
