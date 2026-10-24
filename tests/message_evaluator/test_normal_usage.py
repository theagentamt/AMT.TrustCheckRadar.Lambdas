"""Normal adapter usage/cost controls; all transport boundaries are local fakes."""
import json,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from message_evaluator import ai_provider,app
from shared_message_contract.provider_cost import CostPolicy
from shared_message_contract.validation import MessageError
from shared_message_contract import validation_v2 as v2

COST=CostPolicy('fixture-model-2026-01-01',1024,512,2,4)
TEXT='A gift card can be a birthday present.'
INTENT={'entryPoint':'message','language':'en','target':{'scope':'sanitized_message','sourceType':'pasted_text',
    'sanitizedText':TEXT,'speakerRole':'other','entities':[],'reviewedLinks':[],'withheldLinks':False}}


def response():
    return {'model':COST.model,'service_tier':'default','status':'completed',
        'usage':{'input_tokens':100,'output_tokens':20,'total_tokens':120,
                 'input_tokens_details':{'cached_tokens':10},'output_tokens_details':{'reasoning_tokens':0}},
        'output':[{'type':'message','status':'completed','role':'assistant','content':[{'type':'output_text',
            'text':json.dumps({'assessment':'no_warning','context':'clear','reasons':[]})}]}]}


def transport(monkeypatch,raw):
    settings=SimpleNamespace(model=COST.model,max_output_tokens=COST.maxOutputTokens)
    monkeypatch.setattr(ai_provider,'qualified_settings',lambda cost:settings)
    calls=[]
    def fake(intent,config,ms,**kwargs):
        body=kwargs['body_builder'](intent,config)
        assert body['store'] is False and body['tools']==[] and body['service_tier']=='default'
        assert body['truncation']=='disabled' and body['max_output_tokens']==512
        calls.append(body)
        return kwargs['response_parser'](json.dumps(raw).encode(),TEXT)
    monkeypatch.setattr(ai_provider.proposer,'propose',fake)
    return calls


def test_normal_adapter_reports_numeric_usage_without_content(monkeypatch,capsys):
    raw=response();raw['usage']['future_field']='not retained'
    calls=transport(monkeypatch,raw)
    assert ai_provider.assess(INTENT,1000,cost_policy=COST)['assessment']=='no_warning'
    report=json.loads(capsys.readouterr().out)
    assert len(calls)==1 and report=={'event':'message_ai_usage','failureReason':None,
        'usage':{'inputTokens':100,'outputTokens':20,'cachedTokens':10,'reasoningTokens':0,'tokenCostUpperNano':280}}
    assert TEXT not in json.dumps(report) and 'future_field' not in json.dumps(report)


@pytest.mark.parametrize('change,reason',[
    ({'usage':None},'USAGE_ABSENT'),({'model':'other'},'MODEL_MISMATCH'),
    ({'service_tier':'priority'},'SERVICE_TIER_MISMATCH'),
    ({'usage':{'input_tokens':1025,'output_tokens':20,'total_tokens':1045,
      'input_tokens_details':{'cached_tokens':0},'output_tokens_details':{'reasoning_tokens':0}}},'COST_BOUND_EXCEEDED')])
def test_unqualified_usage_is_unknown_not_zero_or_a_success(monkeypatch,capsys,change,reason):
    calls=transport(monkeypatch,response()|change)
    with pytest.raises(MessageError):ai_provider.assess(INTENT,1000,cost_policy=COST)
    assert len(calls)==1 and json.loads(capsys.readouterr().out)=={
        'event':'message_ai_usage','usage':None,'failureReason':reason}


def test_transport_uncertainty_reports_unknown_without_retry(monkeypatch,capsys):
    transport(monkeypatch,response());calls=[]
    def uncertain(*args,**kwargs):calls.append(1);raise TimeoutError()
    monkeypatch.setattr(ai_provider.proposer,'propose',uncertain)
    with pytest.raises(TimeoutError):ai_provider.assess(INTENT,1000,cost_policy=COST)
    assert calls==[1] and json.loads(capsys.readouterr().out)['failureReason']=='RESPONSE_UNAVAILABLE'


