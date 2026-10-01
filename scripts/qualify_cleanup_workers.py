"""Exactly six root-approved cleanup invocations; no command or receipt injection."""
import argparse,hashlib,json,re,time
from pathlib import Path
ACCOUNT='107827791950';REGION='us-east-1';PREFIX='trustcheckradar-dev-'
LEDGER=PREFIX+'deletion-ledger';HCONTROL=PREFIX+'history-control';HCONTENT=PREFIX+'history-content'
EVENTS={'account-data-api':'reconcile-session-revocation','campaign-deletion-bridge':'reconcile-campaign-cleanup','history-account-deletion-bridge':'reconcile','history-lifecycle':'sweep','v1-authority-deletion':'reconcile-v1-authority-deletion','play-token-deletion':'reconcile-play-token-deletion'}
ALIASED={'v1-authority-deletion','play-token-deletion'}
class Refused(RuntimeError):pass
def need(v):
 if not v:raise Refused('CLEANUP_QUALIFICATION_UNVERIFIED')
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def pairs(values):
 out={}
 for k,v in values:need(k not in out);out[k]=v
 return out
def read(path):
 raw=Path(path).read_bytes();need(len(raw)<=1048576);return raw,json.loads(raw,object_pairs_hook=pairs)
def numeric(v):return type(v) is int and 0<=v<=9007199254740991

def validate_response(kind,result):
 need(type(result) is dict)
 if kind=='campaign-deletion-bridge':
  fields={'RecoveryTicks','RecoveryFailures','CommandsAttempted','CommandsUnverified','CommandsCompleted','CommandsTerminalAcknowledged','SidecarSchemaFailures','IndexCandidatesObserved','ObservedPendingAgeSeconds','ObservedOverdueCommands','RecoveryBudgetExhausted','RecoveryShardTruncated'}
  need(set(result)==fields|{'enabled','complete','receiptEligible'} and result['enabled'] is True and result['complete'] is False and result['receiptEligible'] is False)
  need(all(numeric(result[k]) for k in fields) and result['RecoveryTicks']==1 and all(result[k]==0 for k in fields-{'RecoveryTicks'}))
 elif kind in ALIASED:
  fields={'examined','completed','pending','failed','deleted','overdue','skipped','pages','fullPassCompleted','fullPassAgeSeconds'}
  need(set(result)==fields and all(numeric(v) for v in result.values()) and result['pages']>=1)
  need(all(result[k]==0 for k in ('examined','completed','pending','failed','deleted','overdue')))
 else:
  need(result.get('schemaVersion')==1 and type(result.get('schemaVersion')) is int and result.get('operation')==EVENTS[kind])
  if kind=='history-lifecycle':
   numeric_fields={'expiredContentRecords','expiredControlRecords','expirationBucketQueries','completedErasureJobs','completedRetentionPurges','overdueErasureJobs','observedPendingCompletions','stuckPendingCompletions','redactedReplayRecords','completedAtEpoch'}
   nullable={'expirationCheckpointLagSeconds','oldestPendingCompletionAgeSeconds','oldestPendingErasureAgeSeconds'};booleans={'worksetTruncated'}
   zero=numeric_fields-{'expirationBucketQueries','completedAtEpoch'}
  elif kind=='history-account-deletion-bridge':
   numeric_fields={'scanned','matched','started','alreadyPending','completed','completedAtEpoch'};nullable={'completedPassAtEpoch','fullPassAgeSeconds'};booleans={'worksetTruncated','completedFullPass'};zero={'matched','started','alreadyPending','completed'}
  else:
   zero={'matched','revoked','commandFailures','sessionAlreadyComplete','deviceRecordsDeleted','deviceComponentsCompleted','recoveryRecordsDeleted','recoveryRecordsMinimized','recoveryComponentsCompleted','analysisAbuseRecordsDeleted','analysisAbuseRecordsMinimized','analysisAbuseComponentsCompleted','analysisAbusePolicyBlocked','campaignOutboxRecordsDeleted','campaignOutboxComponentsCompleted','campaignOutboxPolicyBlocked','userProfileRecordsDeleted','userProfileComponentsCompleted','userProfilePolicyBlocked'}
   numeric_fields=zero|{'scanned','completedAtEpoch'};nullable={'completedPassAtEpoch','fullPassAgeSeconds'};booleans={'worksetTruncated','completedFullPass','passHadFailures'}
  need(set(result)=={'schemaVersion','operation'}|numeric_fields|nullable|booleans)
  need(all(numeric(result[k]) for k in numeric_fields) and all(result[k] is None or numeric(result[k]) for k in nullable) and all(type(result[k]) is bool for k in booleans))
  need(all(result[k]==0 for k in zero))
  if 'passHadFailures' in result:need(result['passHadFailures'] is False)
 return {k:v for k,v in result.items() if type(v) in (int,bool) or v is None}

