"""Content-free diagnostics preserve the existing strict rejection boundary."""
import json
import pytest
from message_evaluator import ai_provider as ai
from shared_message_contract.validation import MessageError
from .test_normal_usage import COST, INTENT, TEXT, response, transport


@pytest.mark.parametrize('change,stage',[
    (lambda r:r.update(status='incomplete'),'ENVELOPE_STATUS'),
    (lambda r:r.update(model='untrusted-secret-like-model'),'MODEL_IDENTITY'),
    (lambda r:r.update(output=[]),'OUTPUT_SHAPE'),
    (lambda r:r['output'][0].update(role='untrusted-content'),'MESSAGE_SHAPE'),
    (lambda r:r['output'][0]['content'][0].update(type='refusal'),'CONTENT_SHAPE'),
    (lambda r:r['output'][0]['content'][0].update(text='untrusted not-json'),'ASSESSMENT_JSON'),
    (lambda r:r['output'][0]['content'][0].update(text=json.dumps({'untrusted-key':'private-content'})),'ASSESSMENT_SHAPE'),
    (lambda r:r['output'][0]['content'][0].update(text=json.dumps({'assessment':'abstain','context':'clear','reasons':[]})),'CONTEXT_CONSISTENCY'),
    (lambda r:r['output'][0]['content'][0].update(text=json.dumps({'assessment':'warning','context':'clear','reasons':[]})),'REASON_CONSISTENCY'),
])
def test_exact_closed_stage_and_unchanged_public_error(change,stage,capsys):
    raw=response();change(raw);observed=[]
    with pytest.raises(MessageError) as error:
        ai.parse(json.dumps(raw).encode(),TEXT,expected_model=COST.model,on_rejection=observed.append)
    assert error.value.code=='PROVIDER_RESPONSE_INVALID' and observed==[stage]
    assert not capsys.readouterr().out  # Direct parsing does not create logging side effects.


@pytest.mark.parametrize('raw,stage',[(b'','RESPONSE_BOUNDS'),(b'not-json','ENVELOPE_JSON'),
    (b'{"status":"completed","status":"private-duplicate"}','ENVELOPE_JSON')])
def test_bounds_and_duplicate_json_are_classified_without_values(raw,stage):
    observed=[]
    with pytest.raises(MessageError):ai.parse(raw,TEXT,on_rejection=observed.append)
    assert observed==[stage]


def test_normal_logging_retains_usage_but_no_rejected_content(monkeypatch,capsys):
    raw=response()
    raw['output'][0]['content'][0]['text']=json.dumps({'assessment':'abstain','context':'clear','reasons':[]})
    raw['secret-like-extra']='should-never-enter-logs'
    calls=transport(monkeypatch,raw)
    with pytest.raises(MessageError):ai.assess(INTENT,1000,cost_policy=COST)
    output=capsys.readouterr().out
    records=[json.loads(row) for row in output.splitlines()]
    assert len(calls)==1 and [r['event'] for r in records]==['message_ai_usage','message_ai_rejection']
    assert records[1]=={'event':'message_ai_rejection','stage':'CONTEXT_CONSISTENCY'}
    assert TEXT not in output and 'should-never-enter-logs' not in output and 'assessment' not in output


def test_success_is_identical_and_diagnostic_failure_cannot_change_rejection():
    raw=json.dumps(response()).encode();stages=[]
    assert ai.parse(raw,TEXT,on_rejection=stages.append)==ai.parse(raw,TEXT)
    assert stages==[]
    def broken(_):raise RuntimeError('private diagnostic error')
    with pytest.raises(MessageError) as error:ai.parse(b'broken',TEXT,on_rejection=broken)
    assert error.value.code=='PROVIDER_RESPONSE_INVALID'


@pytest.mark.parametrize('stage',['raw secret',{},None,['OUTPUT_SHAPE']])
def test_reporter_never_echoes_untrusted_stage(stage,capsys):
    ai.report_rejection(stage)
    assert json.loads(capsys.readouterr().out)=={'event':'message_ai_rejection','stage':'PARSER_INTERNAL'}


@pytest.mark.parametrize('reason,text,stage',[
    ({'code':'untrusted-code','spans':[{'start':0,'end':4}]},TEXT,'REASON_SHAPE'),
    ({'code':'AI_PAYMENT_PRESSURE','spans':[{'start':0,'end':9000}]},TEXT,'SPAN_SHAPE'),
    ({'code':'AI_PAYMENT_PRESSURE','spans':[{'start':0,'end':3}]},'123','SPAN_CONTENT'),
])
def test_reason_and_span_stages_do_not_expose_values(reason,text,stage):
    raw=response()
    raw['output'][0]['content'][0]['text']=json.dumps({'assessment':'warning','context':'clear','reasons':[reason]})
    stages=[]
    with pytest.raises(MessageError):ai.parse(json.dumps(raw).encode(),text,on_rejection=stages.append)
    assert stages==[stage]
