import importlib.util,json,copy,subprocess,sys
from pathlib import Path
from unittest.mock import Mock
import pytest
spec=importlib.util.spec_from_file_location('writer',Path(__file__).resolve().parents[2]/'scripts/initialize_deletion_inventories.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
ROOT=Path(__file__).resolve().parents[2]
SHA=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
@pytest.fixture
def world(tmp_path):
 # Exercise actual current runtime validators; source cleanliness/identity is
 # tested separately, so editing a test does not prevent running the suite.
 sys.path[:0]=[str(ROOT/'src/campaign_deletion_bridge'),str(ROOT/'src')]
 from shared_campaign_locators import core as L
 from shared_campaign_recovery import records as R
 from shared_account_finalization import service as A
 from completion import Completion as C
 contracts=(L,R,A,C);now=1790435000
 manifests={};markers={}
 for kind in m.KINDS:
  path=tmp_path/(kind+'.json');path.write_text(json.dumps({'syntheticTestOnly':True,'approved':True,'markerWriteAuthorizedByArtifact':True,'account':m.ACCOUNT,'region':m.REGION,'environment':'dev'}));sha=m.hashlib.sha256(path.read_bytes()).hexdigest();manifests[kind]={'path':str(path),'sha256':sha}
  markers[kind]={'PK':'INVENTORY#dev','SK':m.TARGETS[kind][1],'recordType':{'locator':'CAMPAIGN_LOCATOR_INVENTORY'}.get(kind,m.TARGETS[kind][1]),'schemaVersion':1,'environment':'dev','revision':1,'coverage':'VERIFIED_COMPLETE','manifestSha256':sha,'approvedAtEpoch':now-1}
 markers['locator'].update(locatorSchemaVersion=1,minimumPeriodId=now//1209600,priorPeriodsErased=True,writers=L.WRITERS)
 markers['recovery'].update(legacyCoverageVerified=True,writers=R.WRITERS)
 import completion
 markers['completion'].update(locatorManifestSha256=markers['locator']['manifestSha256'],locatorInventoryRevision=1,recoveryManifestSha256=markers['recovery']['manifestSha256'],recoveryInventoryRevision=1,invariants=completion.INVARIANTS)
 markers['account'].update(requiredComponents=list(A.REQUIRED_COMPONENTS),usernameIsSubVerified=True)
 envs={};funcs={}
 for name in m.WORKERS|{m.API}:
  e={'CAMPAIGN_PERIOD_ADMISSION_ENABLED':'false'}
  if name.endswith('campaign-deletion-bridge'):e.update({k:'false' for k in ('CAMPAIGN_RECOVERY_ENABLED','CAMPAIGN_DELETION_STREAM_ENABLED','CAMPAIGN_COMPLETION_ENABLED')})
  if name==m.API:e.update({k:'false' for k in ('ACCOUNT_DELETION_ENABLED','ACCOUNT_IDENTITY_FINALIZER_ENABLED','CAMPAIGN_RECOVERY_WRITES_ENABLED')});e['ACCOUNT_DELETION_HTTP_SUBJECTS_JSON']='[]'
  envs[name]=e;funcs[name]={'codeSha256':'A'*43+'=','revisionId':'11111111-1111-4111-8111-111111111111','environmentSha256':m.digest(e)}
 p={'schemaVersion':1,'sourceCommit':SHA,'tables':{k:{'name':v,'tableId':'11111111-1111-4111-8111-111111111111'} for k,v in m.TABLES.items()},'markers':markers,'manifests':manifests,'closedFunctions':funcs}
 sts=Mock();sts.get_caller_identity.return_value={'Account':m.ACCOUNT};d=Mock();l=Mock();state={}
 d.describe_table.side_effect=lambda **kw:{'Table':{'TableArn':f'arn:aws:dynamodb:{m.REGION}:{m.ACCOUNT}:table/'+kw['TableName'],'TableStatus':'ACTIVE','TableId':'11111111-1111-4111-8111-111111111111'}}
 l.get_function_configuration.side_effect=lambda **kw:{'FunctionArn':f'arn:aws:lambda:{m.REGION}:{m.ACCOUNT}:function:'+kw['FunctionName'],'CodeSha256':funcs[kw['FunctionName']]['codeSha256'],'RevisionId':funcs[kw['FunctionName']]['revisionId'],'LastUpdateStatus':'Successful','Environment':{'Variables':envs[kw['FunctionName']]}}
 d.get_item.side_effect=lambda **kw:{'Item':state[(kw['TableName'],kw['Key']['SK']['S'])]} if (kw['TableName'],kw['Key']['SK']['S']) in state else {}
 def tx(**kw):
  assert len(kw['TransactItems'])==4
  for a in kw['TransactItems']:
   v=a['Put'];assert v['ConditionExpression']=='attribute_not_exists(PK) AND attribute_not_exists(SK)';state[(v['TableName'],v['Item']['SK']['S'])]=v['Item']
 d.transact_write_items.side_effect=tx
 approval={'schemaVersion':1,'account':m.ACCOUNT,'region':m.REGION,'environment':'dev','sourceCommit':SHA,'manifestSha256':{k:v['sha256'] for k,v in manifests.items()},'runtimeSnapshotSha256':m.digest(funcs),'tableIdentitySha256':m.digest(p['tables']),'approvedAtEpoch':now-1,'reviewReference':'synthetic-offline-test-only','decision':'APPROVED_FOR_MARKER_INITIALIZATION'}
 ap=tmp_path/'approval.json';ap.write_text(json.dumps(approval));p['approval']={'path':str(ap),'sha256':m.hashlib.sha256(ap.read_bytes()).hexdigest()}
 return m.Writer(p,contracts,sts=sts,ddb=d,lam=l,now=lambda:now),state,envs

def test_four_atomic_absent_puts_exact_idempotent_no_rewrite(world):
 w,state,_=world;assert w.apply()['initialized'];assert len(state)==4;assert w.apply()['idempotent'];w.d.transact_write_items.assert_called_once()

def test_lost_ack_exact_readback_no_retry(world):
 w,_,_=world;tx=w.d.transact_write_items.side_effect
 def lose(**kw):tx(**kw);raise TimeoutError()
 w.d.transact_write_items.side_effect=lose
 assert w.apply()['ambiguousWriteAcknowledgment'];w.d.transact_write_items.assert_called_once()

def test_uncommitted_failure_no_retry(world):
 w,_,_=world;w.d.transact_write_items.side_effect=TimeoutError()
 with pytest.raises(m.Refused):w.apply()
 w.d.transact_write_items.assert_called_once()

def test_partial_existing_refused(world):
 w,state,_=world;k='locator';r=w.p['markers'][k];state[(m.TABLES['pipeline'],r['SK'])]=w.contracts[1].wire(r)
 with pytest.raises(m.Refused):w.apply()
 w.d.transact_write_items.assert_not_called()

@pytest.mark.parametrize('bad',['extra','future','revision','link','components','prior','writers','hash','approval','minimum'])
def test_invalid_manifests_marker_contracts_fail_before_aws(world,bad):
 w,_,_=world;p=w.p
 if bad=='extra':p['markers']['account']['extra']=True
 if bad=='future':
  for row in p['markers'].values():row['approvedAtEpoch']=w.now()+1
 if bad=='revision':p['markers']['account']['revision']=True
 if bad=='link':p['markers']['completion']['recoveryInventoryRevision']=2
 if bad=='components':p['markers']['account']['requiredComponents']=[]
 if bad=='prior':p['markers']['locator']['priorPeriodsErased']=False
 if bad=='writers':p['markers']['recovery']['writers']=[]
 if bad=='hash':p['manifests']['account']['sha256']='0'*64
 if bad=='approval':
  f=Path(p['approval']['path']);data=json.loads(f.read_text());data['decision']='PENDING';f.write_text(json.dumps(data));p['approval']['sha256']=m.hashlib.sha256(f.read_bytes()).hexdigest()
 if bad=='minimum':p['markers']['locator']['minimumPeriodId']-=1
 with pytest.raises(Exception):w.apply()
 w.sts.get_caller_identity.assert_not_called();w.d.transact_write_items.assert_not_called()

def test_gate_change_after_observation_prevents_write(world):
 w,_,envs=world;read=w.reads
 def change():
  result=read();envs[m.API]['ACCOUNT_DELETION_ENABLED']='true';return result
 w.reads=change
 with pytest.raises(m.Refused):w.apply()
 w.d.transact_write_items.assert_not_called()

def test_table_identity_or_foreign_account_refused(world):
 w,_,_=world;w.sts.get_caller_identity.return_value={'Account':'000000000000'}
 with pytest.raises(m.Refused):w.apply()
 w.d.transact_write_items.assert_not_called()


def test_source_loader_rejects_wrong_commit_before_import():
 with pytest.raises(m.Refused):m.load_contracts(ROOT,'0'*40)


def test_boolean_numeric_alias_in_existing_marker_is_not_exact(world):
 w,state,_=world;w.apply();key=(m.TABLES['ledger'],'ACCOUNT_DATA_INVENTORY');state[key]['schemaVersion']={'BOOL':True}
 with pytest.raises(m.Refused):w.apply()
 assert w.d.transact_write_items.call_count==1
