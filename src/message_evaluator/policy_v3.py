"""Reuse approved policy while preserving original per-link provider metadata."""
import time
from copy import deepcopy
from . import policy_v2
from shared_message_contract.validation_v3 import validate_summary, present
from shared_message_contract.runtime import fresh_url_mapper
from shared_lookup_freshness import current, wall_clock


def evaluate(check,intent,*,lookup=None,budget_ms=18000,ai=None,clock=None,now=wall_clock):
    captured=[]
    def guarded(request):
        raw=lookup(request)
        if not fresh_url_mapper().supported_private(raw,check,request['scope']):raise ValueError('INVALID_LOOKUP')
        if raw['verdict']=='high_risk' and not current(raw['lookupObservedAt'],raw['lookupValidUntil'],now()):raise ValueError('EXPIRED_LOOKUP')
        converted=deepcopy(raw);converted.pop('lookupObservedAt');converted.pop('lookupValidUntil');converted['schemaVersion']=1
        if raw['verdict']=='high_risk' or (raw['verdict']=='no_known_threat_detected' and raw['processingOutcome']=='complete'):
            captured.append({'observedAt':raw['lookupObservedAt'],'validUntil':raw['lookupValidUntil'],'threatTypes':raw['threatTypes'],
                             'freshness':'current' if raw['verdict']=='high_risk' else 'observation_only'})
        return converted
    value=policy_v2.evaluate(check,intent,lookup=guarded if lookup else None,budget_ms=budget_ms,ai=ai,clock=clock)
    if len(value['evidence'])!=len(captured):raise ValueError('EVIDENCE_MISMATCH')
    value['schemaVersion']=3
    value['evidence']=[e|meta for e,meta in zip(value['evidence'],captured)]
    return present(validate_summary(value),now())


def result(check,**kwargs):
    value=policy_v2.result(check,**kwargs);value['schemaVersion']=3
    return validate_summary(value)
