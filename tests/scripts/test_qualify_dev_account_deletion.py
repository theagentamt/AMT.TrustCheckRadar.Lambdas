import importlib.util,io,json,os
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock
import pytest
spec=importlib.util.spec_from_file_location('subject',Path(__file__).resolve().parents[2]/'scripts/qualify_dev_account_deletion.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
SUB='01993d31-cafe-7abc-8abc-0123456789ab'
POOL={'Id':'us-east-1_Pool123','Arn':'arn:aws:cognito-idp:us-east-1:107827791950:userpool/us-east-1_Pool123','UsernameAttributes':['email'],'MfaConfiguration':'OFF','LambdaConfig':{'PostConfirmation':'arn:aws:lambda:us-east-1:107827791950:function:'+m.WRITER}}
CLIENT={'UserPoolId':POOL['Id'],'ClientId':'client123456','ExplicitAuthFlows':['ALLOW_USER_PASSWORD_AUTH']}
def world(tmp_path):
 p={'schemaVersion':1,'runId':'012345abcdef','poolId':POOL['Id'],'clientId':CLIENT['ClientId'],'poolSettingsSha256':m.digest(POOL),'clientSettingsSha256':m.digest(CLIENT),'writerCodeSha256':'A'*43+'=','writerRevisionId':'11111111-1111-4111-8111-111111111111','apiCodeSha256':'A'*43+'=','apiRevisionId':'22222222-2222-4222-8222-222222222222'}
 sts=Mock();sts.get_caller_identity.return_value={'Account':m.ACCOUNT};c=Mock();c.exceptions=SimpleNamespace(UserNotFoundException=type('Missing',(Exception,),{}));c.describe_user_pool.return_value={'UserPool':dict(POOL)};c.describe_user_pool_client.return_value={'UserPoolClient':dict(CLIENT)}
 email='deletion-qual-'+p['runId']+'@example.invalid';user={'Username':SUB,'Enabled':True,'UserStatus':'FORCE_CHANGE_PASSWORD','UserAttributes':[{'Name':'sub','Value':SUB},{'Name':'email','Value':email},{'Name':'given_name','Value':'Deletion'},{'Name':'family_name','Value':'Qualification'}]}
 c.admin_get_user.side_effect=lambda **kw: (_ for _ in ()).throw(c.exceptions.UserNotFoundException()) if kw['Username']==email else user
 c.admin_create_user.return_value={'User':user};c.initiate_auth.return_value={'AuthenticationResult':{'AccessToken':'secret-token','RefreshToken':'private-refresh'}};c.get_user.return_value=user
 d=Mock();d.describe_table.side_effect=lambda **kw:{'Table':{'TableArn':f'arn:aws:dynamodb:{m.REGION}:{m.ACCOUNT}:table/'+kw['TableName'],'TableStatus':'ACTIVE'}}
 state={'profile':False}
 def get(**kw):
  if kw['TableName']==m.USERS and state['profile']:return {'Item':{'sub':{'S':SUB},'email':{'S':email},'status':{'S':'PENDING_AGE_GATE'},'ageVerified':{'BOOL':False}}}
  return {}
 d.get_item.side_effect=get;l=Mock()
 def cfg(**kw):
  writer=kw['FunctionName']==m.WRITER;prefix='writer' if writer else 'api'
  env={'USERS_TABLE_NAME':m.USERS,'DELETION_LEDGER_TABLE_NAME':m.LEDGER} if writer else {'APP_ENVIRONMENT':'dev','COGNITO_USER_POOL_ID':p['poolId'],'COGNITO_APP_CLIENT_ID':p['clientId'],'ACCOUNT_DELETION_ENABLED':'true','ACCOUNT_IDENTITY_FINALIZER_ENABLED':'true','CAMPAIGN_RECOVERY_WRITES_ENABLED':'true','ACCOUNT_DELETION_HTTP_SUBJECTS_JSON':json.dumps([SUB])}
  return {'FunctionArn':f'arn:aws:lambda:{m.REGION}:{m.ACCOUNT}:function:'+kw['FunctionName'],'CodeSha256':p[prefix+'CodeSha256'],'RevisionId':p[prefix+'RevisionId'],'Runtime':'python3.14','Architectures':['arm64'],'LastUpdateStatus':'Successful','Environment':{'Variables':env}}
 l.get_function_configuration.side_effect=cfg
 def invoke(**kw):state['profile']=True;return {'StatusCode':200,'Payload':io.BytesIO(b'{}')}
 l.invoke.side_effect=invoke
 def http(method,token,body=None):
  assert token=='secret-token'
  return (200,{'status':'NOT_REQUESTED'}) if method=='GET' else (202,{'schemaVersion':1,'operation':'ACCOUNT_DELETION','operationId':body['operationId'],'status':'REQUESTED'})
 h=m.Harness(p,tmp_path/'private.json',sts=sts,cognito=c,lam=l,ddb=d,http=Mock(side_effect=http),now=lambda:1790433600)
 return h,c,d,l,state

def test_success_is_two_stages_no_secrets_and_no_direct_delete(tmp_path):
 h,c,d,l,_=world(tmp_path);assert h.prepare()['stage']=='PROFILE_READY';c.admin_set_user_password.assert_not_called();h.http.assert_not_called()
 j=h.journal();assert j['subject']==SUB and h.path.stat().st_mode&0o777==0o600
 assert h.authenticate_http()['stage']=='ACCEPTED';c.admin_create_user.assert_called_once();assert c.admin_create_user.call_args.kwargs['MessageAction']=='SUPPRESS';assert not c.admin_create_user.call_args.kwargs['ForceAliasCreation']
 assert c.admin_set_user_password.call_args.kwargs['Permanent'] is True
 assert h.http.call_count==2;assert h.journal()['operationId']==j['operationId']
 for secret in ('secret-token','private-refresh',c.admin_set_user_password.call_args.kwargs['Password']):assert secret not in h.path.read_text()
 c.admin_delete_user.assert_not_called();d.put_item.assert_not_called();d.transact_write_items.assert_not_called()

def test_existing_email_no_journal_or_creation(tmp_path):
 h,c,_,_,_=world(tmp_path);c.admin_get_user.side_effect=None;c.admin_get_user.return_value={'existing':True}
 with pytest.raises(m.Refused):h.prepare()
 assert not h.path.exists();c.admin_create_user.assert_not_called()

def test_prepare_not_replayed_and_ambiguous_post_does_not_reset_password(tmp_path):
 h,c,_,l,_=world(tmp_path);h.prepare()
 with pytest.raises(m.Refused):h.prepare()
 h.http.side_effect=[(200,{'status':'NOT_REQUESTED'}),TimeoutError('not printed')]
 with pytest.raises(TimeoutError):h.authenticate_http()
 assert h.journal()['stage']=='POST_ATTEMPTED';assert c.admin_set_user_password.call_count==1
 with pytest.raises(m.Refused):h.authenticate_http()
 assert c.admin_set_user_password.call_count==1;l.invoke.assert_called_once()

@pytest.mark.parametrize('change',['foreign_account','foreign_pool','hooks','settings_drift','writer_drift','existing_profile','existing_fence'])
def test_preparation_refuses_wrong_resources_or_existing_state(tmp_path,change):
 h,c,d,l,_=world(tmp_path)
 if change=='foreign_account':h.sts.get_caller_identity.return_value={'Account':'000000000000'}
 if change=='foreign_pool':c.describe_user_pool.return_value={'UserPool':{**POOL,'Arn':POOL['Arn'].replace(m.ACCOUNT,'000000000000')}}
 if change=='hooks':c.describe_user_pool.return_value={'UserPool':{**POOL,'LambdaConfig':{**POOL['LambdaConfig'],'PreSignUp':'foreign'}}}
 if change=='settings_drift':c.describe_user_pool.return_value={'UserPool':{**POOL,'DeletionProtection':'ACTIVE'}}
 if change=='writer_drift':l.get_function_configuration.side_effect=lambda **kw:{'FunctionArn':f'arn:aws:lambda:{m.REGION}:{m.ACCOUNT}:function:'+m.WRITER,'CodeSha256':'bad'}
 if change.startswith('existing_'):d.get_item.side_effect=lambda **kw:{'Item':{'unknown':True}} if (kw['TableName']==m.USERS)==(change=='existing_profile') else {}
 with pytest.raises((m.Refused,KeyError)):h.prepare()
 l.invoke.assert_not_called();h.http.assert_not_called()

@pytest.mark.parametrize('bad',['A'*36,'{01993d31-cafe-7abc-8abc-0123456789ab}','01993D31-CAFE-7ABC-8ABC-0123456789AB'])
def test_canonical_subject_required(tmp_path,bad):
 h,c,_,l,_=world(tmp_path);c.admin_create_user.return_value={'User':{'Username':bad,'Attributes':[{'Name':'sub','Value':bad}]}}
 with pytest.raises(m.Refused):h.prepare()
 l.invoke.assert_not_called()

def test_wrong_http_scope_before_password_mutation(tmp_path):
 h,c,_,l,_=world(tmp_path);h.prepare();cfg=l.get_function_configuration.side_effect
 def change(**kw):
  value=cfg(**kw)
  if kw['FunctionName']==m.API:value['Environment']['Variables']['ACCOUNT_DELETION_HTTP_SUBJECTS_JSON']='[]'
  return value
 l.get_function_configuration.side_effect=change
 with pytest.raises(m.Refused):h.authenticate_http()
 c.admin_set_user_password.assert_not_called();h.http.assert_not_called()

def test_manifest_duplicate_or_extra_fields_refused(tmp_path):
 h,*_=world(tmp_path)
 with pytest.raises(m.Refused):m.validate_plan({**h.p,'extra':True})
 f=tmp_path/'bad.json';f.write_text('{"same":1,"same":2}')
 with pytest.raises(m.Refused):m.read_json(f)


def test_failed_atomic_journal_update_preserves_original(tmp_path,monkeypatch):
 h,*_=world(tmp_path);h.prepare();before=h.path.read_bytes();j=h.journal();j['stage']='POST_ATTEMPTED'
 def fail(*args):raise OSError('synthetic write failure')
 monkeypatch.setattr(m.os,'replace',fail)
 with pytest.raises(OSError):h.write(j)
 assert h.path.read_bytes()==before and h.journal()['stage']=='PROFILE_READY'
 assert list(tmp_path.iterdir())==[h.path]
