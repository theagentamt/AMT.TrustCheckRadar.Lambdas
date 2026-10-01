from pathlib import Path
import importlib.util,json,io
from unittest.mock import Mock
import pytest
s=importlib.util.spec_from_file_location('q',str(Path(__file__).resolve().parents[2]/'scripts'/'qualify_cleanup_workers.py'));q=importlib.util.module_from_spec(s);s.loader.exec_module(q)
@pytest.mark.parametrize('field',['failed','pending','deleted','completed','overdue','examined'])
def test_nonempty_v1_play_outcome_refused(field):
 result={k:0 for k in ('examined','completed','pending','failed','deleted','overdue','skipped','pages','fullPassCompleted','fullPassAgeSeconds')};result['pages']=1;result[field]=1
 with pytest.raises(q.Refused):q.validate_response('play-token-deletion',result)

def test_disabled_and_injected_shapes_refused():
 for kind in q.EVENTS:
  with pytest.raises(q.Refused):q.validate_response(kind,{'enabled':False})

def test_exact_campaign_tick_is_not_receipt_proof():
 fields=('RecoveryTicks','RecoveryFailures','CommandsAttempted','CommandsUnverified','CommandsCompleted','CommandsTerminalAcknowledged','SidecarSchemaFailures','IndexCandidatesObserved','ObservedPendingAgeSeconds','ObservedOverdueCommands','RecoveryBudgetExhausted','RecoveryShardTruncated')
 r={k:0 for k in fields};r.update(enabled=True,complete=False,receiptEligible=False,RecoveryTicks=1)
 assert q.validate_response('campaign-deletion-bridge',r)['RecoveryTicks']==1
 r['receiptEligible']=True
 with pytest.raises(q.Refused):q.validate_response('campaign-deletion-bridge',r)

@pytest.mark.parametrize('pk,sk,status', [('ACCOUNT#private','ACCOUNT_DELETION','REQUESTED'),('ACCOUNT#private','CAMPAIGN_WITHDRAWAL#op','PENDING'),('ACCOUNT#private','CAMPAIGN_RECOVERY#op','PENDING')])
def test_pending_work_refused_before_any_invoke(pk,sk,status):
 d=Mock();d.scan.return_value={'Items':[{'PK':{'S':pk},'SK':{'S':sk},'status':{'S':status}}]};l=Mock();r=q.Runner({},sts=None,lam=l,ddb=d)
 with pytest.raises(q.Refused):r.empty_work()
 l.invoke.assert_not_called()

def test_history_account_data_refused():
 d=Mock();d.scan.side_effect=[{'Items':[]},{'Items':[{'PK':{'S':'USER#private'},'SK':{'S':'STATE'}}]}];l=Mock();r=q.Runner({},sts=None,lam=l,ddb=d)
 with pytest.raises(q.Refused):r.empty_work()
 l.invoke.assert_not_called()

def test_unknown_functions_and_version_refused():
 r=q.Runner({'schemaVersion':1,'functions':{},'tableIds':{}},sts=None,lam=None,ddb=None)
 with pytest.raises(q.Refused):r.plan()

@pytest.mark.parametrize('fault,category', [('function','function-error'),('version','runtime-mismatch'),('shape','response-validation'),('network','invoke-transport')])
def test_failed_invocation_records_only_fixed_category_and_does_not_retry(fault,category):
 r=q.Runner({'functions':{'account-data-api':{'version':'$LATEST'}}},sts=None,lam=Mock(),ddb=None)
 r.preflight=Mock();r.empty_work=Mock(return_value={});r.runtime=Mock(return_value={})
 payload=io.BytesIO(b'{"private":"DO_NOT_PERSIST"}')
 response={'StatusCode':200,'Payload':payload,'ExecutedVersion':'$LATEST'}
 if fault=='function':response['FunctionError']='Unhandled'
 if fault=='version':response['ExecutedVersion']='9'
 if fault=='network':r.l.invoke.side_effect=RuntimeError('DO_NOT_PERSIST')
 else:r.l.invoke.return_value=response
 saved=[]
 with pytest.raises(q.Refused):r.run(save=lambda value:saved.append(json.loads(json.dumps(value))))
 assert saved[-1]['failure']=={'worker':'account-data-api','category':category}
 assert saved[-1]['allPassed'] is False and saved[-1]['cases']==[]
 assert 'DO_NOT_PERSIST' not in json.dumps(saved)
 assert r.l.invoke.call_count==1
