"""Independent bounded privacy validation; never trusts a client sanitization flag."""
import json
import re
import unicodedata

VERSION = '1.0.0-message-candidate.1'
POLICY = 'message-rules-2026-09-20-v1'
APPROVAL_SHA = '0367140fbdbaef36dd59ba81030f3e35e04e78dbbed4129277c9cb0758971a80'
TYPES = {'url':'URL', 'email':'EMAIL', 'phone':'PHONE', 'credit_card':'CREDIT_CARD', 'ssn':'SSN',
         'ip_address':'IP_ADDRESS', 'messenger_handle':'MESSENGER_HANDLE', 'payment_handle':'PAYMENT_HANDLE',
         'crypto_wallet':'CRYPTO_WALLET', 'password':'PASSWORD', 'verification_code':'VERIFICATION_CODE'}
TOKEN = re.compile(r'\[(' + '|'.join(TYPES.values()) + r')_([1-9][0-9]{0,2})\]')
LIMITS = {'WITHHELD_LINKS','UNKNOWN_SPEAKER','INSUFFICIENT_EVIDENCE','HOSTILE_INPUT_STOP','PROVIDER_UNAVAILABLE',
          'PROVIDER_RESPONSE_INVALID','BUDGET_LIMIT','UNSUPPORTED_CONTENT'}
RULES = {'DEMAND_GIFT_CARD_PAYMENT','REQUEST_SECRET_DISCLOSURE','PAYMENT_WITH_SECRECY_PRESSURE','BENIGN_FIXED_TEXT'}
ACTIONS = {'avoid_link','pause_and_verify','verify_independently','review_input','use_built_in_help'}
MESSAGES = {'message.high_risk_request','message.suspicious_request','message.no_supported_finding','message.inconclusive',
            'message.provider_unavailable','message.hostile_stop','message.partial_known_threat'}


class MessageError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def require(value, code='INPUT_REJECTED'):
    if not value:
        raise MessageError(code)


def validate_intent(value):
    require(type(value) is dict and set(value) == {'entryPoint','language','target'})
    require(value['entryPoint'] == 'message' and value['language'] in ('en','es'))
    target = value['target']
    require(type(target) is dict and set(target) == {'scope','sourceType','sanitizedText','speakerRole','entities','withheldLinks','reviewedLinks'})
    require(target['scope'] == 'sanitized_message' and target['sourceType'] in ('pasted_text','ocr','mixed'))
    require(target['speakerRole'] in ('self','other','mixed','unknown'))
    text = target['sanitizedText']
    require(type(text) is str and 1 <= len(text) <= 8000 and text.strip() == text and bool(text.strip()))
    require(unicodedata.normalize('NFC',text) == text and all(unicodedata.category(c) not in ('Cf','Cs') and
            (unicodedata.category(c) != 'Cc' or c in '\n\t') for c in text), 'PRIVACY_REVIEW_REQUIRED')
    require(type(target['withheldLinks']) is bool and type(target['entities']) is list and len(target['entities']) <= 100)
    tokens = {}
    for item in target['entities']:
        require(type(item) is dict and set(item) == {'token','type'} and item['type'] in TYPES)
        token = item['token']
        match = TOKEN.fullmatch(token) if type(token) is str else None
        require(match and match.group(1) == TYPES[item['type']] and token not in tokens and token in text, 'PRIVACY_REVIEW_REQUIRED')
        tokens[token] = item['type']
    require(set(TOKEN.findall(text)) == {TOKEN.fullmatch(t).groups() for t in tokens}, 'PRIVACY_REVIEW_REQUIRED')
    for prefix in TYPES.values():
        indices=sorted(int(TOKEN.fullmatch(token).group(2)) for token in tokens if TOKEN.fullmatch(token).group(1)==prefix)
        require(indices == list(range(1,len(indices)+1)), 'PRIVACY_REVIEW_REQUIRED')
    residual = TOKEN.sub('',text)
    require('[' not in residual and ']' not in residual, 'PRIVACY_REVIEW_REQUIRED')
    sensitive = [r'[a-z][a-z0-9+.-]*\s*://', r'www\.', r'\b[^\s@]+@[^\s@]+\b', r'\b(?:\d{1,3}\.){3}\d{1,3}\b',
                 r'(?<![\w])[@$][A-Za-z0-9_]{1,64}', r'\d{4,}', r'\+?\d[\d ()-]{6,}\d', r'\b(?:[a-z0-9-]+\.)+[a-z]{2,63}\b',
                 r'\b(?:password|passcode|contraseña|c[oó]digo|secret|api[_ -]?key|token)\s*[:=]\s*\S+']
    require(not any(re.search(pattern,residual,re.IGNORECASE) for pattern in sensitive),'PRIVACY_REVIEW_REQUIRED')
    links = target['reviewedLinks']
    require(type(links) is list and len(links) <= 1)
    linked = set()
    for link in links:
        require(type(link) is dict and set(link) == {'token','url','scope','withheldComponents'})
        require(tokens.get(link['token']) == 'url' and link['token'] not in linked)
        linked.add(link['token'])
        require(link['scope'] in ('full_url','origin_only') and type(link['url']) is str and
                1 <= len(link['url'].encode('utf-8')) <= 2048 and '#' not in link['url'])
        require(type(link['withheldComponents']) is list and len(set(link['withheldComponents'])) == len(link['withheldComponents']) and
                all(c in ('path','query','fragment') for c in link['withheldComponents']))
        # Full URL validation remains the existing URL assessment boundary; only
        # HTTP(S) user-reviewed targets enter it, never a model destination.
        from urllib.parse import urlsplit
        try: parts = urlsplit(link['url'])
        except ValueError: raise MessageError('INPUT_REJECTED') from None
        require(parts.scheme in ('http','https') and parts.hostname and not parts.username and not parts.password)
        from url_redirect_resolver.resolver import parse_target, ResolutionStop
        try:parse_target(link['url'])
        except ResolutionStop:raise MessageError('PRIVACY_REVIEW_REQUIRED') from None
        if link['scope'] == 'full_url':
            require(set(link['withheldComponents']) <= {'fragment'})
        if link['scope'] == 'origin_only':
            require(parts.path in ('','/') and not parts.query and set(link['withheldComponents']) >= {'path','query'})
    require(not ({t for t,k in tokens.items() if k == 'url'} - linked) or target['withheldLinks'], 'PRIVACY_REVIEW_REQUIRED')
    return value


