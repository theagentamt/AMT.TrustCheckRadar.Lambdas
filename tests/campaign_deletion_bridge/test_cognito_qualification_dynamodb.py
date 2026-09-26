"""Real SDK/Moto Cognito plus existing real receipt-producer composition."""
import importlib.util
import os
from pathlib import Path
import sys
from copy import deepcopy

import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('Isolated SDK only',allow_module_level=True)
from tests.campaign_deletion_bridge.test_qualification_dynamodb import runner, Q, ENV, CONTEXT, PREFIX
import boto3
from botocore.exceptions import ClientError

sys.modules['campaign_qualification']=Q
spec=importlib.util.spec_from_file_location('cognito_qualification',Path(__file__).resolve().parents[2]/'scripts/qualification/cognito_qualification.py')
C=importlib.util.module_from_spec(spec);spec.loader.exec_module(C)


def event(case='identity_complete'):
    return {'schemaVersion':1,'operation':'qualify-account-deletion-identity','runId':ENV['QUALIFICATION_RUN_ID'],'case':case}


@pytest.fixture
def world(runner, request, monkeypatch):
    client=boto3.client('cognito-idp',region_name=Q.REGION)
    pool=client.create_user_pool(PoolName=PREFIX+'-cognito',UsernameAttributes=['email'],UserPoolTags=runner.config['tags'])['UserPool']
    with monkeypatch.context() as scoped:
        if getattr(request,'param',4)==7:
            from uuid import UUID
            from moto.cognitoidp import models
            scoped.setattr(models.random,'uuid4',lambda:UUID('01997e3a-0000-7000-8000-000000000001'))
        user=client.admin_create_user(UserPoolId=pool['Id'],Username='synthetic@example.invalid',MessageAction='SUPPRESS',ForceAliasCreation=False)['User']
    subject=user['Username']
    env=ENV|{'QUALIFICATION_COGNITO_POOL_ID':pool['Id'],'QUALIFICATION_COGNITO_SUBJECT':subject}
    config=C.configuration(env,CONTEXT,event())
    return runner,client,config,env


@pytest.mark.parametrize('case',C.CASES)
def test_real_sdk_all_receipts_then_identity_delete_and_retry(world,case):
    runner,client,config,_=world
    result=C.execute(config,CONTEXT,runner.d,runner.kms,client,case)
    assert result['passed'] and result['actualIdentityDeleted']
    assert config['subject'] not in str(result) and config['pool'] not in str(result)
    with pytest.raises(ClientError) as error:client.admin_get_user(UserPoolId=config['pool'],Username=config['subject'])
    assert error.value.response['Error']['Code']=='UserNotFoundException'
    command=runner.get('ledger','ACCOUNT#'+config['subject'],'ACCOUNT_DELETION')
    assert command['status']=='COMPLETE'
    assert runner.get('ledger',command['PK'],'CAMPAIGN_RECOVERY_CONTROL') is None
    assert runner.get('ledger',command['PK'],'ACCOUNT_DELETION#IDENTITY')['status']=='COMPLETE'


@pytest.mark.parametrize('field,value',[
    ('QUALIFICATION_COGNITO_POOL_ID','us-west-2_Foreign'),('QUALIFICATION_COGNITO_POOL_ID',''),
    ('QUALIFICATION_COGNITO_SUBJECT','other@example.invalid'),('QUALIFICATION_COGNITO_SUBJECT','AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA'),
    ('QUALIFICATION_USERS_TABLE','trustcheckradar-dev-users')])
def test_configuration_rejects_invalid_identity_resource(world,field,value):
    _,_,_,env=world
    with pytest.raises(Q.QualificationFailure):C.configuration(env|{field:value},CONTEXT,event())


@pytest.mark.parametrize('change',[{'operation':'qualify-campaign-completion'},{'case':'handler_all_components'},
                                  {'schemaVersion':True},{'subject':'arbitrary'}])
def test_no_event_or_environment_switch_to_identity_mode(world,change):
    with pytest.raises(Q.QualificationFailure):C.configuration(world[3],CONTEXT,event()|change)


@pytest.mark.parametrize('change',[
    {'Name':'production'}, {'Arn':'arn:aws:cognito-idp:us-east-1:111111111111:userpool/other'},
    {'UserPoolTags':{}},{'UsernameAttributes':[]},{'LambdaConfig':{'PostConfirmation':'production'}},
    {'Id':'us-east-1_Other'}])
def test_pool_preflight_mismatch_preserves_identity_and_store(world,change,monkeypatch):
    runner,client,config,_=world
    before=runner.snapshot();real=client.describe_user_pool
    monkeypatch.setattr(client,'describe_user_pool',lambda **kw:{'UserPool':real(**kw)['UserPool']|change})
    with pytest.raises(Q.QualificationFailure):C.execute(config,CONTEXT,runner.d,runner.kms,client,'identity_complete')
    assert runner.snapshot()==before
    assert client.admin_get_user(UserPoolId=config['pool'],Username=config['subject'])['Enabled']


