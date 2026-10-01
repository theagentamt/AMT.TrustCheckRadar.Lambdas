"""Recovery builder/parser through real HTTP framing using an in-memory socket."""
import sys,json,importlib.util
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
spec=importlib.util.spec_from_file_location('recovery_fake_wire',ROOT/'tests/message_evaluator/test_proposer.py');wire=importlib.util.module_from_spec(spec);spec.loader.exec_module(wire)
from recovery_evaluator import ai_provider as ai
from shared_recovery_contract.constants import PLAYBOOK,RecoveryError
from message_evaluator import proposer

def intent():return {'entryPoint':'recovery','language':'es','target':{'scope':'recovery_clarification','sanitizedText':'Envié dinero.','entities':[],'playbookVersion':PLAYBOOK}}

def run(status=200,content=None,headers=b''):
    value={'model':wire.settings().model,'status':'completed','output':[{'type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':json.dumps(content or {'status':'classified','context':'clear','exposureIds':['sent_payment']})}]}]}
    calls=[]
    def factory(*args,**kwargs):
        assert args==('api.openai.com',) and kwargs['port']==443
        connection=wire.Connection(wire.wire(value,status,headers),*args,**kwargs);calls.append(connection);return connection
    try:
        result=proposer.propose(intent(),wire.settings(),1000,clock=lambda:0,connection_factory=factory,
            secret_loader=lambda *_:'synthetic-api-key',body_builder=ai.request_body,response_parser=lambda raw,_:ai.parse(raw,expected_model=wire.settings().model))
        return result,calls
    finally:
        for c in calls:assert c.test_socket.closed


def test_actual_recovery_wire_has_only_approved_vendor_projection():
    result,calls=run();assert result['exposureIds']==['sent_payment'] and len(calls)==1
    sent=b''.join(calls[0].test_socket.sent)
    assert sent.startswith(b'POST /v1/responses HTTP/1.1')
    body=json.loads(sent.split(b'\r\n\r\n',1)[1]);data=json.loads(body['input'][1]['content'][0]['text'])
    assert set(data)=={'untrustedRecoveryDescription','allowedExposureIds','allowedActionIds','policyVersion'}
    assert data['untrustedRecoveryDescription']=={'sanitizedText':'Envié dinero.','language':'es'}
    assert not {'account','device','entities','baseline','manualSelections','metadata','user','previous_response_id'}&set(body)
    assert body['tools']==[] and body['max_output_tokens']==256 and body['store'] is False

@pytest.mark.parametrize('status',[301,302,307,308,401,403,429,500,503])
def test_single_attempt_no_redirect_or_retry(status):
    with pytest.raises(Exception):run(status,headers=b'Location: https://example.invalid/\r\n')

@pytest.mark.parametrize('value',[{'status':'classified','context':'clear','exposureIds':['invented']},{'status':'classified','context':'clear','exposureIds':['sent_payment'],'advice':'private text'}])
def test_schema_failure_cannot_escape_as_advice(value):
    with pytest.raises(Exception):run(content=value)


def test_adapter_normalizes_secret_and_cleanup_errors(monkeypatch):
    monkeypatch.setattr(ai,'qualified_settings',wire.settings)
    def private_failure(*_a,**_k):raise RuntimeError('DO-NOT-LOG secret or description')
    monkeypatch.setattr(proposer,'propose',private_failure)
    with pytest.raises(RecoveryError) as exc:ai.assess(intent(),1000)
    assert str(exc.value)=='PROVIDER_UNAVAILABLE'
