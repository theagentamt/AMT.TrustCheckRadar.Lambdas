"""Offline exact-evidence boundaries; no model, key or network calls."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import threading

import pytest
from jsonschema import Draft202012Validator

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from message_evaluator import ai_provider as ai
from shared_message_contract.validation import MessageError


def provider(quotes, *, code='AI_PAYMENT_PRESSURE'):
    return {'assessment':'warning','context':'clear','reasons':[{'code':code,'quotes':quotes,'support':{role:0 for role in ai.EVIDENCE_ROLES[code]}}]}


def wire(value):
    return json.dumps({'model':'fixture-2026-01-01','status':'completed','output':[
        {'type':'message','role':'assistant','status':'completed','content':[
            {'type':'output_text','text':json.dumps({'result':value},ensure_ascii=False)}]}]},ensure_ascii=False).encode()


def rejected(value,text,stage):
    stages=[]
    with pytest.raises(MessageError) as error:
        ai.parse(wire(value),text,expected_model='fixture-2026-01-01',on_rejection=stages.append)
    assert error.value.code=='PROVIDER_RESPONSE_INVALID' and stages==[stage]


@pytest.mark.parametrize('text,quote,start,end',[
    ('🔐 Café: pay now.','pay',8,11),
    ('🔐 Café: pay now.','Café',2,6),
    ('mañana\tpaga\nahora','paga\nahora',7,17),
    ('payment','pay',0,3),  # Same literal-substring boundary as prior numeric spans.
])
def test_exact_quotes_derive_codepoints_without_changing_reviewed_text(text,quote,start,end):
    value=provider([quote]);before=deepcopy(value)
    assert Draft202012Validator(ai.SCHEMA).is_valid({'result':value})
    actual=ai.parse(wire(value),text,expected_model='fixture-2026-01-01')
    assert actual=={'assessment':'warning','context':'clear','reasons':[
        {'code':'AI_PAYMENT_PRESSURE','spans':[{'start':start,'end':end}]}]}
    assert value==before and text[start:end]==quote


@pytest.mark.parametrize('text,quote',[
    ('pay now','Pay now'),('pay now',' pay now'),('pay now','pay now '),
    ('pay  now','pay now'),('pay now','pay\nnow'),('pay now','pay\u00a0now'),
    ('Café','Cafe\u0301'),('Pague hoy.','Pague hoy!'),
])
def test_no_trim_casefold_whitespace_punctuation_or_unicode_repair(text,quote):
    # Oversized variants fail before search; neither rejection normalizes data.
    rejected(provider([quote]),text,'EVIDENCE_TEXT_BOUNDS' if len(quote)>len(text) else 'EVIDENCE_NOT_FOUND')


@pytest.mark.parametrize('text,quote',[
    ('pay now; pay now','pay now'),('aaa','aa'),('ababa','aba'),
])
def test_repeated_including_overlapping_matches_are_ambiguous(text,quote):
    rejected(provider([quote]),text,'EVIDENCE_AMBIGUOUS')


@pytest.mark.parametrize('quote',[None,True,4,{},[]])
def test_evidence_requires_a_literal_string(quote):
    rejected(provider([quote]),'pay now','EVIDENCE_TEXT_TYPE')


@pytest.mark.parametrize('quote',['','x'*8001])
def test_empty_or_oversized_evidence_is_bounded(quote):
    rejected(provider([quote]),'pay now','EVIDENCE_TEXT_BOUNDS')


@pytest.mark.parametrize('quote',['[URL_1]','URL','URL_1','[URL','1]','...','123'])
def test_placeholder_fragments_punctuation_and_digits_cannot_ground_a_reason(quote):
    rejected(provider([quote]),'Visit [URL_1] ... 123','SPAN_CONTENT')


def test_placeholder_with_actual_visible_context_is_not_blanket_rejected():
    value=ai.parse(wire(provider(['[URL_1] now'])),'Visit [URL_1] now')
    assert value['reasons'][0]['spans']==[{'start':6,'end':17}]


@pytest.mark.parametrize('quotes',[
    ['today','transfer'],['transfer today','today now'],['transfer','transfer']])
def test_no_sorting_overlapping_or_duplicate_evidence_repair(quotes):
    rejected(provider(quotes),'transfer today now','SPAN_ORDER_OR_OVERLAP')


@pytest.mark.parametrize('quotes,relation',[
    (['bravo','alpha'],'REVERSED_DISJOINT'),
    (['alpha','alpha'],'EXACT_DUPLICATE'),
    (['alpha bravo','bravo'],'CURRENT_WITHIN_PREVIOUS'),
    (['alpha bravo','alpha'],'CURRENT_WITHIN_PREVIOUS'),
    (['bravo','alpha bravo'],'CURRENT_CONTAINS_PREVIOUS'),
    (['alpha','alpha bravo'],'CURRENT_CONTAINS_PREVIOUS'),
    (['alpha bravo','bravo charlie'],'PARTIAL_OVERLAP'),
    (['bravo charlie','alpha bravo'],'PARTIAL_OVERLAP'),
])
def test_rejection_detail_distinguishes_relations_without_retaining_evidence(quotes,relation):
    stages=[];details=[]
    with pytest.raises(MessageError) as error:
        ai.parse(wire(provider(quotes)),'alpha bravo charlie',on_rejection=stages.append,
                 on_rejection_detail=details.append)
    assert error.value.code=='PROVIDER_RESPONSE_INVALID' and stages==['SPAN_ORDER_OR_OVERLAP']
    assert details==[{'reasonCode':'AI_PAYMENT_PRESSURE','spanRelation':relation}]


def test_normalized_boundary_reports_only_first_violating_adjacent_pair_and_its_reason():
    value={'assessment':'warning','context':'clear','reasons':[
        {'code':'AI_PAYMENT_PRESSURE','spans':[{'start':0,'end':5}]},
        {'code':'AI_PRETEXT','spans':[{'start':0,'end':5},{'start':6,'end':11},{'start':6,'end':11}]},
    ]}
    details=[]
    with pytest.raises(MessageError):
        ai.validate_assessment(value,'alpha bravo charlie',on_rejection_detail=details.append)
    assert details==[{'reasonCode':'AI_PRETEXT','spanRelation':'EXACT_DUPLICATE'}]


def test_detail_observer_failure_cannot_change_rejection_or_log_private_data(capsys):
    def broken(_):raise RuntimeError('private observer exception')
    with pytest.raises(MessageError) as error:
        ai.parse(wire(provider(['alpha','alpha'])),'alpha bravo',on_rejection_detail=broken)
    assert error.value.code=='PROVIDER_RESPONSE_INVALID' and capsys.readouterr().out==''


def test_rejection_detail_is_closed_and_does_not_replace_generic_stage(monkeypatch,capsys):
    monkeypatch.setattr(ai,'_span_relation',lambda *_:'untrusted non-enum diagnostic')
    details=[];stages=[]
    with pytest.raises(MessageError) as error:
        ai.parse(wire(provider(['alpha','alpha'])),'alpha bravo',on_rejection=stages.append,
                 on_rejection_detail=details.append)
    assert error.value.code=='PROVIDER_RESPONSE_INVALID' and stages==['SPAN_ORDER_OR_OVERLAP']
    assert details==[] and capsys.readouterr().out==''


def test_non_span_rejection_and_valid_response_have_no_span_detail():
    details=[]
    with pytest.raises(MessageError):
        ai.parse(wire(provider(['missing'])),'alpha bravo',on_rejection_detail=details.append)
    ai.parse(wire(provider(['alpha'])),'alpha bravo',on_rejection_detail=details.append)
    assert details==[]


def test_same_exact_passage_can_support_distinct_categories_as_before():
    value=provider(['pay now']);value['reasons'].append({'code':'AI_CONSEQUENTIAL_URGENCY','quotes':['pay now'],'support':{'requestedAction':0,'urgency':0}})
    actual=ai.parse(wire(value),'pay now')
    assert [r['spans'] for r in actual['reasons']]==[[{'start':0,'end':7}]]*2


def test_legacy_numeric_provider_wire_is_not_an_alternate_path():
    value={'assessment':'warning','context':'clear','reasons':[
        {'code':'AI_PAYMENT_PRESSURE','spans':[{'start':0,'end':3}]}]}
    rejected(value,'pay now','REASON_SHAPE')


def normalized(span):
    return {'assessment':'warning','context':'clear','reasons':[{'code':'AI_PAYMENT_PRESSURE','spans':[span]}]}


@pytest.mark.parametrize('spans,stage',[
    ([None],'SPAN_OBJECT_SHAPE'),([{'start':0,'end':3,'extra':0}],'SPAN_OBJECT_SHAPE'),
    ([{'start':True,'end':3}],'SPAN_OFFSET_TYPE'),([{'start':0.0,'end':3}],'SPAN_OFFSET_TYPE'),
    ([{'start':-1,'end':3}],'SPAN_START_NEGATIVE'),([{'start':1,'end':1}],'SPAN_EMPTY_OR_REVERSED'),
    ([{'start':3,'end':1}],'SPAN_EMPTY_OR_REVERSED'),([{'start':0,'end':999}],'SPAN_END_OUT_OF_BOUNDS'),
    ([{'start':0,'end':5},{'start':3,'end':7}],'SPAN_ORDER_OR_OVERLAP'),
])
def test_shared_normalized_validator_has_finite_precise_guards(spans,stage):
    value=normalized({});value['reasons'][0]['spans']=spans;stages=[]
    with pytest.raises(MessageError):ai.validate_assessment(value,'pay now',on_rejection=stages.append)
    assert stages==[stage]


def test_normalized_callback_output_is_revalidated_and_detached():
    value=normalized({'start':0,'end':3});result=ai.validate_assessment(value,'pay now')
    value['reasons'][0]['spans'][0]['end']=999
    assert result['reasons'][0]['spans'][0]['end']==3
    with pytest.raises(MessageError):ai.validate_assessment(provider(['pay']),'pay now')


def test_callback_mutation_after_span_checks_cannot_change_validated_snapshot(monkeypatch):
    value=normalized({'start':0,'end':3});checked=threading.Event();mutated=threading.Event()
    original=ai._check
    def checkpoint(condition,stage):
        original(condition,stage)
        if stage=='SPAN_CONTENT':
            checked.set();assert mutated.wait(2)
    def mutate():
        if checked.wait(2):value['reasons'][0]['spans'][0]['end']=999
        mutated.set()
    monkeypatch.setattr(ai,'_check',checkpoint)
    thread=threading.Thread(target=mutate);thread.start()
    try:result=ai.validate_assessment(value,'pay now')
    finally:thread.join(timeout=2)
    assert not thread.is_alive() and value['reasons'][0]['spans'][0]['end']==999
    assert result['reasons'][0]['spans'][0]['end']==3


def test_finite_diagnostics_never_include_evidence_offsets_or_arbitrary_errors(capsys):
    with pytest.raises(MessageError):
        ai.parse(wire(provider(['untrusted-text'])),'pay now',on_rejection=ai.report_rejection)
    assert json.loads(capsys.readouterr().out)=={'event':'message_ai_rejection','stage':'EVIDENCE_TEXT_BOUNDS'}
    def broken(_):raise RuntimeError('untrusted-private-detail')
    with pytest.raises(MessageError) as error:ai.validate_assessment(normalized({'start':0,'end':999}),'pay now',on_rejection=broken)
    assert error.value.code=='PROVIDER_RESPONSE_INVALID' and capsys.readouterr().out==''


def test_exact_provider_to_policy_projection_preserves_public_candidate3():
    from message_evaluator.policy_v3 import evaluate
    from .test_ai_policy import intent
    request=intent('Pay this fee today.');parsed=ai.parse(wire(provider(['Pay this fee today.'])),request['target']['sanitizedText'])
    actual=evaluate('same-check',request,ai=lambda *_:parsed,ai_primary=True)
    assert actual['schemaVersion']==3 and actual['aiAssessmentStatus']=='warning'
    assert actual['aiReasonCodes']==['AI_PAYMENT_PRESSURE']
    assert not {'quotes','spans','sanitizedText','result'} & set(actual)
