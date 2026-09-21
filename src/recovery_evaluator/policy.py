"""Approved self-exposure classification; deterministic advice, no model prose."""
import time
from shared_recovery_contract.constants import *
from shared_recovery_contract.validation import validate_intent,hostile,outcome
from .ai_provider import validate_proposal


def evaluate(check,intent,*,ai=None,budget_ms=18000,clock=time.monotonic):
    validate_intent(intent)
    require(type(budget_ms) is int and 1<=budget_ms<=18000)
    if hostile(intent['target']['sanitizedText']):return outcome(check,'blocked','HOSTILE_INPUT_STOP')
    if ai is None:return outcome(check,'unavailable','PROVIDER_UNAVAILABLE')
    deadline=clock()+budget_ms/1000
    try:
        proposal=validate_proposal(ai(intent,budget_ms))
        if clock()>=deadline:raise RecoveryError('BUDGET_LIMIT')
        if proposal['status']=='abstain':
            if proposal['context']=='suspected_injection':return outcome(check,'blocked','HOSTILE_INPUT_STOP')
            if proposal['context']=='unsupported':return outcome(check,'unsupported','UNSUPPORTED_CONTENT')
            return outcome(check,'inconclusive','INSUFFICIENT_EVIDENCE')
        ids=[x for x in EXPOSURES if x in proposal['exposureIds']]
        return outcome(check,'complete',None,ids)
    except Exception as error:
        code=getattr(error,'code','PROVIDER_UNAVAILABLE')
        return outcome(check,'unavailable',code if code in ('PROVIDER_RESPONSE_INVALID','BUDGET_LIMIT') else 'PROVIDER_UNAVAILABLE')
