"""Reviewed recovery projection and approved deterministic action grounding."""
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from .constants import *


def bundle():
    path=Path(__file__).parent/'playbook'/'bundle.json'
    if not path.exists():path=Path(__file__).resolve().parents[2]/'contracts/recovery-playbook/1.0/bundle.json'
    raw=path.read_bytes();require(hashlib.sha256(raw).hexdigest()==BUNDLE_SHA,'CONTRACT_UNSUPPORTED')
    value=json.loads(raw);require(value['bundleVersion']==PLAYBOOK and value['approval']=='Approved','CONTRACT_UNSUPPORTED')
    return value


def ordered_actions(exposures):
    require(type(exposures) is list and 1<=len(exposures)<=5 and all(type(x) is str and x in EXPOSURES for x in exposures)
            and len(set(exposures))==len(exposures),'PROVIDER_RESPONSE_INVALID')
    selected=set(exposures)
    if 'clicked_link' in selected:selected.add('software_or_remote_access')
    actions=sorted((a for a in bundle()['actions'] if a['exposureId'] in selected),key=lambda a:(a['phase']!='now',a['rank']))
    return [a['id'] for a in actions]


SECRET_LABEL = re.compile(r'\b(?P<label>password|passcode|contraseña|c[oó]digo|code|secret|api[_ -]?key|token)(?P<separator>\s*[:=]\s*)(?P<value>\S+)', re.I)


def privacy_validation_view(text):
    """Check explicit values before inherited residual scanning removes tokens.

    This is only a validator view: submitted, HMAC-bound and vendor text remains
    byte-for-byte unchanged. It avoids treating prose after an opaque token as a
    secret value when the inherited validator removes that token.
    """
    def checked(match):
        value=match['value'].rstrip('.,!;:)')
        prefix='VERIFICATION_CODE' if match['label'].lower() in ('code','código','codigo') else 'PASSWORD'
        require(re.fullmatch(r'\['+prefix+r'_[1-9][0-9]*\]',value) is not None,'PRIVACY_REVIEW_REQUIRED')
        return match['label']+match['separator'].replace(':',' ').replace('=',' ')+match['value']
    return SECRET_LABEL.sub(checked,text)


def validate_intent(value):
    require(type(value) is dict and set(value)=={'entryPoint','language','target'})
    require(value['entryPoint']=='recovery' and value['language'] in ('en','es'))
    target=value['target'];require(type(target) is dict and set(target)=={'scope','sanitizedText','entities','playbookVersion'})
    require(target['scope']==SCOPE and target['playbookVersion']==PLAYBOOK,'CONTRACT_UNSUPPORTED')
    text=target['sanitizedText'];require(type(text) is str and 1<=len(text)<=2000)
    require(text.strip()==text and bool(text.strip()) and unicodedata.normalize('NFC',text)==text
        and all(unicodedata.category(c) not in ('Cf','Cs') and (unicodedata.category(c)!='Cc' or c in '\n\t') for c in text),'PRIVACY_REVIEW_REQUIRED')
    try:require(len(text.encode('utf-8'))<=8000)
    except UnicodeError:raise RecoveryError('PRIVACY_REVIEW_REQUIRED') from None
    try:
        from shared_message_contract.privacy import validate_runtime_intent
        validate_runtime_intent({'entryPoint':'message','language':value['language'],'target':{
            'scope':'sanitized_message','sourceType':'pasted_text','speakerRole':'self','sanitizedText':privacy_validation_view(text),
            'entities':target['entities'],'withheldLinks':True,'reviewedLinks':[]}})
    except Exception:raise RecoveryError('PRIVACY_REVIEW_REQUIRED') from None
    bundle()
    return value


def hostile(text):
    patterns=(r'ignore\s+(?:all\s+)?previous\s+instructions',r'you\s+are\s+chatgpt',r'return\s+exactly',
              r'ignora\s+(?:las\s+)?instrucciones\s+anteriores',r'eres\s+chatgpt',r'devuelve\s+exactamente')
    return sum(bool(re.search(p,text,re.I)) for p in patterns)>=2


def outcome(check,state,reason,exposures=()):
    value={'schemaVersion':1,'kind':'recovery_clarification','checkId':check,'policyVersion':POLICY,
        'playbookVersion':PLAYBOOK,'processingOutcome':state,'reasonCode':reason,
        'exposureIds':list(exposures),'actionIds':ordered_actions(list(exposures)) if exposures else [],
        'applyRequiresConfirmation':True}
    return validate_outcome(value,check)


def validate_outcome(value,check=None,state=None):
    require(type(value) is dict and set(value)=={'schemaVersion','kind','checkId','policyVersion','playbookVersion','processingOutcome','reasonCode','exposureIds','actionIds','applyRequiresConfirmation'},'RESULT_SUMMARY_INVALID')
    require(type(value['schemaVersion']) is int and value['schemaVersion']==1 and value['kind']=='recovery_clarification'
        and value['policyVersion']==POLICY and value['playbookVersion']==PLAYBOOK
        and type(value['checkId']) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',value['checkId'])
        and (check is None or value['checkId']==check) and (state is None or value['processingOutcome']==state)
        and value['processingOutcome'] in OUTCOMES and value['applyRequiresConfirmation'] is True,'RESULT_SUMMARY_INVALID')
    if value['processingOutcome']=='complete':
        require(value['reasonCode'] is None and value['actionIds']==ordered_actions(value['exposureIds']),'RESULT_SUMMARY_INVALID')
    else:
        reasons={'inconclusive':{'INSUFFICIENT_EVIDENCE'},'partial':{'INCOMPLETE_RESPONSE'},'failed':{'PROVIDER_UNAVAILABLE'},
            'blocked':{'HOSTILE_INPUT_STOP'},'invalid_input':{'INPUT_REJECTED'},'unsupported':{'UNSUPPORTED_CONTENT'},
            'unavailable':{'PROVIDER_UNAVAILABLE','PROVIDER_RESPONSE_INVALID','BUDGET_LIMIT','SERVICE_NOT_ENABLED'}}
        require(value['exposureIds']==[] and value['actionIds']==[] and value['reasonCode'] in reasons[value['processingOutcome']],'RESULT_SUMMARY_INVALID')
    return value
