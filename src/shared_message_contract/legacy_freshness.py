"""Legacy Google matches have no deadline: preserve other evidence, not validity."""
from . import validation_v2 as v2
from .validation import validate_summary

def historical(value):
    original=(v2.validate_summary if value['schemaVersion']==2 else validate_summary)(value)
    from message_evaluator.policy import result as rules
    from message_evaluator.policy_v2 import result as ai
    limits=list(original['limitationCodes'])
    if 'WITHHELD_LINKS' not in limits:limits.append('WITHHELD_LINKS')
    evidence=[e for e in original['evidence'] if e['outcome']!='match']
    if original['schemaVersion']==2:
        if original['aiAssessmentStatus'] in ('warning','no_warning'):limits=[x for x in limits if x!='INSUFFICIENT_EVIDENCE']
        return ai(original['checkId'],rules=original['ruleIds'],limits=limits,evidence=evidence,ai_status=original['aiAssessmentStatus'],ai_reasons=original['aiReasonCodes'])
    return rules(original['checkId'],rules=original['ruleIds'],limits=limits,evidence=evidence)