class Runner:
 def __init__(self,plan,*,sts,lam,ddb):self.p=plan;self.sts=sts;self.l=lam;self.d=ddb
 def plan(self):
  p=self.p;need(set(p)=={'schemaVersion','functions','tableIds'} and type(p['schemaVersion']) is int and p['schemaVersion']==1 and set(p['functions'])==set(EVENTS) and set(p['tableIds'])=={LEDGER,HCONTROL,HCONTENT})
  for k,pin in p['functions'].items():
   need(set(pin)=={'codeSha256','revisionId','environmentSha256','version'})
   need(pin['version']=='$LATEST' if k not in ALIASED else isinstance(pin['version'],str) and re.fullmatch('[1-9][0-9]*',pin['version']))
 def runtime(self,kind):
  pin=self.p['functions'][kind];name=PREFIX+kind;args={'FunctionName':name}
  if kind in ALIASED:
   alias=self.l.get_alias(FunctionName=name,Name='live');need(alias['FunctionVersion']==pin['version'] and not alias.get('RoutingConfig',{}).get('AdditionalVersionWeights'));args['Qualifier']='live'
  c=self.l.get_function_configuration(**args);e=c.get('Environment',{}).get('Variables',{})
  need(c['FunctionArn'].split(':function:',1)[0]==f'arn:aws:lambda:{REGION}:{ACCOUNT}' and c['FunctionName']==name and c['CodeSha256']==pin['codeSha256'] and c['RevisionId']==pin['revisionId'] and c['Runtime']=='python3.14' and c['Architectures']==['arm64'] and c['LastUpdateStatus']=='Successful' and digest(e)==pin['environmentSha256'])
  if kind=='account-data-api':need(e.get('ACCOUNT_DELETION_ENABLED')=='true' and json.loads(e.get('ACCOUNT_DELETION_HTTP_SUBJECTS_JSON','[]'))==[])
  if kind=='campaign-deletion-bridge':need(e.get('CAMPAIGN_RECOVERY_ENABLED')=='true' and e.get('CAMPAIGN_COMPLETION_ENABLED')=='true' and e.get('CAMPAIGN_PERIOD_ADMISSION_ENABLED')=='true')
  if kind=='history-account-deletion-bridge':need(e.get('HISTORY_ACCOUNT_DELETION_ENABLED')=='true')
  if kind=='history-lifecycle':need(e.get('HISTORY_LIFECYCLE_ENABLED')=='true' and all(e.get(k)=='false' for k in ('HISTORY_READS_ENABLED','HISTORY_WRITES_ENABLED','HISTORY_MUTATIONS_ENABLED','RECOGNITION_ENABLED')))
  if kind=='v1-authority-deletion':need(e.get('STAGE')=='dev' and e.get('V1_AUTHORITY_DELETION_ENABLED')=='true')
  if kind=='play-token-deletion':need(e.get('STAGE')=='dev' and e.get('PLAY_TOKEN_CLEANUP_ENABLED')=='true' and e.get('PLAY_LIFECYCLE_ENABLED','false')=='false' and e.get('PLAY_CHECKPOINT_POLICY_APPROVED','false')=='false')
  return args
 def preflight(self):
  self.plan();need(self.sts.get_caller_identity()['Account']==ACCOUNT)
  for table,id in self.p['tableIds'].items():
   desc=self.d.describe_table(TableName=table)['Table'];need(desc['TableArn']==f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/'+table and desc['TableId']==id and desc['TableStatus']=='ACTIVE')
  for kind in EVENTS:self.runtime(kind)
 def empty_work(self):
  counts={};deadline=time.monotonic()+60
  for table in (LEDGER,HCONTROL,HCONTENT):
   cursor=None;count=0
   for _ in range(100):
    need(time.monotonic()<deadline)
    args={'TableName':table,'ConsistentRead':True,'Limit':100,'ProjectionExpression':'#p,#s,#t','ExpressionAttributeNames':{'#p':'PK','#s':'SK','#t':'status'}}
    if cursor:args['ExclusiveStartKey']=cursor
    page=self.d.scan(**args)
    for item in page.get('Items',[]):
     pk=item.get('PK',{}).get('S');sk=item.get('SK',{}).get('S');status=item.get('status',{}).get('S');need(isinstance(pk,str) and isinstance(sk,str));count+=1
     if table==LEDGER:
      if pk.startswith('ACCOUNT#') and (sk=='ACCOUNT_DELETION' or sk.startswith('CAMPAIGN_WITHDRAWAL#')):need(status=='COMPLETE')
      need(not sk.startswith('CAMPAIGN_RECOVERY#'))
     elif table==HCONTROL:need(pk=='LIFECYCLE#dev')
     else:raise Refused('CLEANUP_QUALIFICATION_UNVERIFIED')
    cursor=page.get('LastEvaluatedKey')
    if not cursor:break
   else:raise Refused('CLEANUP_QUALIFICATION_UNVERIFIED')
   counts[table]=count
  return counts
 def run(self,save=lambda value:None):
  self.preflight();report={'schemaVersion':1,'cases':[],'actualCommandsInjected':0,'cloudWatchIngestionVerified':False}
  save(report)
  for kind,op in EVENTS.items():
   report['attempting']=kind;save(report)
   category='preflight'
   try:
    counts=self.empty_work();category='runtime-mismatch';args=self.runtime(kind)
    category='invoke-transport'
    response=self.l.invoke(**args,InvocationType='RequestResponse',LogType='None',Payload=json.dumps({'schemaVersion':1,'operation':op}).encode())
    category='invoke-response'
    stream=response['Payload']
    try:raw=stream.read(65537)
    finally:stream.close()
    category='function-error' if response.get('FunctionError') else 'invoke-response'
    need(response.get('StatusCode')==200 and not response.get('FunctionError') and len(raw)<=65536)
    category='runtime-mismatch';need(response.get('ExecutedVersion')==self.p['functions'][kind]['version'])
    category='response-validation';data=json.loads(raw,object_pairs_hook=pairs);values=validate_response(kind,data)
    category='runtime-mismatch';self.runtime(kind)
    category='postflight';self.empty_work()
   except Exception:
    report['failure']={'worker':kind,'category':category};report['allPassed']=False;save(report)
    raise Refused('CLEANUP_QUALIFICATION_UNVERIFIED') from None
   report['cases'].append({'worker':kind,'invocationSucceeded':True,'functionError':False,'counts':values,'preflightObservedRows':counts});save(report)
  report.pop('attempting',None);report['allPassed']=True;save(report);return report

def main():
 p=argparse.ArgumentParser();p.add_argument('--plan',required=True);p.add_argument('--plan-sha256',required=True);p.add_argument('--output',required=True);p.add_argument('--execute',action='store_true');a=p.parse_args();raw,plan=read(a.plan);need(hashlib.sha256(raw).hexdigest()==a.plan_sha256)
 if not a.execute:Runner(plan,sts=None,lam=None,ddb=None).plan();print(json.dumps({'offlineValidated':True,'invokes':0}));return
 import boto3
 from botocore.config import Config
 s=boto3.Session(profile_name='trustcheckradar',region_name=REGION);cfg=Config(connect_timeout=3,read_timeout=120,retries={'total_max_attempts':1});clients={'sts':s.client('sts',config=cfg),'lam':s.client('lambda',config=cfg),'ddb':s.client('dynamodb',config=cfg)}
 try:
  result=Runner(plan,**clients).run(save=lambda r:Path(a.output).write_text(json.dumps(r,indent=2)+'\n'));Path(a.output).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'allPassed':True,'cases':6}))
 finally:
  for c in clients.values():c.close()
if __name__=='__main__':
 try:main()
 except Exception:print(json.dumps({'failed':True,'code':'CLEANUP_QUALIFICATION_UNVERIFIED'}));raise SystemExit(2) from None
