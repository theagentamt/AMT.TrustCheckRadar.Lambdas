"""Explicit tested whole-message coverage, not general semantic inference."""
import re
from shared_message_contract.validation import POLICY, validate_intent, validate_summary

# These exact normalized whole-message cases are the initial qualified coverage.
# Paraphrases, quotes, unknown segments and LLM agreement do not extend it.
COVERAGE = {
    'en': {
        'pay this tax fee by buying gift cards and send me the numbers and pins.': 'DEMAND_GIFT_CARD_PAYMENT',
        'send me your account password and the login code.': 'REQUEST_SECRET_DISCLOSURE',
        'transfer the money and do not check with anyone.': 'PAYMENT_WITH_SECRECY_PRESSURE',
        'i bought a gift card for your birthday.': 'BENIGN_FIXED_TEXT',
        'never send anyone your login code.': 'BENIGN_FIXED_TEXT',
    },
    'es': {
        'pague esta tasa con tarjetas de regalo y envíeme los números y pin.': 'DEMAND_GIFT_CARD_PAYMENT',
        'envíeme la contraseña de su cuenta y el código de acceso.': 'REQUEST_SECRET_DISCLOSURE',
        'transfiera el dinero y no lo consulte con nadie.': 'PAYMENT_WITH_SECRECY_PRESSURE',
        'compré una tarjeta de regalo para tu cumpleaños.': 'BENIGN_FIXED_TEXT',
        'nunca envíe a nadie su código de acceso.': 'BENIGN_FIXED_TEXT',
    },
}


def result(check, *, rules=(), limits=(), evidence=()):
    rules=list(dict.fromkeys(rules));limits=list(dict.fromkeys(limits));evidence=list(evidence)
    matched=any(e['outcome']=='match' for e in evidence)
    if matched and not limits:limits.append('INSUFFICIENT_EVIDENCE')
    high=matched or bool(set(rules)&{'DEMAND_GIFT_CARD_PAYMENT','REQUEST_SECRET_DISCLOSURE'})
    if high:
        verdict='high_risk';state='partial' if limits else 'complete';action='avoid_link' if matched else 'pause_and_verify'
        key='message.partial_known_threat' if matched else 'message.high_risk_request'
    elif 'PAYMENT_WITH_SECRECY_PRESSURE' in rules:
        verdict='suspicious';state='partial' if limits else 'complete';action='verify_independently';key='message.suspicious_request'
    elif rules==['BENIGN_FIXED_TEXT'] and not limits:
        verdict='no_known_threat_detected';state='complete';action='verify_independently';key='message.no_supported_finding'
    else:
        verdict='unknown';action='review_input'
        if 'HOSTILE_INPUT_STOP' in limits:state='blocked';key='message.hostile_stop'
        elif set(limits)&{'PROVIDER_UNAVAILABLE','PROVIDER_RESPONSE_INVALID','BUDGET_LIMIT'}:
            state='unavailable';action='use_built_in_help';key='message.provider_unavailable'
        else:
            state='inconclusive';key='message.inconclusive'
            if 'INSUFFICIENT_EVIDENCE' not in limits:limits.append('INSUFFICIENT_EVIDENCE')
    value={'schemaVersion':1,'kind':'message_assessment','checkId':check,'policyVersion':POLICY,'verdict':verdict,
           'processingOutcome':state,'coverage':'supported_checks_complete' if state=='complete' else ('limited' if rules or evidence else 'not_assessed'),
           'limitationCodes':limits,'ruleIds':rules,'evidence':evidence,'nextAction':action,'messageKey':key}
    return validate_summary(value,check)


def evaluate(check, intent, *, lookup=None, budget_ms=18000):
    validate_intent(intent)
    target=intent['target'];text=target['sanitizedText'];limits=[];rules=[];evidence=[]
    # This stop heuristic protects processing, never establishes an abuse verdict.
    instruction=sum(bool(re.search(p,text,re.IGNORECASE)) for p in (
        r'ignore\s+(?:all\s+)?previous\s+instructions',r'you\s+are\s+chatgpt',r'return\s+exactly',
        r'ignore\s+las\s+instrucciones\s+anteriores',r'ignora\s+(?:las\s+)?instrucciones\s+anteriores',
        r'indique\s+que\s+es\s+seguro',r'eres\s+chatgpt',r'devuelve\s+exactamente',r'mark\s+safe'))
    if instruction>=2:limits.append('HOSTILE_INPUT_STOP')
    if target['speakerRole'] in ('mixed','unknown'):limits.append('UNKNOWN_SPEAKER')
    normalized=' '.join(text.casefold().split())
    rule=COVERAGE[intent['language']].get(normalized)
    if not limits and rule and (rule=='BENIGN_FIXED_TEXT' or target['speakerRole']=='other'):
        rules.append(rule)
    else:
        limits.append('INSUFFICIENT_EVIDENCE')
    if target['withheldLinks']:limits.append('WITHHELD_LINKS')
    for link in target['reviewedLinks']:
        if lookup is None or budget_ms<14000:
            limits.append('PROVIDER_UNAVAILABLE' if lookup is None else 'BUDGET_LIMIT');continue
        try:
            raw=lookup({'schemaVersion':1,'checkId':check,'url':link['url'],'scope':link['scope'],
                        'executionBudgetMs':min(18000,budget_ms)})
            from shared_message_contract.runtime import url_mapper
            if not url_mapper().supported_private(raw,check,link['scope']):raise ValueError()
            if raw['verdict']=='high_risk' and 'KNOWN_THREAT_MATCH' in raw['reasonCodes']:
                evidence.append({'source':'google_web_risk_lookup','outcome':'match','targetScope':'observed_http_chain'})
            elif raw['verdict']=='no_known_threat_detected' and raw['processingOutcome']=='complete' and 'NO_LIST_MATCH' in raw['reasonCodes']:
                evidence.append({'source':'google_web_risk_lookup','outcome':'no_match','targetScope':'origin_only' if link['scope']=='origin_only' else 'full_submitted_url'})
            else:limits.append('PROVIDER_UNAVAILABLE')
            if raw['processingOutcome']!='complete' or link['scope']!='full_url':limits.append('WITHHELD_LINKS')
        except Exception:
            limits.append('PROVIDER_UNAVAILABLE')
    return result(check,rules=rules,limits=limits,evidence=evidence)