@pytest.mark.parametrize('change',[{'Username':'another'},{'UserAttributes':[]},{'Enabled':False},
                                  {'UserStatus':'UNKNOWN'}])
def test_user_preflight_mapping_failure_before_fixture_writes(world,change,monkeypatch):
    runner,client,config,_=world
    before=runner.snapshot();real=client.admin_get_user
    monkeypatch.setattr(client,'admin_get_user',lambda **kw:real(**kw)|change)
    with pytest.raises(Q.QualificationFailure):C.execute(config,CONTEXT,runner.d,runner.kms,client,'identity_complete')
    assert runner.snapshot()==before


@pytest.mark.parametrize('method',['admin_get_user','admin_delete_user','admin_user_global_sign_out'])
def test_every_admin_call_rejects_wrong_subject_or_pool_before_sdk(world,method):
    runner,client,config,_=world
    invoked=[]
    identity=C.Identity(config,client,lambda *a,**kw:invoked.append(kw))
    for kw in ({'UserPoolId':config['pool'],'Username':'wrong'},
               {'UserPoolId':'us-east-1_Other','Username':config['subject']},
               {'UserPoolId':config['pool'],'Username':config['subject'],'extra':'value'}):
        with pytest.raises(Q.QualificationFailure):getattr(identity,method)(**kw)
    assert invoked==[] and identity.calls==[]


def test_preflight_budget_failure_never_seeds(world,monkeypatch):
    runner,client,config,_=world
    before=runner.snapshot()
    from types import SimpleNamespace
    context=SimpleNamespace(**vars(CONTEXT));context.get_remaining_time_in_millis=lambda:5000
    with pytest.raises(Q.QualificationFailure):C.execute(config,context,runner.d,runner.kms,client,'identity_complete')
    assert runner.snapshot()==before


def test_real_handler_constructs_checked_mode_and_sanitized_result(world,monkeypatch,capsys):
    runner,client,config,env=world
    for k,v in env.items():monkeypatch.setenv(k,v)
    monkeypatch.setattr(Q,'verify_package',lambda c:None)
    runner.kms.close=lambda:None
    clients={'dynamodb':runner.d,'kms':runner.kms,'cognito-idp':client}
    monkeypatch.setattr(C.boto3,'client',lambda name,**kw:clients[name])
    result=C.lambda_handler(event(),CONTEXT)
    assert result['passed'] and result['actualIdentityDeleted']
    assert capsys.readouterr().out==''


def test_wrong_handler_manifest_cannot_enable_identity(world,tmp_path,monkeypatch):
    import json
    fake=tmp_path/'campaign_qualification.py';fake.write_text('')
    (tmp_path/'qualification-manifest.json').write_text(json.dumps({'sourceSha':'a'*40,
        'handler':'campaign_qualification.lambda_handler','memberSha256':{}}))
    monkeypatch.setattr(Q,'__file__',str(fake))
    with pytest.raises(Q.QualificationFailure):Q.verify_package(world[2])


def test_unknown_failure_is_content_free(monkeypatch,capsys):
    monkeypatch.setattr(C,'configuration',lambda *a:(_ for _ in ()).throw(RuntimeError('sensitive-subject')))
    value=C.lambda_handler({'secret':'sensitive-subject'},CONTEXT)
    assert value['passed'] is False and 'sensitive' not in str(value) and capsys.readouterr().out==''


def test_reusing_deleted_identity_refuses_before_reset_of_completed_evidence(world):
    runner,client,config,_=world
    assert C.execute(config,CONTEXT,runner.d,runner.kms,client,'identity_complete')['passed']
    before=runner.snapshot()
    with pytest.raises(ClientError) as error:
        C.execute(config,CONTEXT,runner.d,runner.kms,client,'identity_delete_lost_ack')
    assert error.value.response['Error']['Code']=='UserNotFoundException'
    assert runner.snapshot()==before


@pytest.mark.parametrize('world',[7],indirect=True)
@pytest.mark.parametrize('case',C.CASES)
def test_returned_uuid7_subject_through_all_producers_and_finalizer(world,case):
    from uuid import UUID
    runner,client,config,_=world
    assert UUID(config['subject']).version==7
    value=C.execute(config,CONTEXT,runner.d,runner.kms,client,case)
    assert value['passed'] and value['actualIdentityDeleted']
    assert runner.get('ledger','ACCOUNT#'+config['subject'],'ACCOUNT_DELETION')['status']=='COMPLETE'


@pytest.mark.parametrize('subject',[' 01997e3a-0000-7000-8000-000000000001',
    '01997E3A-0000-7000-8000-000000000001','01997e3a000070008000000000000001',
    '{01997e3a-0000-7000-8000-000000000001}','not-a-uuid'])
def test_noncanonical_uuid_subject_still_refused(world,subject):
    with pytest.raises(Q.QualificationFailure):
        C.configuration(world[3]|{'QUALIFICATION_COGNITO_SUBJECT':subject},CONTEXT,event())
