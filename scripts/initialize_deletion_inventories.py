"""Four reviewed marker rows only; default offline, no approval generation."""
import argparse, hashlib, importlib, json, re, subprocess, sys, time
from pathlib import Path
ACCOUNT='107827791950';REGION='us-east-1';ENV='dev'
TABLES={'pipeline':'trustcheckradar-dev-campaign-pipeline','ledger':'trustcheckradar-dev-deletion-ledger'}
KINDS=('locator','recovery','completion','account')
TARGETS={'locator':('pipeline','CAMPAIGN_LOCATORS'),'recovery':('ledger','CAMPAIGN_RECOVERY_INVENTORY'),'completion':('ledger','CAMPAIGN_COMPLETION_INVENTORY'),'account':('ledger','ACCOUNT_DATA_INVENTORY')}
WORKERS={'trustcheckradar-dev-campaign-'+v for v in ('observation-publisher','cluster-aggregator','deletion-bridge','lifecycle')}
API='trustcheckradar-dev-account-data-api'
class Refused(RuntimeError):pass
def need(v):
 if not v:raise Refused('INVENTORY_WRITE_UNVERIFIED')
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def pairs(values):
 out={}
 for k,v in values:need(k not in out);out[k]=v
 return out
def document(path):
 raw=Path(path).read_bytes();need(len(raw)<=4*1024*1024);return raw,json.loads(raw,object_pairs_hook=pairs)
def load_contracts(root,sha):
 root=Path(root).resolve();need(subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()==sha)
 need(not subprocess.check_output(['git','-C',str(root),'status','--porcelain'],text=True).strip())
 sys.path[:0]=[str(root/'src/campaign_deletion_bridge'),str(root/'src')]
 from shared_campaign_locators import core as L
 from shared_campaign_recovery import records as R
 from shared_account_finalization import service as A
 from completion import Completion
 return L,R,A,Completion

