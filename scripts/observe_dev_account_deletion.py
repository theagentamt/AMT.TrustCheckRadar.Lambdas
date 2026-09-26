"""Read-only designated-subject completion and other-row preservation observer."""
import argparse,base64,hashlib,json,os,sys,time,subprocess,re
from pathlib import Path
from uuid import UUID
ACCOUNT='107827791950';REGION='us-east-1';PREFIX='trustcheckradar-dev-'
TABLES={PREFIX+s for s in ('users','device-bindings','device-recovery-control','analysis-abuse-control','web-risk-cache','purchase-entitlements','history-control','history-content','campaign-pipeline','campaign-outbox','campaign-intelligence','deletion-ledger','play-tokens')}
LEDGER=PREFIX+'deletion-ledger';USERS=PREFIX+'users'
class Refused(RuntimeError):pass
def need(v):
 if not v:raise Refused('DELETION_OBSERVATION_UNVERIFIED')
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),default=lambda b:base64.b64encode(b).decode()).encode()).hexdigest()
def load(path):
 need(not Path(path).is_symlink() and Path(path).stat().st_mode&0o077==0);return json.loads(Path(path).read_text())
def binding(j):return digest([j['poolId'],j['subject'],j['operationId']])
def journal(path):
 j=load(path);need(j.get('stage') in ('PROFILE_READY','POST_ATTEMPTED','ACCEPTED'))
 need(j.get('poolId')=='us-east-1_wzN0wUSdQ' and j.get('clientId')=='5kvl9a8jo4fr1qqnci27tdabk4' and str(UUID(j['subject']))==j['subject'] and str(UUID(j['operationId']))==j['operationId'] and UUID(j['operationId']).version==4)
 need(j.get('email')=='deletion-qual-'+j['runId']+'@example.invalid');return j

def exempt(row,j):
 pk=row.get('PK',{}).get('S');sk=row.get('SK',{}).get('S');need(isinstance(pk,str) and isinstance(sk,str))
 if pk in ('USER#'+j['subject'],'ACCOUNT#'+j['subject']) or pk.startswith('USER#'+j['subject']+'#'):return True
 return (pk=='LIFECYCLE#dev' and sk in ('ACCOUNT_DELETION_RECONCILIATION','ACCOUNT_DELETION_SESSION_REVOCATION_RECONCILIATION','EXPIRATION#HISTORY','EXPIRATION#CONTROL','RECONCILIATION#HISTORY','RECONCILIATION#CONTROL') or pk=='V1#CONTROL' and sk=='DELETION_RECONCILIATION_CURSOR' or pk=='PLAY#CONTROL' and sk=='TOKEN_DELETION_CURSOR')

