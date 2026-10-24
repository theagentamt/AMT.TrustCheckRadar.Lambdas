"""Private provider format matches existing policy; no provider requests."""
from copy import deepcopy
import itertools
import json
from pathlib import Path
import sys

import pytest
from jsonschema import Draft202012Validator

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from message_evaluator import ai_provider as ai
from shared_message_contract.validation import MessageError
from .test_ai_policy import wire, intent

STATES=list(itertools.product(
    ['warning','no_warning','abstain'],
    ['clear','insufficient','unsupported','suspected_injection','contradictory'],[False,True]))


def proposal(assessment='warning',context='clear',present=True):
    return {'assessment':assessment,'context':context,'reasons':[
        {'code':'AI_PAYMENT_PRESSURE','spans':[{'start':0,'end':4}]}] if present else []}


def provider_value(value):
    """Explicit valid fixture evidence, independent of numeric boundary fixtures."""
    return {**value,'reasons':[{'code':r['code'],'quotes':['Your']} for r in value['reasons']]}


@pytest.mark.parametrize('assessment,context,present',STATES)
def test_exhaustive_schema_parser_state_parity(assessment,context,present):
    value=proposal(assessment,context,present)
    valid=((assessment in ('warning','no_warning') and context=='clear')
           or (assessment=='abstain' and context!='clear')) and present==(assessment=='warning')
    private=provider_value(value)
    assert Draft202012Validator(ai.SCHEMA).is_valid({'result':private}) is valid
    stages=[]
    if valid:
        assert ai.parse(wire(private),'Your parcel is waiting.',on_rejection=stages.append)==value
        assert stages==[]
    else:
        with pytest.raises(MessageError):ai.parse(wire(private),'Your parcel is waiting.',on_rejection=stages.append)
        if assessment=='abstain' and context=='clear':expected='CONTEXT_ABSTAIN_WITH_CLEAR'
        elif assessment!='abstain' and context!='clear':expected='CONTEXT_NON_ABSTAIN_WITH_NON_CLEAR'
        else:expected='REASON_CONSISTENCY'
        assert stages==[expected]


def test_supported_subset_shape_and_required_closed_objects():
    Draft202012Validator.check_schema(ai.SCHEMA)
    assert ai.SCHEMA['type']=='object' and 'anyOf' not in ai.SCHEMA
    assert len(ai.SCHEMA['properties']['result']['anyOf'])==3
    def visit(node):
        if type(node) is dict:
            assert not set(node)&{'allOf','oneOf','if','then','else','not','dependentSchemas','dependentRequired'}
            if node.get('type')=='object':
                assert node['additionalProperties'] is False
                assert set(node['required'])==set(node['properties'])
            for item in node.values():visit(item)
        elif type(node) is list:
            for item in node:visit(item)
    visit(ai.SCHEMA)


@pytest.mark.parametrize('count',[0,1,5,6])
def test_warning_reason_array_bounds_remain_closed(count):
    value=proposal()
    codes=sorted(ai.AI_REASONS)
    value['reasons']=[{'code':codes[i%len(codes)],'spans':[{'start':0,'end':4}]} for i in range(count)]
    valid=count in (1,5)
    private=provider_value(value)
    assert Draft202012Validator(ai.SCHEMA).is_valid({'result':private}) is valid
    if valid:assert ai.parse(wire(private),'Your parcel is waiting.')==value
    else:
        with pytest.raises(MessageError):ai.parse(wire(private),'Your parcel is waiting.')


@pytest.mark.parametrize('count',[0,1,3,4])
def test_span_array_bounds_and_unicode_codepoints_preserved(count):
    text='🔐 Café: pay now.'
    value=proposal()
    value['reasons'][0]['spans']=[{'start':start,'end':end} for start,end in [(2,6),(8,11),(12,15),(15,16)][:count]]
    valid=count in (1,3)
    private={**value,'reasons':[{'code':value['reasons'][0]['code'],
        'quotes':['Café','pay','now','.'][:count]}]}
    assert Draft202012Validator(ai.SCHEMA).is_valid({'result':private}) is valid
    if valid:
        assert ai.validate_assessment(value,text)==value
        assert ai.parse(wire(private),text)==value
    else:
        with pytest.raises(MessageError):ai.validate_assessment(value,text)
        with pytest.raises(MessageError):ai.parse(wire(private),text)


@pytest.mark.parametrize('root',[{},[],None,{'result':None},{'result':{},'extra':'untrusted'},
    {'assessment':'no_warning','context':'clear','reasons':[]}])
def test_missing_extra_or_legacy_flat_provider_root_rejected(root):
    envelope=json.loads(wire(proposal('no_warning','clear',False)))
    envelope['output'][0]['content'][0]['text']=json.dumps(root)
    assert not Draft202012Validator(ai.SCHEMA).is_valid(root)
    with pytest.raises(MessageError):ai.parse(json.dumps(envelope).encode(),'Your parcel is waiting.')


def test_schema_revision_invalidates_historical_evidence_without_repinning():
    root=Path(__file__).resolve().parents[2]
    old=json.loads((root/'evaluation/message_ai/playbook_refinement/profile-identities.json').read_text())
    assert old['promptSha256']=='a2593d45e096b7bc257662ceda066e69abce1bbebf51f8ad2012ea4cfeb6d63f'
    assert old['schemaSha256']=='fbe75bac1c21b9b9bffe6159587f59ec55a2bbc19932763ff02c56bc23dedd45'
    assert ai.PROMPT_SHA256!=old['promptSha256'] and ai.SCHEMA_SHA256!=old['schemaSha256']
    assert json.loads((root/'src/message_evaluator/ai_qualifications.json').read_text())=={}
    assert json.loads((root/'evaluation/message_ai/controlled/approved_experiments.json').read_text())=={}


def test_private_wrapper_unwrapped_before_policy_without_changing_assessment():
    from message_evaluator.policy_v3 import evaluate
    value=proposal()
    parsed=ai.parse(wire(provider_value(value)),'Your parcel is waiting.')
    assert parsed==value and 'result' not in parsed
    expected=evaluate('same-check',intent(),ai=lambda *_:deepcopy(value),ai_primary=True)
    actual=evaluate('same-check',intent(),ai=lambda *_:deepcopy(parsed),ai_primary=True)
    assert actual==expected and actual['schemaVersion']==3
    assert actual['aiAssessmentStatus']=='warning' and 'ai_assessment' in actual['assessmentBasis']


def test_prompt_explains_consistency_without_forcing_warning_or_missing_context():
    assert 'inside the required result property' in ai.INSTRUCTIONS
    assert 'Never combine abstain with clear' in ai.INSTRUCTIONS
    assert 'not permission to infer missing' in ai.INSTRUCTIONS
    assert 'negated or preventive advice' in ai.INSTRUCTIONS