def test_evaluator_cost_mismatch_stops_before_provider(monkeypatch):
    for key,value in {'STAGE':'dev','MESSAGE_EVALUATOR_ENABLED':'true','MESSAGE_AI_ENABLED':'true',
        'MESSAGE_AI_QUALIFIED':'true','MESSAGE_AI_PRIMARY_ENABLED':'true','MESSAGE_AI_POLICY_VERSION':v2.POLICY,
        'MESSAGE_AI_POLICY_APPROVAL_SHA256':v2.APPROVAL_SHA,'MESSAGE_AI_COST_POLICY':json.dumps(COST.as_dict())}.items():monkeypatch.setenv(key,value)
    monkeypatch.setattr(ai_provider,'assess',lambda *a,**k:pytest.fail('provider boundary reached'))
    event={'schemaVersion':3,'checkId':'check','policyVersion':v2.POLICY,'intent':INTENT,
        'executionBudgetMs':1000,'runtimeMode':'ai_primary_v1','providerCost':COST.as_dict()|{'inputNanoUsdPerToken':3}}
    assert app.lambda_handler(event,SimpleNamespace(get_remaining_time_in_millis=lambda:2000))=={'enabled':True,'errorCode':'EVALUATOR_UNAVAILABLE'}


def test_cost_policy_must_be_bound_to_actual_qualification_record(monkeypatch,tmp_path):
    import hashlib
    root=Path(__file__).resolve().parents[2]
    contract=(root/'contracts/message-consumer/1.0.0-candidate.2/SHA256SUMS').read_bytes()
    (tmp_path/'ai_contract').mkdir();(tmp_path/'ai_contract/SHA256SUMS').write_bytes(contract)
    (tmp_path/'qualifications').mkdir();evidence=b'{"syntheticUnitFixture":true}'
    (tmp_path/'qualifications/fixture.json').write_bytes(evidence)
    record={'model':COST.model,'policyVersion':v2.POLICY,'promptSha256':ai_provider.PROMPT_SHA256,
        'schemaSha256':ai_provider.SCHEMA_SHA256,'contractSha256':hashlib.sha256(contract).hexdigest(),
        'evidenceSha256':hashlib.sha256(evidence).hexdigest(),'status':'approved','costPolicy':COST.as_dict()}
    path=tmp_path/'ai_qualifications.json';path.write_text(json.dumps({'fixture':record}))
    monkeypatch.setattr(ai_provider,'__file__',str(tmp_path/'ai_provider.py'))
    for key,value in {'STAGE':'dev','MESSAGE_AI_ENABLED':'true','MESSAGE_AI_QUALIFIED':'true',
        'MESSAGE_AI_QUALIFICATION_ID':'fixture','MESSAGE_AI_POLICY_VERSION':v2.POLICY,
        'MESSAGE_AI_POLICY_APPROVAL_SHA256':v2.APPROVAL_SHA,'MESSAGE_PROPOSER_MODEL':COST.model,
        'MESSAGE_PROPOSER_MAX_OUTPUT_TOKENS':'512','MESSAGE_PROPOSER_TIMEOUT_MS':'1000',
        'MESSAGE_PROPOSER_SECRET_ARN':'arn:aws:secretsmanager:us-east-1:107827791950:secret:trustcheckradar/dev/openai-ABC123'}.items():monkeypatch.setenv(key,value)
    assert ai_provider.qualified_settings(COST).model==COST.model
    for change in ({},COST.as_dict()|{'contextTokens':1025},COST.as_dict()|{'inputNanoUsdPerToken':True}):
        path.write_text(json.dumps({'fixture':record|{'costPolicy':change}}))
        with pytest.raises(MessageError):ai_provider.qualified_settings(COST)


def test_legacy_paid_proposer_cannot_bypass_normal_currency_policy(monkeypatch):
    from shared_message_contract import POLICY,APPROVAL_SHA
    for key,value in {'STAGE':'dev','MESSAGE_EVALUATOR_ENABLED':'true','MESSAGE_POLICY_VERSION':POLICY,
        'MESSAGE_POLICY_APPROVAL_SHA256':APPROVAL_SHA,'MESSAGE_PROPOSER_ENABLED':'true'}.items():monkeypatch.setenv(key,value)
    monkeypatch.setattr(ai_provider.proposer,'propose',lambda *a,**k:pytest.fail('legacy paid transport'))
    monkeypatch.setattr(ai_provider.proposer.Settings,'from_env',lambda *a:pytest.fail('legacy credential settings'))
    result=app.lambda_handler({'schemaVersion':1,'checkId':'check','policyVersion':POLICY,'intent':INTENT,
        'executionBudgetMs':1000},SimpleNamespace(get_remaining_time_in_millis=lambda:2000))
    assert result['processingOutcome']=='unavailable'