def validate_summary(value, client_id=None, outcome=None):
    keys = {'schemaVersion','kind','checkId','policyVersion','verdict','processingOutcome','coverage','limitationCodes','ruleIds','evidence','nextAction','messageKey'}
    require(type(value) is dict and set(value) == keys,'RESULT_SUMMARY_INVALID')
    require(type(value['schemaVersion']) is int and value['schemaVersion'] == 1 and value['kind'] == 'message_assessment' and
            value['policyVersion'] == POLICY,'RESULT_SUMMARY_INVALID')
    require(type(value['checkId']) is str and re.fullmatch('[A-Za-z0-9_-]{1,64}',value['checkId']), 'RESULT_SUMMARY_INVALID')
    require(client_id is None or value['checkId'] == client_id,'RESULT_SUMMARY_INVALID')
    require(outcome is None or value['processingOutcome'] == outcome,'RESULT_SUMMARY_INVALID')
    require(value['processingOutcome'] in ('complete','partial','blocked','unavailable','inconclusive','unsupported'), 'RESULT_SUMMARY_INVALID')
    require(value['verdict'] in ('unknown','high_risk','suspicious','no_known_threat_detected'), 'RESULT_SUMMARY_INVALID')
    require(value['coverage'] in ('supported_checks_complete','limited','not_assessed'), 'RESULT_SUMMARY_INVALID')
    require(value['nextAction'] in ACTIONS and value['messageKey'] in MESSAGES,'RESULT_SUMMARY_INVALID')
    for key,allowed,cap in [('limitationCodes',LIMITS,8),('ruleIds',RULES,4)]:
        require(type(value[key]) is list and len(value[key]) <= cap and all(type(x) is str and x in allowed for x in value[key]) and
                len(set(value[key])) == len(value[key]), 'RESULT_SUMMARY_INVALID')
    require(type(value['evidence']) is list and len(value['evidence']) <= 2,'RESULT_SUMMARY_INVALID')
    matches = False
    for evidence in value['evidence']:
        require(type(evidence) is dict and set(evidence) == {'source','outcome','targetScope'},'RESULT_SUMMARY_INVALID')
        require(evidence['source'] == 'google_web_risk_lookup' and evidence['outcome'] in ('match','no_match') and
                evidence['targetScope'] in ('full_submitted_url','origin_only','redirect_hop','observed_http_chain'),'RESULT_SUMMARY_INVALID')
        matches |= evidence['outcome'] == 'match'
    rules = set(value['ruleIds']); limits = set(value['limitationCodes']); state = value['processingOutcome']; verdict=value['verdict']
    high = matches or bool(rules & {'DEMAND_GIFT_CARD_PAYMENT','REQUEST_SECRET_DISCLOSURE'})
    if high:
        require(verdict == 'high_risk' and state in ('complete','partial'),'RESULT_SUMMARY_INVALID')
        require(value['nextAction'] == ('avoid_link' if matches else 'pause_and_verify'),'RESULT_SUMMARY_INVALID')
        require(value['messageKey'] == ('message.partial_known_threat' if matches else 'message.high_risk_request'),'RESULT_SUMMARY_INVALID')
        if matches:require(state=='partial' and bool(limits),'RESULT_SUMMARY_INVALID')
    elif 'PAYMENT_WITH_SECRECY_PRESSURE' in rules:
        require(verdict == 'suspicious' and state in ('complete','partial') and value['nextAction']=='verify_independently' and
                value['messageKey']=='message.suspicious_request','RESULT_SUMMARY_INVALID')
    elif rules == {'BENIGN_FIXED_TEXT'} and not limits:
        require(verdict == 'no_known_threat_detected' and state == 'complete' and value['nextAction']=='verify_independently' and
                value['messageKey']=='message.no_supported_finding','RESULT_SUMMARY_INVALID')
    else:
        require(verdict == 'unknown' and state in ('blocked','unavailable','inconclusive','unsupported'),'RESULT_SUMMARY_INVALID')
        if 'HOSTILE_INPUT_STOP' in limits:
            require(state=='blocked' and value['messageKey']=='message.hostile_stop' and value['nextAction']=='review_input','RESULT_SUMMARY_INVALID')
        elif limits & {'PROVIDER_UNAVAILABLE','PROVIDER_RESPONSE_INVALID','BUDGET_LIMIT'}:
            require(state=='unavailable' and value['messageKey']=='message.provider_unavailable' and value['nextAction']=='use_built_in_help','RESULT_SUMMARY_INVALID')
        else:
            require(state=='inconclusive' and value['messageKey']=='message.inconclusive' and value['nextAction']=='review_input','RESULT_SUMMARY_INVALID')
    if state=='complete':
        require(not limits and value['coverage']=='supported_checks_complete' and verdict!='unknown','RESULT_SUMMARY_INVALID')
    else:
        require(bool(limits) and value['coverage'] in ('limited','not_assessed'),'RESULT_SUMMARY_INVALID')
    require(not limits or state!='complete','RESULT_SUMMARY_INVALID')
    return value