class Observer:
 def __init__(self,j,*,ddb,cognito,sts,now=lambda:int(time.time())):self.j=j;self.d=ddb;self.c=cognito;self.sts=sts;self.now=now;self.deadline=time.monotonic()+180
 def preflight(self):need(self.sts.get_caller_identity()['Account']==ACCOUNT)
 def snapshot(self):
  self.preflight();out={};deadline=time.monotonic()+180
  for table in sorted(TABLES):
   desc=self.d.describe_table(TableName=table)['Table'];need(desc['TableArn']==f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/'+table and desc['TableStatus']=='ACTIVE')
   rows={};cursor=None
   for _ in range(100):
    need(time.monotonic()<min(deadline,self.deadline));args={'TableName':table,'ConsistentRead':True,'Limit':100}
    if cursor:args['ExclusiveStartKey']=cursor
    page=self.d.scan(**args)
    for row in page.get('Items',[]):
     if not exempt(row,self.j):rows[digest({'PK':row['PK'],'SK':row['SK']})]=digest(row)
    cursor=page.get('LastEvaluatedKey')
    if not cursor:break
   else:raise Refused('DELETION_OBSERVATION_UNVERIFIED')
   out[table]={'tableId':desc['TableId'],'rows':rows}
  return {'schemaVersion':1,'targetBindingSha256':binding(self.j),'tables':out,'observedAtEpoch':self.now()}
 def owned(self,table,pk):
  rows=[];cursor=None
  for _ in range(100):
   need(time.monotonic()<self.deadline)
   args={'TableName':table,'ConsistentRead':True,'Limit':100,'KeyConditionExpression':'PK = :pk','ExpressionAttributeValues':{':pk':{'S':pk}}}
   if cursor:args['ExclusiveStartKey']=cursor
   page=self.d.query(**args);rows.extend(page.get('Items',[]));cursor=page.get('LastEvaluatedKey')
   if not cursor:return rows
  raise Refused('DELETION_OBSERVATION_UNVERIFIED')
 def observe(self,baseline,Finalizer,plain,components):
  self.preflight();j=self.j;need(baseline.get('targetBindingSha256')==binding(j) and set(baseline['tables'])==TABLES)
  rows={plain(r)['SK']:plain(r) for r in self.owned(LEDGER,'ACCOUNT#'+j['subject'])};current=rows.get('ACCOUNT_DELETION');need(current is not None and current.get('operationId')==j['operationId'])
  counts={'receiptCount':sum(('ACCOUNT_DELETION#'+c) in rows for c in components),'complete':False,'cognitoAbsent':False,'otherRowsPreserved':False}
  if current.get('status')!='COMPLETE':return counts
  original={k:v for k,v in current.items() if k not in ('completedAtEpoch','retainUntilEpoch')};original.update(status='REQUESTED',eventType='account.deletion.requested')
  now=self.now();Finalizer._validate_command(original,now,'dev');need(Finalizer._completed(current,original,now))
  validator=Finalizer.__new__(Finalizer);validator.environment='dev'
  for component in components:validator._validate_receipt(rows.get('ACCOUNT_DELETION#'+component),original,component,now)
  need('CAMPAIGN_RECOVERY_CONTROL' not in rows and not any(k.startswith('CAMPAIGN_RECOVERY#') for k in rows))
  need(not self.owned(USERS,'USER#'+j['subject']))
  try:self.c.admin_get_user(UserPoolId=j['poolId'],Username=j['subject'])
  except self.c.exceptions.UserNotFoundException:pass
  else:raise Refused('DELETION_OBSERVATION_UNVERIFIED')
  after=self.snapshot();preserved=0
  for table,before in baseline['tables'].items():
   seen=after['tables'][table];need(seen['tableId']==before['tableId'])
   for key,sha in before['rows'].items():need(seen['rows'].get(key)==sha);preserved+=1
  final={plain(r)['SK']:plain(r) for r in self.owned(LEDGER,'ACCOUNT#'+j['subject'])}
  need(final==rows and not self.owned(USERS,'USER#'+j['subject']))
  fresh=self.now();need(fresh>=now)
  for component in components:validator._validate_receipt(final['ACCOUNT_DELETION#'+component],original,component,fresh)
  counts.update(complete=True,cognitoAbsent=True,otherRowsPreserved=True,otherRowsCompared=preserved,receiptCount=12,originalOperationMatched=True,recoveryControlAbsent=True,userPartitionEmpty=True)
  return counts

def main():
 p=argparse.ArgumentParser();p.add_argument('operation',choices=['baseline','observe']);p.add_argument('--journal',required=True);p.add_argument('--baseline',required=True);p.add_argument('--source-root',required=True);p.add_argument('--source-commit',required=True);p.add_argument('--baseline-sha256');p.add_argument('--output',required=True);a=p.parse_args();j=journal(a.journal)
 import boto3
 from botocore.config import Config
 from boto3.dynamodb.types import TypeDeserializer
 need(subprocess.check_output(['git','-C',a.source_root,'rev-parse','HEAD'],text=True).strip()==a.source_commit and not subprocess.check_output(['git','-C',a.source_root,'status','--porcelain'],text=True).strip())
 sys.path.insert(0,str(Path(a.source_root)/'src'));from shared_account_finalization import Finalizer,REQUIRED_COMPONENTS
 s=boto3.Session(profile_name='trustcheckradar',region_name=REGION);cfg=Config(connect_timeout=3,read_timeout=8,retries={'total_max_attempts':1});clients={'sts':s.client('sts',config=cfg),'ddb':s.client('dynamodb',config=cfg),'cognito':s.client('cognito-idp',config=cfg)}
 try:
  observer=Observer(j,**clients)
  if a.operation=='baseline':
   result=observer.snapshot();fd=os.open(a.baseline,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
   with os.fdopen(fd,'w') as f:json.dump(result,f);f.flush();os.fsync(f.fileno())
   report={'baselineCaptured':True,'tables':13,'retainedRows':sum(len(t['rows']) for t in result['tables'].values()),'baselineSha256':hashlib.sha256(Path(a.baseline).read_bytes()).hexdigest()}
  else:
   need(a.baseline_sha256 is not None and hashlib.sha256(Path(a.baseline).read_bytes()).hexdigest()==a.baseline_sha256)
   d=TypeDeserializer();report=observer.observe(load(a.baseline),Finalizer,lambda row:{k:d.deserialize(v) for k,v in row.items()},REQUIRED_COMPONENTS)
  Path(a.output).write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
 finally:
  for c in clients.values():c.close()
if __name__=='__main__':
 try:main()
 except Exception:print(json.dumps({'failed':True,'code':'DELETION_OBSERVATION_UNVERIFIED'}));raise SystemExit(2) from None
