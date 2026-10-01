"""Qualification identity: executable source and exact request template, no registry."""
import hashlib
import json
from pathlib import Path

# Roots work identically in source and packaged Lambda layouts. No evidence file or
# qualification registry is included, avoiding a self-referential approval hash.
FILES=('recovery_evaluator/ai_provider.py','recovery_evaluator/profile.py','recovery_evaluator/policy.py',
       'message_evaluator/proposer.py','shared_recovery_contract/validation.py',
       'shared_recovery_contract/constants.py','shared_message_contract/privacy.py',
       'shared_message_contract/validation.py','shared_message_contract/runtime.py')


def identity(settings):
    from .ai_provider import request_body
    from shared_recovery_contract.constants import PLAYBOOK
    root=Path(__file__).resolve().parent.parent
    source={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in FILES}
    template=request_body({'entryPoint':'recovery','language':'en','target':{
        'scope':'recovery_clarification','sanitizedText':'I sent money.','entities':[],'playbookVersion':PLAYBOOK}},settings)
    value={'schemaVersion':1,'source':source,'requestTemplate':template,
           'timeoutMs':settings.timeout_ms,'supportedLanguages':['en','es'],
           'inputCodepoints':2000,'inputUtf8Bytes':8000,'wireUtf8Bytes':32768}
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
