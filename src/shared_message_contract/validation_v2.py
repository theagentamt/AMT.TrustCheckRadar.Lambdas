"""Candidate.2 AI policy; candidate.1 validation and semantics are immutable."""
from .validation import (validate_intent, require, RULES, LIMITS, ACTIONS, MESSAGES,
                         validate_summary as validate_v1, POLICY as POLICY_V1)
import re

VERSION = '1.0.0-message-candidate.2'
POLICY = 'message-ai-2026-09-21-v1'
APPROVAL_SHA = 'd6e9fff12225540bef9ba7833cce457cca4b9c791af3b49dd8a4f1601d204349'
AI_REASONS = {'AI_CREDENTIAL_REQUEST','AI_PAYMENT_PRESSURE','AI_PRETEXT',
              'AI_VERIFICATION_BYPASS','AI_CONSEQUENTIAL_URGENCY'}
BASES = {'qualified_rules','google_web_risk_lookup','ai_assessment'}
AI_STATUSES = {'not_assessed','warning','no_warning','abstained','unavailable'}
AI_MESSAGES = {'message.ai_warning','message.ai_no_warning','message.ai_inconclusive'}


def validate_summary(value, client_id=None, outcome=None):
    original_keys={'schemaVersion','kind','checkId','policyVersion','verdict','processingOutcome','coverage',
                   'limitationCodes','ruleIds','evidence','nextAction','messageKey'}
    require(type(value) is dict and set(value)==original_keys|{'assessmentBasis','aiAssessmentStatus','aiReasonCodes'},'RESULT_SUMMARY_INVALID')
    require(type(value['schemaVersion']) is int and value['schemaVersion']==2 and value['kind']=='message_assessment'
            and value['policyVersion']==POLICY,'RESULT_SUMMARY_INVALID')
    require(type(value['checkId']) is str and re.fullmatch('[A-Za-z0-9_-]{1,64}',value['checkId']) and
            (client_id is None or client_id==value['checkId']) and (outcome is None or outcome==value['processingOutcome']),'RESULT_SUMMARY_INVALID')
    for key, allowed, cap in [('assessmentBasis',BASES,3),('aiReasonCodes',AI_REASONS,5),
                              ('ruleIds',RULES,4),('limitationCodes',LIMITS,8)]:
        require(type(value[key]) is list and len(value[key])<=cap and all(type(x) is str and x in allowed for x in value[key])
                and len(set(value[key]))==len(value[key]),'RESULT_SUMMARY_INVALID')
    require(value['aiAssessmentStatus'] in AI_STATUSES,'RESULT_SUMMARY_INVALID')
    status=value['aiAssessmentStatus']; reasons=value['aiReasonCodes']; rules=value['ruleIds']; evidence=value['evidence']
    require(bool(reasons)==(status=='warning'),'RESULT_SUMMARY_INVALID')
    require(type(evidence) is list and len(evidence)<=2,'RESULT_SUMMARY_INVALID')
    for item in evidence:
        require(type(item) is dict and set(item)=={'source','outcome','targetScope'} and
                item['source']=='google_web_risk_lookup' and item['outcome'] in ('match','no_match') and
                item['targetScope'] in ('full_submitted_url','observed_http_chain','origin_only','redirect_hop'),'RESULT_SUMMARY_INVALID')
    basis=set()
    if rules:basis.add('qualified_rules')
    if evidence:basis.add('google_web_risk_lookup')
    if status in ('warning','no_warning'):basis.add('ai_assessment')
    require(set(value['assessmentBasis'])==basis,'RESULT_SUMMARY_INVALID')
    limits=value['limitationCodes']; state=value['processingOutcome']; verdict=value['verdict']
    require(state in ('complete','partial','inconclusive','blocked','unavailable') and
            value['coverage'] in ('supported_checks_complete','limited','not_assessed'),'RESULT_SUMMARY_INVALID')
    require((not limits and state=='complete' and value['coverage']=='supported_checks_complete') or
            (bool(limits) and state!='complete' and value['coverage']==('limited' if basis else 'not_assessed')),'RESULT_SUMMARY_INVALID')
    if status in ('abstained','unavailable'):require(bool(limits),'RESULT_SUMMARY_INVALID')
    if status=='unavailable':require(bool(set(limits)&{'PROVIDER_UNAVAILABLE','PROVIDER_RESPONSE_INVALID','BUDGET_LIMIT'}),'RESULT_SUMMARY_INVALID')
    if status=='abstained':require(bool(set(limits)&{'INSUFFICIENT_EVIDENCE','UNKNOWN_SPEAKER','UNSUPPORTED_CONTENT','HOSTILE_INPUT_STOP'}),'RESULT_SUMMARY_INVALID')
    if status in ('warning','no_warning'):
        require(not set(limits)&{'HOSTILE_INPUT_STOP','UNKNOWN_SPEAKER','UNSUPPORTED_CONTENT'},'RESULT_SUMMARY_INVALID')
        require('INSUFFICIENT_EVIDENCE' not in limits or any(e['outcome']=='match' for e in evidence),'RESULT_SUMMARY_INVALID')
        require(not (status=='warning' and 'BENIGN_FIXED_TEXT' in rules),'RESULT_SUMMARY_INVALID')
    independent_warning=bool(set(rules)&{'DEMAND_GIFT_CARD_PAYMENT','REQUEST_SECRET_DISCLOSURE','PAYMENT_WITH_SECRECY_PRESSURE'}) or any(e['outcome']=='match' for e in evidence)
    if independent_warning or (rules==['BENIGN_FIXED_TEXT'] and not limits and status!='warning'):
        # Existing verified evidence retains its precedence and conservative link
        # partial semantics. AI disagreement must not erase it or change its copy.
        baseline={k:value[k] for k in original_keys};baseline.update(schemaVersion=1,policyVersion=POLICY_V1)
        validate_v1(baseline,client_id,outcome)
        return value
    if status=='warning':
        require(verdict=='suspicious' and state==('partial' if limits else 'complete') and
                value['messageKey']=='message.ai_warning' and value['nextAction']=='verify_independently','RESULT_SUMMARY_INVALID')
    elif status=='no_warning' and not limits:
        require(verdict=='no_known_threat_detected' and state=='complete' and
                value['messageKey']=='message.ai_no_warning' and value['nextAction']=='verify_independently','RESULT_SUMMARY_INVALID')
    else:
        require(verdict=='unknown','RESULT_SUMMARY_INVALID')
        if 'HOSTILE_INPUT_STOP' in limits:
            expected=('blocked','message.hostile_stop','review_input')
        elif set(limits)&{'PROVIDER_UNAVAILABLE','PROVIDER_RESPONSE_INVALID','BUDGET_LIMIT'}:
            expected=('unavailable','message.provider_unavailable','use_built_in_help')
        else:expected=('inconclusive','message.ai_inconclusive','review_input')
        require((state,value['messageKey'],value['nextAction'])==expected,'RESULT_SUMMARY_INVALID')
    return value
