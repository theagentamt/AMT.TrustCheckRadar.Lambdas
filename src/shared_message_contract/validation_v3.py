"""Provider-validity metadata; unchanged candidate.2 rules and AI policy."""
from copy import deepcopy
from . import validation_v2 as prior
from .validation import require
from shared_lookup_freshness import evidence as lookup_evidence, current

VERSION='1.0.0-message-candidate.3'
POLICY=prior.POLICY
APPROVAL_SHA=prior.APPROVAL_SHA
FRESHNESS={'current','expired','unverified','observation_only'}


def baseline(value):
    result=deepcopy(value);result['schemaVersion']=2
    result['evidence']=[{k:e[k] for k in ('source','outcome','targetScope')} for e in value['evidence'] if e['freshness'] in ('current','observation_only')]
    return result


def validate_summary(value, client_id=None, outcome=None):
    require(type(value) is dict and type(value.get('schemaVersion')) is int and value['schemaVersion']==3,'RESULT_SUMMARY_INVALID')
    require(type(value.get('evidence')) is list and len(value['evidence'])<=2,'RESULT_SUMMARY_INVALID')
    for e in value['evidence']:
        require(type(e) is dict and set(e)=={'source','outcome','targetScope','observedAt','validUntil','threatTypes','freshness'},'RESULT_SUMMARY_INVALID')
        require(e['source']=='google_web_risk_lookup' and e['outcome'] in ('match','no_match') and e['targetScope'] in ('full_submitted_url','origin_only','redirect_hop','observed_http_chain') and type(e['freshness']) is str and e['freshness'] in FRESHNESS,'RESULT_SUMMARY_INVALID')
        require((e['outcome']=='match')==(e['freshness'] in ('current','expired','unverified')),'RESULT_SUMMARY_INVALID')
        if e['freshness']=='unverified':
            require(e['observedAt'] is None and e['validUntil'] is None and e['threatTypes']==[],'RESULT_SUMMARY_INVALID')
        else:
            try:lookup_evidence({k:e[k] for k in ('threatTypes','observedAt','validUntil')})
            except (ValueError,TypeError,OverflowError):require(False,'RESULT_SUMMARY_INVALID')
            require(bool(e['threatTypes'])==(e['outcome']=='match'),'RESULT_SUMMARY_INVALID')
    prior.validate_summary(baseline(value),client_id,outcome)
    return value


def present(value, now):
    """Return display projection; never modify the immutable settled summary."""
    validate_summary(value)
    result=deepcopy(value);changed=False
    for e in result['evidence']:
        if e['freshness']=='current' and not current(e['observedAt'],e['validUntil'],now):
            e['freshness']='expired';changed=True
    if changed:
        from message_evaluator.policy_v2 import result as rebuild
        active=[{k:e[k] for k in ('source','outcome','targetScope')} for e in result['evidence'] if e['freshness'] in ('current','observation_only')]
        limits=list(result['limitationCodes'])
        # Stale links preserve limited coverage, never create a no-warning result.
        if 'WITHHELD_LINKS' not in limits:limits.append('WITHHELD_LINKS')
        if result['aiAssessmentStatus'] in ('warning','no_warning'):
            limits=[x for x in limits if x!='INSUFFICIENT_EVIDENCE']
        fresh=rebuild(result['checkId'],rules=result['ruleIds'],limits=limits,evidence=active,ai_status=result['aiAssessmentStatus'],ai_reasons=result['aiReasonCodes'])
        fresh.update(schemaVersion=3,evidence=result['evidence']);result=fresh
    return validate_summary(result)
