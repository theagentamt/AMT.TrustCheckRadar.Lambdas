import copy
import json
from pathlib import Path
import sys
import pytest
from jsonschema import Draft202012Validator
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from shared_message_contract.validation import validate_intent, validate_summary, MessageError
from message_evaluator.policy import evaluate, result, COVERAGE
from message_evaluator import app

CONTRACT=Path(__file__).resolve().parents[2]/'contracts/message-consumer/1.0.0-candidate.1'

def intent(text,role='other',language='en'):
    return {'entryPoint':'message','language':language,'target':{'scope':'sanitized_message','sourceType':'pasted_text','sanitizedText':text,'speakerRole':role,'entities':[],'withheldLinks':False,'reviewedLinks':[]}}

@pytest.mark.parametrize('text',['Contact @alice','Send payment to $alice','Visit https://example.com','Call 555-123-4567','email alice@example.com','password: hidden','[URL_1]','hidden\u200bvalue'])
def test_privacy_rejection_before_lookup(text):
    calls=[]
    with pytest.raises(MessageError):evaluate('check',intent(text),lookup=lambda e:calls.append(e))
    assert not calls

@pytest.mark.parametrize('language,text,rule',[(lang,text,rule) for lang,rows in COVERAGE.items() for text,rule in rows.items()])
def test_only_qualified_templates_complete(language,text,rule):
    outcome=evaluate('check',intent(text,language=language))
    assert outcome['ruleIds']==[rule] and outcome['processingOutcome']=='complete'
    Draft202012Validator(json.loads((CONTRACT/'outcome.schema.json').read_text())).validate(outcome)
    changed=evaluate('check',intent('Quoted example: '+text,language=language))
    assert changed['processingOutcome']=='inconclusive' and changed['verdict']=='unknown'

@pytest.mark.parametrize('role',['mixed','unknown','self'])
def test_risky_rule_requires_other_role(role):
    outcome=evaluate('check',intent(next(iter(COVERAGE['en'])),role))
    assert outcome['processingOutcome']=='inconclusive'

@pytest.mark.parametrize('url,scope,withheld',[('https://example.com/?token=secret','full_url',[]),('https://example.com/reset/value','full_url',[]),('https://example.com/a','full_url',['path']),('https://example.com/a','origin_only',['path','query'])])
def test_invalid_link_projection_never_lookup(url,scope,withheld):
    request=intent('Review [URL_1]');request['target'].update(entities=[{'token':'[URL_1]','type':'url'}],reviewedLinks=[{'token':'[URL_1]','url':url,'scope':scope,'withheldComponents':withheld}])
    calls=[]
    with pytest.raises(MessageError):evaluate('check',request,lookup=lambda e:calls.append(e))
    assert not calls

@pytest.mark.parametrize('limits',[['HOSTILE_INPUT_STOP'],['PROVIDER_UNAVAILABLE'],['INSUFFICIENT_EVIDENCE'],['BUDGET_LIMIT']])
def test_independent_threat_survives_every_limitation(limits):
    outcome=result('check',limits=limits,evidence=[{'source':'google_web_risk_lookup','outcome':'match','targetScope':'observed_http_chain'}])
    assert outcome['verdict']=='high_risk' and outcome['processingOutcome']=='partial'
    assert outcome['messageKey']=='message.partial_known_threat'
    Draft202012Validator(json.loads((CONTRACT/'outcome.schema.json').read_text())).validate(outcome)

@pytest.mark.parametrize('mutation',[{'messageKey':'message.high_risk_request','verdict':'high_risk','nextAction':'pause_and_verify'},{'messageKey':'message.suspicious_request','verdict':'suspicious','nextAction':'verify_independently'},{'limitationCodes':['HOSTILE_INPUT_STOP','INSUFFICIENT_EVIDENCE']}])
def test_summary_and_schema_reject_invented_or_hidden_findings(mutation):
    value=result('check',limits=['INSUFFICIENT_EVIDENCE']);value.update(mutation)
    with pytest.raises(Exception):validate_summary(value)
    assert list(Draft202012Validator(json.loads((CONTRACT/'outcome.schema.json').read_text())).iter_errors(value))

def test_disabled_evaluator_does_not_touch_content(monkeypatch,capsys):
    monkeypatch.delenv('MESSAGE_EVALUATOR_ENABLED',raising=False)
    monkeypatch.setattr(app,'evaluate',lambda *a,**k:pytest.fail('evaluated disabled request'))
    assert app.lambda_handler({'sensitive':'not logged'},None)=={'enabled':False}
    assert not capsys.readouterr().out
