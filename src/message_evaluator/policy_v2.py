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


def evaluate(check,intent,*,lookup=None,budget_ms=18000,ai=None,clock=None):
    import json
    import re
    import time
    from shared_message_contract.validation import MessageError
    from shared_message_contract.privacy import validate_runtime_intent as validate_intent
    from .policy import evaluate as evaluate_v1
    from .ai_provider import parse
    clock=clock or time.monotonic
    validate_intent(intent)
    deadline=clock()+budget_ms/1000
    original=evaluate_v1(check,intent,lookup=lookup,budget_ms=budget_ms,clock=clock)
    rules=original['ruleIds'];limits=original['limitationCodes'];evidence=original['evidence'];target=intent['target']
    if rules:
        return result(check,rules=rules,limits=limits,evidence=evidence)
    if set(limits)&{'HOSTILE_INPUT_STOP','UNKNOWN_SPEAKER'} or target['speakerRole']!='other':
        return result(check,limits=limits,evidence=evidence,ai_status='abstained')
    # Obvious quotation/mixed-source boundaries are outside this initial profile;
    # absence of these markers does not prove context and is evaluated separately.
    text=target['sanitizedText']
    if target['sourceType']=='mixed' or re.search(r'["“”«»]|(?:^|\n)\s*>',text):
        return result(check,limits=list(limits)+['UNSUPPORTED_CONTENT'],evidence=evidence,ai_status='abstained')
    remaining=max(0,int((deadline-clock())*1000))
    if ai is None or remaining<250:
        return result(check,limits=list(limits)+['PROVIDER_UNAVAILABLE' if ai is None else 'BUDGET_LIMIT'],evidence=evidence,ai_status='unavailable')
    try:
        proposal=ai(intent,remaining)
        # Validate again at the policy boundary even if a provider adapter claims
        # it already parsed the response. No callback controls a public verdict.
        wire={'status':'completed','output':[{'type':'message','status':'completed','role':'assistant',
              'content':[{'type':'output_text','text':json.dumps(proposal)}]}]}
        proposal=parse(json.dumps(wire).encode(),text)
        if clock()>=deadline:raise MessageError('BUDGET_LIMIT')
        if proposal['assessment']=='abstain':
            extra='UNSUPPORTED_CONTENT' if proposal['context'] in ('unsupported','suspected_injection') else 'INSUFFICIENT_EVIDENCE'
            return result(check,limits=list(limits)+[extra],evidence=evidence,ai_status='abstained')
        # The old rule engine's unsupported-text limitation is resolved only by
        # this new, qualified AI policy. Independent link limitations remain.
        limits=[x for x in limits if x!='INSUFFICIENT_EVIDENCE']
        return result(check,limits=limits,evidence=evidence,ai_status=proposal['assessment'],
                      ai_reasons=[x['code'] for x in proposal['reasons']])
    except Exception as exc:
        code=exc.code if isinstance(exc,MessageError) else 'PROVIDER_UNAVAILABLE'
        code=code if code in ('PROVIDER_RESPONSE_INVALID','BUDGET_LIMIT') else 'PROVIDER_UNAVAILABLE'
        return result(check,limits=list(limits)+[code],evidence=evidence,ai_status='unavailable')
