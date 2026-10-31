import io
import json
from pathlib import Path
import sys
from unittest.mock import Mock
import pytest
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'src/url_assessment')]
from assessment_service import Budget,Unavailable
from guarded_dependencies import Dependencies
from shared_url_guarded import supported_step


def test_private_rpc_has_one_attempt_url_only_no_google_secret(monkeypatch):
    import boto3
    calls=[];configs=[]
    client=Mock()
    client.invoke.side_effect=lambda **kw: calls.append(kw) or {'StatusCode':200,'Payload':io.BytesIO(b'{}')}
    monkeypatch.setattr(boto3,'client',lambda name,**kw:configs.append((name,kw['config'])) or client)
    monkeypatch.setenv('URL_RESOLVER_FUNCTION_ARN','arn:aws:lambda:us-east-1:107827791950:function:trustcheckradar-dev-url-resolver:live')
    deps=Dependencies();deps.key='unit-not-a-credential'
    assert deps.step('case','https://example.com/',Budget(3))=={}
    payload=json.loads(calls[0]['Payload'])
    assert set(payload)=={'schemaVersion','checkId','url','executionBudgetMs'}
    assert payload['schemaVersion']==2 and 1<=payload['executionBudgetMs']<=2100
    assert configs[0][0]=='lambda' and configs[0][1].retries=={'total_max_attempts':1}
    assert configs[0][1].connect_timeout+configs[0][1].read_timeout<=3
    assert 'unit-not-a-credential' not in str(calls)
    client.close.assert_called_once()


@pytest.mark.parametrize('raw',[b'{"x":1,"x":2}',b'x'*32769,b'invalid-json'])
def test_private_rpc_malformed_reply_stops_without_retry(monkeypatch,raw):
    import boto3
    client=Mock();client.invoke.return_value={'StatusCode':200,'Payload':io.BytesIO(raw)}
    monkeypatch.setattr(boto3,'client',lambda *a,**kw:client)
    monkeypatch.setenv('URL_RESOLVER_FUNCTION_ARN','arn:aws:lambda:us-east-1:107827791950:function:trustcheckradar-dev-url-resolver:live')
    with pytest.raises(Unavailable,match='RESOLVER_UNAVAILABLE'):Dependencies().step('case','https://example.com/',Budget(3))
    client.invoke.assert_called_once();client.close.assert_called_once()


@pytest.mark.parametrize('poison',[None,{},[],True])
def test_malformed_step_is_bounded(poison):
    assert not supported_step(poison,'case','https://example.com/')


def test_invalid_unicode_location_never_escapes_validation():
    value=dict(schemaVersion=2,checkId='case',resolutionStatus='http_response',
               hops=[dict(url='https://example.com/',httpStatus=302)],location='\ud800',
               reasonCodes=[],requestCount=1,scope='HTTP_SINGLE_HOP_ONLY')
    assert not supported_step(value,'case','https://example.com/')


def test_legacy_private_assessment_is_explicitly_unavailable_with_zero_network(monkeypatch):
    import importlib.util
    spec=importlib.util.spec_from_file_location('retired_private_app',ROOT/'src/url_assessment/app.py')
    app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
    monkeypatch.setenv('STAGE','dev')
    monkeypatch.setattr(app,'assess',lambda *_:pytest.fail('legacy resolve-first path retired'))
    result=app.lambda_handler({'schemaVersion':1,'checkId':'legacy','scope':'full_url','url':'https://example.com/'},
        Mock(get_remaining_time_in_millis=lambda:35000))
    assert result['checkId']=='legacy' and result['processingOutcome']=='unavailable' and result['reasonCodes']==['CONFIGURATION_UNAVAILABLE']
    assert result['lookupCount']==result['providerCallCount']==result['observedHopCount']==0
