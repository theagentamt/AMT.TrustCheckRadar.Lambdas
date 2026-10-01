from pathlib import Path
import importlib.util,json,base64
from unittest.mock import Mock
import pytest
s=importlib.util.spec_from_file_location('m',str(Path(__file__).resolve().parents[2]/'scripts'/'qualify_dev_account_deletion_monitor.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
TOKEN='a.'+base64.urlsafe_b64encode(json.dumps({'exp':2000}).encode()).decode().rstrip('=')+'.c'
class Denied(Exception):response={'Error':{'Code':'NotAuthorizedException'}}
def test_both_authenticated_denials_required():
 c=Mock();c.get_user.side_effect=Denied();http=Mock(return_value=(401,{'error':{'code':'UNAUTHORIZED'}}));r=m.monitor_revocation(c,http,TOKEN,operation_id='owned',now=lambda:1000)
 assert r['httpAccepted'] and r['monitorCompleted'] and not r['erasureVerified'] and r['polls']==1

def test_failure_preserves_accepted_state():
 c=Mock();http=Mock(side_effect=TimeoutError('private'))
 r=m.monitor_revocation(c,http,TOKEN,operation_id='owned',now=lambda:1000,sleep=lambda n:None)
 assert r['httpAccepted'] and not r['monitorCompleted'] and 'private' not in json.dumps(r)
 assert r['polls']==120 and r['transportFailures']==120

def test_expired_token_not_revocation_proof():
 c=Mock();http=Mock();r=m.monitor_revocation(c,http,TOKEN,operation_id='owned',now=lambda:2001)
 assert r['httpAccepted'] and not r['monitorCompleted'];c.get_user.assert_not_called();http.assert_not_called()

def test_gateway_401_is_not_application_complete_proof():
 c=Mock();http=Mock(return_value=(401,{'message':'Unauthorized'}));r=m.monitor_revocation(c,http,TOKEN,operation_id='owned',now=lambda:1000)
 assert r['httpAccepted'] and not r['monitorCompleted'] and not r['applicationGetDenied']

def test_bounded_pending_does_not_resubmit():
 c=Mock();http=Mock(return_value=(200,{'schemaVersion':1,'operation':'ACCOUNT_DELETION','status':'REQUESTED','operationId':'owned'}));clock=iter([0,0,0,0,7]);r=m.monitor_revocation(c,http,TOKEN,operation_id='owned',max_seconds=6,now=lambda:1000,monotonic=lambda:next(clock),sleep=lambda n:None)
 assert r['httpAccepted'] and not r['monitorCompleted'] and r['polls']==1
 assert all(call.args[0]=='GET' for call in http.call_args_list)


def test_expiry_crossed_during_cognito_is_not_revocation_proof():
 c=Mock();c.get_user.side_effect=Denied();http=Mock(return_value=(401,{'error':{'code':'UNAUTHORIZED'}}));times=iter([1000,1000,1000,2001])
 r=m.monitor_revocation(c,http,TOKEN,operation_id='owned',now=lambda:next(times))
 assert r['httpAccepted'] and not r['monitorCompleted']

def test_alternating_denials_do_not_accumulate():
 c=Mock();c.get_user.side_effect=[{},Denied()];http=Mock(side_effect=[(401,{'error':{'code':'UNAUTHORIZED'}}),(200,{'schemaVersion':1,'operation':'ACCOUNT_DELETION','status':'REQUESTED','operationId':'owned'})]);tick=[0]
 def sleep(n):tick[0]+=5
 r=m.monitor_revocation(c,http,TOKEN,operation_id='owned',max_seconds=10,now=lambda:1000,monotonic=lambda:tick[0],sleep=sleep)
 assert r['httpAccepted'] and not r['monitorCompleted'] and r['polls']==2

def test_other_operation_pending_refused():
 c=Mock();http=Mock(return_value=(200,{'schemaVersion':1,'operation':'ACCOUNT_DELETION','status':'REQUESTED','operationId':'other'}))
 r=m.monitor_revocation(c,http,TOKEN,operation_id='owned',now=lambda:1000)
 assert r['httpAccepted'] and not r['monitorCompleted'];c.get_user.assert_not_called()

def test_unavailable_http_records_only_status_and_allowlisted_error_code():
 c=Mock();http=Mock(return_value=(500,{'error':{'code':'INTERNAL_ERROR','message':'SECRET_VALUE'},'raw':'PRIVATE'}))
 result=m.monitor_revocation(c,http,TOKEN,operation_id='owned',now=lambda:1000,sleep=lambda n:None)
 assert result['httpAccepted'] and result['category']=='OBSERVATION_BUDGET_EXHAUSTED'
 assert result['httpStatus']==500 and result['httpErrorCode']=='INTERNAL_ERROR'
 assert 'SECRET_VALUE' not in json.dumps(result) and 'PRIVATE' not in json.dumps(result)
 c.get_user.assert_not_called()

def test_unknown_http_error_value_not_persisted():
 c=Mock();http=Mock(return_value=(503,{'error':{'code':'PRIVATE_UNKNOWN'}}))
 result=m.monitor_revocation(c,http,TOKEN,operation_id='owned',now=lambda:1000,sleep=lambda n:None)
 assert result['httpStatus']==503 and result['httpErrorCode']=='UNRECOGNIZED'
 assert 'PRIVATE_UNKNOWN' not in json.dumps(result)

@pytest.mark.parametrize('status',[429,500,502,503,504])
def test_transient_http_then_same_poll_denials_can_complete(status):
 c=Mock();c.get_user.side_effect=Denied();http=Mock(side_effect=[(status,None),(401,{'error':{'code':'UNAUTHORIZED'}})])
 r=m.monitor_revocation(c,http,TOKEN,operation_id='owned',now=lambda:1000,sleep=lambda n:None)
 assert r['httpAccepted'] and r['monitorCompleted'] and r['polls']==2
 assert r['httpRetryCounts'][str(status)]==1 and sum(r['httpRetryCounts'].values())==1
 assert c.get_user.call_count==1 and all(call.args[0]=='GET' for call in http.call_args_list)


def test_permanent_503_exhausts_time_budget_without_denial_or_post():
 c=Mock();http=Mock(return_value=(503,None));clock=[0]
 def sleep(n):clock[0]+=n
 r=m.monitor_revocation(c,http,TOKEN,operation_id='owned',max_seconds=10,now=lambda:1000,monotonic=lambda:clock[0],sleep=sleep)
 assert r['httpAccepted'] and not r['monitorCompleted'] and r['category']=='OBSERVATION_BUDGET_EXHAUSTED'
 assert r['polls']==2 and r['httpRetryCounts']['503']==2 and not r['applicationGetDenied']
 c.get_user.assert_not_called();assert all(call.args[0]=='GET' for call in http.call_args_list)


def test_network_exception_retry_then_success_never_retains_message():
 c=Mock();c.get_user.side_effect=Denied();http=Mock(side_effect=[TimeoutError('SECRET_TOKEN'),(401,{'error':{'code':'UNAUTHORIZED'}})])
 r=m.monitor_revocation(c,http,TOKEN,operation_id='owned',now=lambda:1000,sleep=lambda n:None)
 assert r['monitorCompleted'] and r['transportFailures']==1 and r['polls']==2
 assert 'SECRET_TOKEN' not in json.dumps(r)


@pytest.mark.parametrize('transport',[False,True])
def test_expiry_during_failed_network_call_prevents_retry(transport):
 c=Mock();clock=[1000];sleep=Mock()
 def http(*args):
  clock[0]=2000
  if transport:raise TimeoutError('private')
  return 503,None
 r=m.monitor_revocation(c,http,TOKEN,operation_id='owned',now=lambda:clock[0],sleep=sleep)
 assert r['httpAccepted'] and not r['monitorCompleted'] and r['polls']==1
 assert r['category']=='OBSERVATION_BUDGET_EXHAUSTED'
 c.get_user.assert_not_called();sleep.assert_not_called()


@pytest.mark.parametrize('payload',[b'',b'<html>upstream unavailable</html>'])
def test_unstructured_retryable_response_exposes_only_status(monkeypatch,payload):
 conn=Mock();response=Mock(status=503);response.read.return_value=payload;conn.getresponse.return_value=response
 monkeypatch.setattr(m.http.client,'HTTPSConnection',Mock(return_value=conn))
 assert m.http_call('GET','MEMORY_ONLY_TOKEN')==(503,None)
 conn.close.assert_called_once()


def test_nonretryable_unknown_response_and_invalid_budget_fail_closed():
 c=Mock();http=Mock(return_value=(403,{'error':{'code':'FORBIDDEN'}}))
 r=m.monitor_revocation(c,http,TOKEN,operation_id='owned',now=lambda:1000)
 assert not r['monitorCompleted'] and r['category']=='HTTP_OBSERVATION_UNAVAILABLE' and r['polls']==1
 http.reset_mock()
 r=m.monitor_revocation(c,http,TOKEN,operation_id='owned',max_seconds=601,now=lambda:1000)
 assert r['httpAccepted'] and not r['monitorCompleted'];http.assert_not_called()
