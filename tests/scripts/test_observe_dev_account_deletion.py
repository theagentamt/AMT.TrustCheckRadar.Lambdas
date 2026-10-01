from pathlib import Path
import importlib.util,sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from boto3.dynamodb.types import TypeSerializer,TypeDeserializer
s=importlib.util.spec_from_file_location('o',str(Path(__file__).resolve().parents[2]/'scripts'/'observe_dev_account_deletion.py'));o=importlib.util.module_from_spec(s);s.loader.exec_module(o)
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'));from shared_account_finalization import Finalizer,REQUIRED_COMPONENTS
S,D=TypeSerializer(),TypeDeserializer();wire=lambda row:{k:S.serialize(v) for k,v in row.items()};plain=lambda row:{k:D.deserialize(v) for k,v in row.items()}
@pytest.fixture
def world():
 j={'subject':'01993d31-cafe-7abc-8abc-0123456789ab','poolId':'us-east-1_wzN0wUSdQ','operationId':'11111111-1111-4111-8111-111111111111'};now=1790435000;pk='ACCOUNT#'+j['subject']
 cmd={'PK':pk,'SK':'ACCOUNT_DELETION','schemaVersion':1,'recordVersion':1,'environment':'dev','accountId':j['subject'],'operationId':j['operationId'],'occurredAtEpoch':now-100,'deleteByEpoch':now-100+86400,'eventType':'account.deletion.completed','status':'COMPLETE','completedAtEpoch':now-1,'retainUntilEpoch':now-1+120*86400}
 rows={'ACCOUNT_DELETION':cmd}
 for c in REQUIRED_COMPONENTS:rows['ACCOUNT_DELETION#'+c]={'PK':pk,'SK':'ACCOUNT_DELETION#'+c,'schemaVersion':1,'recordVersion':1,'environment':'dev','component':c,'status':'COMPLETE','eventType':'account.deletion.component.completed','operationId':j['operationId'],'occurredAtEpoch':now-2,'requestOccurredAtEpoch':now-100,'retainUntilEpoch':now-2+120*86400}
 sts=Mock();sts.get_caller_identity.return_value={'Account':o.ACCOUNT};d=Mock();c=Mock();c.exceptions=SimpleNamespace(UserNotFoundException=type('Missing',(Exception,),{}));c.admin_get_user.side_effect=c.exceptions.UserNotFoundException()
 observer=o.Observer(j,ddb=d,cognito=c,sts=sts,now=lambda:now);observer.owned=lambda table,pk:[wire(v) for v in rows.values()] if table==o.LEDGER else []
 baseline={'targetBindingSha256':o.binding(j),'tables':{t:{'tableId':'fixed','rows':{'other-key':'other-value'}} for t in o.TABLES}}
 observer.snapshot=lambda:baseline
 return observer,baseline,rows

def test_exact_twelve_complete_and_absent_identity(world):
 x,b,_=world;r=x.observe(b,Finalizer,plain,REQUIRED_COMPONENTS);assert r['complete'] and r['receiptCount']==12 and r['otherRowsPreserved'] and r['otherRowsCompared']==13
 x.c.admin_delete_user.assert_not_called();x.d.put_item.assert_not_called()

@pytest.mark.parametrize('bad',['missing_receipt','wrong_operation','expired','surviving_control','surviving_job','profile','cognito','other_changed'])
def test_incomplete_or_changed_proofs_never_claim_complete(world,bad):
 x,b,r=world
 if bad=='missing_receipt':r.pop('ACCOUNT_DELETION#PLAY_TOKENS')
 if bad=='wrong_operation':r['ACCOUNT_DELETION#IDENTITY']['operationId']='22222222-2222-4222-8222-222222222222'
 if bad=='expired':r['ACCOUNT_DELETION#HISTORY']['retainUntilEpoch']=1
 if bad=='surviving_control':r['CAMPAIGN_RECOVERY_CONTROL']={'PK':r['ACCOUNT_DELETION']['PK'],'SK':'CAMPAIGN_RECOVERY_CONTROL'}
 if bad=='surviving_job':r['CAMPAIGN_RECOVERY#x']={'PK':r['ACCOUNT_DELETION']['PK'],'SK':'CAMPAIGN_RECOVERY#x'}
 if bad=='profile':
  previous=x.owned;x.owned=lambda t,pk:previous(t,pk) if t==o.LEDGER else [{'PK':{'S':'USER#x'}}]
 if bad=='cognito':x.c.admin_get_user.side_effect=None;x.c.admin_get_user.return_value={}
 if bad=='other_changed':
  import copy
  other=copy.deepcopy(b);other['tables'][o.USERS]['rows']={};x.snapshot=lambda:other
 with pytest.raises(Exception):x.observe(b,Finalizer,plain,REQUIRED_COMPONENTS)

def test_pending_not_complete(world):
 x,b,r=world;r['ACCOUNT_DELETION']['status']='REQUESTED';result=x.observe(b,Finalizer,plain,REQUIRED_COMPONENTS);assert not result['complete'];x.c.admin_get_user.assert_not_called()
