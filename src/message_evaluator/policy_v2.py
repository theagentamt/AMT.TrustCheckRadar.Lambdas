"""Approved AI-assisted policy, separate from candidate.1 semantics."""
from shared_message_contract.validation_v2 import POLICY, validate_summary
from .policy import result as result_v1


def result(check, *, rules=(), limits=(), evidence=(), ai_status='not_assessed', ai_reasons=()):
    rules=list(dict.fromkeys(rules));limits=list(dict.fromkeys(limits));evidence=list(evidence)
    ai_reasons=list(dict.fromkeys(ai_reasons))
    # Partial link coverage may preserve a text finding. Untrustworthy text
    # attribution/context or a hostile stop cannot preserve an AI-only finding.
    if ai_status in ('warning','no_warning') and (
        set(limits)&{'HOSTILE_INPUT_STOP','UNKNOWN_SPEAKER','UNSUPPORTED_CONTENT'} or
        ('INSUFFICIENT_EVIDENCE' in limits and not any(e['outcome']=='match' for e in evidence)) or
        (ai_status=='warning' and 'BENIGN_FIXED_TEXT' in rules)):
        ai_status='abstained';ai_reasons=[]
        if not limits:limits.append('INSUFFICIENT_EVIDENCE')
    if ai_status=='abstained' and not limits:limits.append('INSUFFICIENT_EVIDENCE')
    if ai_status=='unavailable' and not limits:limits.append('PROVIDER_UNAVAILABLE')
    independent=bool(set(rules)&{'DEMAND_GIFT_CARD_PAYMENT','REQUEST_SECRET_DISCLOSURE','PAYMENT_WITH_SECRECY_PRESSURE'}) or any(e['outcome']=='match' for e in evidence)
    if independent or (rules==['BENIGN_FIXED_TEXT'] and not limits and ai_status!='warning'):
        value=result_v1(check,rules=rules,limits=limits,evidence=evidence)
    else:
        if ai_status=='warning':
            verdict='suspicious';state='partial' if limits else 'complete';key='message.ai_warning';action='verify_independently'
        elif ai_status=='no_warning' and not limits:
            verdict='no_known_threat_detected';state='complete';key='message.ai_no_warning';action='verify_independently'
        else:
            verdict='unknown'
            if not limits:limits.append('INSUFFICIENT_EVIDENCE')
            if 'HOSTILE_INPUT_STOP' in limits:state='blocked';key='message.hostile_stop';action='review_input'
            elif set(limits)&{'PROVIDER_UNAVAILABLE','PROVIDER_RESPONSE_INVALID','BUDGET_LIMIT'}:
                state='unavailable';key='message.provider_unavailable';action='use_built_in_help'
            else:state='inconclusive';key='message.ai_inconclusive';action='review_input'
        value={'kind':'message_assessment','checkId':check,'verdict':verdict,'processingOutcome':state,
               'limitationCodes':limits,'ruleIds':rules,'evidence':evidence,'nextAction':action,'messageKey':key}
    basis=[]
    if rules:basis.append('qualified_rules')
    if evidence:basis.append('google_web_risk_lookup')
    if ai_status in ('warning','no_warning'):basis.append('ai_assessment')
    value.update(schemaVersion=2,policyVersion=POLICY,assessmentBasis=basis,aiAssessmentStatus=ai_status,aiReasonCodes=ai_reasons,
                 coverage='supported_checks_complete' if value['processingOutcome']=='complete' else ('limited' if basis else 'not_assessed'))
    return validate_summary(value,check)