def validate(p,contracts,now):
 L,R,A,C=contracts
 need(type(p) is dict and set(p)=={'schemaVersion','sourceCommit','tables','markers','manifests','closedFunctions','approval'})
 need(type(p['schemaVersion']) is int and p['schemaVersion']==1 and re.fullmatch('[0-9a-f]{40}',p['sourceCommit']))
 need(set(p['tables'])==set(TABLES) and set(p['markers'])==set(KINDS) and set(p['manifests'])==set(KINDS))
 for name,table in p['tables'].items():
  need(set(table)=={'name','tableId'} and table['name']==TABLES[name] and isinstance(table['tableId'],str) and re.fullmatch('[0-9a-f-]{36}',table['tableId']))
 for kind in KINDS:
  spec=p['manifests'][kind];need(set(spec)=={'path','sha256'})
  raw,m=document(spec['path']);need(hashlib.sha256(raw).hexdigest()==spec['sha256']==p['markers'][kind]['manifestSha256'])
  need(type(m) is dict and m.get('account')==ACCOUNT and m.get('region')==REGION and m.get('environment')==ENV)
 epochs={r['approvedAtEpoch'] for r in p['markers'].values()};need(len(epochs)==1 and type(next(iter(epochs))) is int and 0<next(iter(epochs))<now)
 spec=p['approval'];need(type(spec) is dict and set(spec)=={'path','sha256'})
 raw,approval=document(spec['path']);need(hashlib.sha256(raw).hexdigest()==spec['sha256'])
 need(type(approval) is dict and set(approval)=={'schemaVersion','account','region','environment','sourceCommit','manifestSha256','runtimeSnapshotSha256','tableIdentitySha256','approvedAtEpoch','reviewReference','decision'})
 need(type(approval['schemaVersion']) is int and approval['schemaVersion']==1 and approval['account']==ACCOUNT and approval['region']==REGION and approval['environment']==ENV and approval['sourceCommit']==p['sourceCommit'])
 need(approval['decision']=='APPROVED_FOR_MARKER_INITIALIZATION' and type(approval['reviewReference']) is str and 1<=len(approval['reviewReference'])<=1024)
 need(approval['manifestSha256']=={k:p['manifests'][k]['sha256'] for k in KINDS} and approval['runtimeSnapshotSha256']==digest(p['closedFunctions']) and approval['tableIdentitySha256']==digest(p['tables']))
 need(type(approval['approvedAtEpoch']) is int and approval['approvedAtEpoch']==next(iter(epochs)))
 l,r,c,a=[p['markers'][k] for k in KINDS]
 class Reader:
  def get_item(self,**kw):return {'Item':L.serialize(l)}
 L.load_inventory(Reader(),TABLES['pipeline'],ENV,l['manifestSha256'],l['revision'],now)
 need(l['minimumPeriodId']==now//1209600)
 R.validate_inventory(r,ENV,r['manifestSha256'],r['revision'],now)
 validator=C.__new__(C);validator.env=ENV;validator.manifest=c['manifestSha256'];validator.revision=c['revision'];validator.locator_manifest=l['manifestSha256'];validator.locator_revision=l['revision'];validator.recovery_manifest=r['manifestSha256'];validator.recovery_revision=r['revision']
 # A validation-only later clock; this neither constructs nor writes a command.
 validator._marker(c,{'occurredAtEpoch':now},now)
 A.validate_inventory(a,ENV,a['manifestSha256'],A.REQUIRED_COMPONENTS,expected_revision=a['revision'],now_epoch=now)
 functions=p['closedFunctions'];need(type(functions) is dict and WORKERS|{API}<=set(functions) and len(functions)<=20)
 for name,pin in functions.items():
  need(re.fullmatch('trustcheckradar-dev-[a-z0-9-]+',name) and set(pin)=={'codeSha256','revisionId','environmentSha256'})
  need(re.fullmatch('[0-9a-f]{64}',pin['environmentSha256']))
 return p

class Writer:
 def __init__(self,p,contracts,*,sts,ddb,lam,now=lambda:int(time.time())):self.p=p;self.contracts=contracts;self.sts=sts;self.d=ddb;self.l=lam;self.now=now
 def boundary(self):
  p=self.p;need(self.sts.get_caller_identity()['Account']==ACCOUNT)
  for table in p['tables'].values():
   d=self.d.describe_table(TableName=table['name'])['Table'];need(d['TableArn']==f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/'+table['name'] and d['TableId']==table['tableId'] and d['TableStatus']=='ACTIVE')
  for name,pin in p['closedFunctions'].items():
   c=self.l.get_function_configuration(FunctionName=name);env=c.get('Environment',{}).get('Variables',{})
   need(c['FunctionArn']==f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:'+name)
   need(c['CodeSha256']==pin['codeSha256'] and c['RevisionId']==pin['revisionId'] and c['LastUpdateStatus']=='Successful' and digest(env)==pin['environmentSha256'])
   if name in WORKERS:need(env.get('CAMPAIGN_PERIOD_ADMISSION_ENABLED')=='false')
   if name.endswith('campaign-deletion-bridge'):
    need(all(env.get(k)=='false' for k in ('CAMPAIGN_RECOVERY_ENABLED','CAMPAIGN_DELETION_STREAM_ENABLED','CAMPAIGN_COMPLETION_ENABLED')))
   if name==API:need(all(env.get(k)=='false' for k in ('ACCOUNT_DELETION_ENABLED','ACCOUNT_IDENTITY_FINALIZER_ENABLED','CAMPAIGN_RECOVERY_WRITES_ENABLED')) and json.loads(env.get('ACCOUNT_DELETION_HTTP_SUBJECTS_JSON','[]'))==[])
 def reads(self):
  R=self.contracts[1];rows={}
  for kind in KINDS:
   table,sk=TARGETS[kind];raw=self.d.get_item(TableName=TABLES[table],Key=R.wire({'PK':'INVENTORY#dev','SK':sk}),ConsistentRead=True).get('Item');rows[kind]=R.plain(raw) if raw else None
  return rows
 def validate_observed(self,observed,now):
  R=self.contracts[1]
  need(all(type(observed[k]) is dict and R.wire(observed[k])==R.wire(self.p['markers'][k]) for k in KINDS))
 def apply(self):
  start=self.now();validate(self.p,self.contracts,start);self.boundary();observed=self.reads();wanted=self.p['markers']
  if observed==wanted:
   # Python True==1 is not exact DynamoDB type identity. Revalidate observed
   # SDK rows by exact DynamoDB serialization before positive readback.
   self.validate_observed(observed,start)
   self.boundary();need(self.now()>=start);return {'initialized':False,'exactReadback':True,'idempotent':True}
  need(all(v is None for v in observed.values()))
  self.boundary();now=self.now();need(now>=start);validate(self.p,self.contracts,now)
  R=self.contracts[1];actions=[{'Put':{'TableName':TABLES[TARGETS[k][0]],'Item':R.wire(wanted[k]),'ConditionExpression':'attribute_not_exists(PK) AND attribute_not_exists(SK)'}} for k in KINDS]
  ambiguous=False
  try:self.d.transact_write_items(TransactItems=actions)
  except Exception:ambiguous=True
  observed=self.reads();need(observed==wanted);self.validate_observed(observed,now);self.boundary();finished=self.now();need(finished>=now);validate(self.p,self.contracts,finished)
  return {'initialized':True,'exactReadback':True,'ambiguousWriteAcknowledgment':ambiguous,'mutatingTransactionAttempts':1}

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--plan',required=True);parser.add_argument('--plan-sha256',required=True);parser.add_argument('--source-root',required=True);parser.add_argument('--execute',action='store_true');a=parser.parse_args()
 raw,p=document(a.plan);need(hashlib.sha256(raw).hexdigest()==a.plan_sha256);contracts=load_contracts(a.source_root,p['sourceCommit']);validate(p,contracts,int(time.time()))
 if not a.execute:print(json.dumps({'offlineValidated':True,'awsCalls':0,'markers':4}));return
 import boto3
 from botocore.config import Config
 s=boto3.Session(profile_name='trustcheckradar',region_name=REGION);cfg=Config(connect_timeout=3,read_timeout=8,retries={'total_max_attempts':1})
 clients={'sts':s.client('sts',config=cfg),'ddb':s.client('dynamodb',config=cfg),'lam':s.client('lambda',config=cfg)}
 try:print(json.dumps(Writer(p,contracts,**clients).apply()))
 finally:
  for c in clients.values():c.close()
if __name__=='__main__':
 try:main()
 except Exception:print(json.dumps({'failed':True,'code':'INVENTORY_WRITE_UNVERIFIED','inspectExactReadbackBeforeRetry':True}));raise SystemExit(2) from None
