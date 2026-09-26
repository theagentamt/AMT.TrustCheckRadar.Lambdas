from pathlib import Path
import importlib.util,json,base64
from unittest.mock import Mock
s=importlib.util.spec_from_file_location('m',str(Path(__file__).resolve().parents[2]/'scripts'/'qualify_dev_account_deletion_monitor.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
TOKEN='a.'+base64.urlsafe_b64encode(json.dumps({'exp':2000}).encode()).decode().rstrip('=')+'.c'
class Denied(Exception):response={'Error':{'Code':'NotAuthorizedException'}}
def test_both_authenticated_denials_required():
 c=Mock();c.get_user.side_effect=Denied();http=Mock(return_value=(401,{'error':{'code':'UNAUTHORIZED'}}));r=m.monitor_revocation(c,http,TOKEN,operation_id='owned',now=lambda:1000)
 assert r['httpAccepted'] and r['monitorCompleted'] and not r['erasureVerified'] and r['polls']==1

def test_failure_preserves_accepted_state():
 c=Mock();http=Mock(side_effect=TimeoutError('private'))
 r=m.monitor_revocation(c,http,TOKEN,operation_id='owned',now=lambda:1000)
 assert r['httpAccepted'] and not r['monitorCompleted'] and 'private' not in json.dumps(r)

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
