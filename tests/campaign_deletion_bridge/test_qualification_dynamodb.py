"""Same packaged runner against real SDK/Moto, without cloud resource provisioning."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('Isolated SDK only',allow_module_level=True)
from tests.campaign_deletion_bridge.test_completion_dynamodb import CompletionUnavailable
import boto3
from moto import mock_aws

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('campaign_qualification',ROOT/'scripts/qualification/campaign_qualification.py')
Q=importlib.util.module_from_spec(spec);spec.loader.exec_module(Q)
RUN='012345abcdef'
PREFIX='amt-campaign-completion-qual-'+RUN
FUNCTION=PREFIX+'-runner'
KEY=f'arn:aws:kms:{Q.REGION}:{Q.ACCOUNT}:key/12345678-1234-4234-8234-123456789abc'
ENV={'QUALIFICATION_RUN_ID':RUN,'QUALIFICATION_SOURCE_SHA':'a'*40,'QUALIFICATION_KEY_ARN':KEY,
     'QUALIFICATION_FUNCTION_NAME':FUNCTION,'AWS_REGION':Q.REGION,
     **{'QUALIFICATION_'+k.upper()+'_TABLE':PREFIX+'-'+k for k in ('pipeline','ledger','users')}}
CONTEXT=SimpleNamespace(function_name=FUNCTION,invoked_function_arn=f'arn:aws:lambda:{Q.REGION}:{Q.ACCOUNT}:function:{FUNCTION}',
                        get_remaining_time_in_millis=lambda:60000)
def event(case):return {'schemaVersion':1,'operation':'qualify-campaign-completion','runId':RUN,'case':case}

@pytest.fixture
def runner(monkeypatch):
    monkeypatch.setenv('MOTO_ACCOUNT_ID',Q.ACCOUNT)
    for k,v in ENV.items():monkeypatch.setenv(k,v)
    with mock_aws():
        d=boto3.client('dynamodb',region_name=Q.REGION)
        config=Q.configuration(ENV,CONTEXT,event(Q.CASES[0]))
        for table in config['tables'].values():
            d.create_table(TableName=table,BillingMode='PAY_PER_REQUEST',
                KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],
                AttributeDefinitions=[{'AttributeName':k,'AttributeType':'S'} for k in ('PK','SK')],
                Tags=[{'Key':k,'Value':v} for k,v in config['tags'].items()])
        kms=SimpleNamespace(
            describe_key=lambda **kw:{'KeyMetadata':{'Arn':KEY,'KeyState':'Enabled','KeySpec':'HMAC_256','KeyUsage':'GENERATE_VERIFY_MAC'}},
            list_resource_tags=lambda **kw:{'Tags':[{'TagKey':k,'TagValue':v} for k,v in config['tags'].items()]},
            generate_mac=lambda **kw:{'KeyId':KEY,'MacAlgorithm':'HMAC_SHA_256','Mac':b'x'*32})
        yield Q.Runner(config,d,kms,CONTEXT)

@pytest.mark.parametrize('case',Q.CASES)
def test_actual_qualification_runner_case(runner,case):
    runner.preflight();runner.run(case)

@pytest.mark.parametrize('field,value',[
    ('QUALIFICATION_RUN_ID','123'),('QUALIFICATION_PIPELINE_TABLE','trustcheckradar-dev-pipeline'),
    ('QUALIFICATION_LEDGER_TABLE',PREFIX+'-users'),('QUALIFICATION_USERS_TABLE',PREFIX+'-ledger'),
    ('QUALIFICATION_FUNCTION_NAME','production'),('AWS_REGION','us-west-2'),
    ('QUALIFICATION_KEY_ARN',KEY.replace(Q.ACCOUNT,'111111111111')),('QUALIFICATION_SOURCE_SHA','not-a-sha')])
def test_configuration_rejects_foreign_or_unpinned_resources(field,value):
    with pytest.raises(Q.QualificationFailure):Q.configuration(ENV|{field:value},CONTEXT,event(Q.CASES[0]))

@pytest.mark.parametrize('change',[{'schemaVersion':True},{'runId':'f'*12},{'operation':'other'},
                                  {'case':'arbitrary'},{'payload':'secret'}])
def test_closed_event_schema(change):
    with pytest.raises(Q.QualificationFailure):Q.configuration(ENV,CONTEXT,event(Q.CASES[0])|change)

@pytest.mark.parametrize('bad',['table_tag','key_tag','key_state','table_arn'])
def test_preflight_rejects_before_seed_or_mutation(runner,bad):
    before=runner.snapshot()
    if bad=='table_tag':
        real=runner.d.list_tags_of_resource
        runner.d.list_tags_of_resource=lambda **kw:{'Tags':[]}
    if bad=='key_tag':runner.kms.list_resource_tags=lambda **kw:{'Tags':[]}
    if bad=='key_state':runner.kms.describe_key=lambda **kw:{'KeyMetadata':{'Arn':KEY,'KeyState':'PendingDeletion','KeySpec':'HMAC_256','KeyUsage':'GENERATE_VERIFY_MAC'}}
    if bad=='table_arn':
        real=runner.d.describe_table
        def changed(**kw):
            out=real(**kw);out['Table']['TableArn']=out['Table']['TableArn'].replace(Q.ACCOUNT,'111111111111');return out
        runner.d.describe_table=changed
    with pytest.raises(Q.QualificationFailure):runner.preflight()
    assert runner.snapshot()==before


def test_handler_errors_never_return_event_or_secret(monkeypatch,capsys):
    result=Q.lambda_handler({'secret':'sensitive-customer-placeholder'},CONTEXT)
    assert result['passed'] is False and result['code']=='QUALIFICATION_FAILED'
    assert capsys.readouterr().out=='' and 'sensitive' not in str(result)


def test_actual_handler_composes_sdk_runner_and_sanitizes_success(runner,monkeypatch):
    monkeypatch.setattr(Q,'verify_package',lambda config:None)
    runner.kms.close=lambda:None
    monkeypatch.setattr(Q.boto3,'client',lambda service,**kw:runner.d if service=='dynamodb' else runner.kms)
    value=Q.lambda_handler(event('account_complete_replay'),CONTEXT)
    assert value['passed'] is True and value['syntheticOnly'] is True
    assert value['historicalCoverageApproved'] is False and value['sourceSha']=='a'*40
    assert 'synthetic-'+RUN not in str(value)


def test_incomplete_reset_scan_never_starts_fixture_deletion(runner):
    calls=[]
    runner.d.scan=lambda **kw:{'Items':[],'LastEvaluatedKey':{'PK':{'S':'unknown'},'SK':{'S':'unknown'}}}
    runner.d.delete_item=lambda **kw:calls.append('delete')
    runner.d.put_item=lambda **kw:calls.append('put')
    with pytest.raises(Q.QualificationFailure):runner.seed()
    assert calls==[]


def test_package_verification_detects_tampered_member(tmp_path,monkeypatch):
    import hashlib,json
    fake=tmp_path/'campaign_qualification.py';fake.write_text('original')
    manifest={'sourceSha':'a'*40,'handler':'campaign_qualification.lambda_handler',
              'memberSha256':{'campaign_qualification.py':hashlib.sha256(b'original').hexdigest()}}
    (tmp_path/'qualification-manifest.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(Q,'__file__',str(fake))
    Q.verify_package({'source':'a'*40})
    fake.write_text('modified')
    with pytest.raises(Q.QualificationFailure):Q.verify_package({'source':'a'*40})
