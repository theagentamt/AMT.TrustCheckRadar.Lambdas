"""OFFLINE contract reference. No transport, credentials, authority or persistence.

Structural grounding is not proof that a model inferred the right exposure.
The default public boundary stays disabled. simulate() requires an injected test
callback and never applies suggestions to the independently approved local plan.
"""
import copy
import importlib.util
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator
from shared_message_contract.privacy import validate_runtime_intent

ROOT = Path(__file__).parent
SELECTION_DIR = ROOT.parents[1] / 'recovery-selection' / '1.0.0'
spec = importlib.util.spec_from_file_location('recovery_selection_reference', SELECTION_DIR/'reference_selector.py')
selection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selection)
VERSION = 'recovery-clarification-0.1.0-candidate.1'


class ContractError(ValueError):
    pass


def read(name):
    return json.loads((ROOT/name).read_text())


def validate(schema, value, code):
    if not Draft202012Validator(read(schema)).is_valid(value):
        raise ContractError(code)
    return value


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError('invalid_json')
        result[key] = value
    return result


def decode(raw, limit):
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= limit:
            raise ValueError()
        return json.loads(raw.decode('utf-8'), object_pairs_hook=unique_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except Exception:
        raise ContractError('invalid_json') from None


def validate_request(raw):
    try:
        value = validate('request.schema.json', decode(raw, 32768), 'input_invalid')
        if len(value['sanitizedDescription'].encode('utf-8')) > 8000:
            raise ContractError('input_invalid')
        # Reuse scalar, NFC, placeholder and residual-identifier privacy checks
        # only. This is not message authority, risk classification or its prompt.
        validate_runtime_intent({'entryPoint':'message','language':value['language'],'target':{
            'scope':'sanitized_message','sourceType':'pasted_text','speakerRole':'self',
            'sanitizedText':value['sanitizedDescription'],'entities':value['entities'],
            'withheldLinks':any(e['type']=='url' for e in value['entities']),'reviewedLinks':[]}})
    except ContractError:
        raise ContractError('input_invalid') from None
    except Exception:
        raise ContractError('privacy_rejected') from None
    # Bounded engineering stop heuristic, not complete injection detection.
    patterns=(r'ignore\s+(?:all\s+)?previous\s+instructions',r'you\s+are\s+chatgpt',
              r'return\s+exactly',r'ignora\s+(?:las\s+)?instrucciones\s+anteriores',
              r'eres\s+chatgpt',r'devuelve\s+exactamente')
    if sum(bool(re.search(p,value['sanitizedDescription'],re.I)) for p in patterns)>=2:
        raise ContractError('hostile_input')
    return value


def validate_proposal(raw):
    try:
        value=validate('model-output.schema.json',decode(raw,4096),'output_invalid')
        status=value['status']; context=value['context']
        exposures=value['exposureIds']; actions=value['actionIds']; questions=value['questionIds']
        if status=='classified':
            if context!='clear' or not exposures or questions:
                raise ValueError()
            expected=selection.ordered_actions({'contractVersion':selection.VERSION,'language':'en',
                                                'exposures':exposures,'unsure':False})
            if actions!=expected:
                raise ValueError()
        elif status=='needs_confirmation':
            if context!='insufficient' or exposures or actions or questions:
                raise ValueError()
        elif context=='clear' or exposures or actions or questions:
            raise ValueError()
        return value
    except Exception:
        raise ContractError('output_invalid') from None


def model_projection(request):
    """Draft minimal projection for fake tests; not an executable provider request."""
    return {'language':request['language'], 'sanitizedDescription':request['sanitizedDescription'],
            'allowedExposureIds':list(selection.EXPOSURES),
            'allowedActionIds':list(selection.ACTION_PRESENTATION_ORDER),
            'classificationPolicyVersion':'recovery-classification-draft-1'}


def baseline_copy(baseline):
    try:
        schema=json.loads((SELECTION_DIR/'selection-output.schema.json').read_text())
        Draft202012Validator(schema).validate(baseline)
        return copy.deepcopy(baseline)
    except Exception:
        raise ContractError('invalid_baseline') from None


def result(baseline, state, reason, proposal=None):
    value={'contractVersion':VERSION,'state':state,'reason':reason,
           'suggestedExposureIds':proposal['exposureIds'] if proposal else [],
           'suggestedActionIds':proposal['actionIds'] if proposal else [],
           'questionIds':proposal['questionIds'] if proposal else [],
           'baselinePlan':baseline_copy(baseline),'baselineDisposition':'unchanged',
           'authorityAccounting':'not_exercised','providerCalls':0}
    return validate('offline-result.schema.json',value,'invalid_result')


def evaluate_disabled(baseline):
    """No enable override, callback, natural-language input or provider path."""
    return result(baseline,'unavailable','disabled')


def simulate(raw, baseline, *, fake_model, cancelled=False):
    """Engineering-only injected callback; no accuracy/quality or charging claim."""
    baseline=baseline_copy(baseline)
    if type(cancelled) is not bool or not callable(fake_model):
        raise ContractError('invalid_simulator')
    if cancelled:
        return result(baseline,'cancelled','cancelled')
    if baseline['state'] not in ('available','review_due'):
        return result(baseline,'unavailable','baseline_unavailable')
    try:
        request=validate_request(raw)
    except ContractError as error:
        return result(baseline,'rejected',str(error))
    try:
        output=fake_model(model_projection(request))
    except Exception:
        return result(baseline,'unavailable','simulator_failed')
    try:
        proposal=validate_proposal(output)
    except ContractError:
        return result(baseline,'rejected','output_invalid')
    if proposal['status']=='classified':
        return result(baseline,'offline_proposal',None,proposal)
    return result(baseline,'uncertain',proposal['status'],proposal)
