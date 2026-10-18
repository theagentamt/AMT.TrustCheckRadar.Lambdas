"""Offline actual-store counterexample; no real AWS or source modification."""
from pathlib import Path
import json,datetime,hashlib,os,copy
import boto3
from botocore.config import Config
from moto import mock_aws
from operations_reporting.reducer import aggregate_day,day_bounds
from operations_reporting.store import AggregateStore
from operations_reporting.revisions import validate_summary
root=Path.cwd();fixtures=json.loads((root/'tests/operations_reporting/fixtures.json').read_bytes())
events=[x['event']for x in fixtures['events']]
accepted=next(x for x in events if x['eventType']=='check_accepted'and x['service']=='message_check'and any(y['eventType']=='check_terminal'and y['logicalCheckRef']==x['logicalCheckRef']and y['processingOutcome']=='complete'for y in events))
terminal=next(y for y in events if y['eventType']=='check_terminal'and y['logicalCheckRef']==accepted['logicalCheckRef']and y['processingOutcome']=='complete')
day='2026-10-03';start,end=day_bounds(day)
def pair(number):
 a,b=copy.deepcopy(accepted),copy.deepcopy(terminal)
 for e in(a,b):e['logicalCheckRef']='lc1_'+format(number,'032x')
 a['eventId']='op1_'+format(100+number,'032x');b['eventId']='op1_'+format(200+number,'032x')
 return[a,b]
a_events=pair(1);b_events=pair(2)+pair(3)
assert not({e['logicalCheckRef']for e in a_events}&{e['logicalCheckRef']for e in b_events})
a=validate_summary(aggregate_day(a_events,[],local_day=day,environment='dev',as_of_epoch=end+60))
b=validate_summary(aggregate_day(b_events,[],local_day=day,environment='dev',as_of_epoch=end+86400+60))
with mock_aws():
 c=boto3.client('dynamodb',region_name='us-east-1',aws_access_key_id='synthetic',aws_secret_access_key='synthetic',config=Config(retries={'total_max_attempts':1}))
 table='cohort-isolation-synthetic'
 c.create_table(TableName=table,KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}],BillingMode='PAY_PER_REQUEST')
 first=AggregateStore(c,table,environment='dev',now=lambda:end+60).commit(a)
 second=AggregateStore(c,table,environment='dev',now=lambda:end+86400+60).commit(b)
 assert first['decision']==second['decision']=='COMMITTED'
 assert first['record']['report']['accepted']==1 and second['record']['report']['accepted']==2
 assert second['record']['previousRecordSha256']==first['record']['recordSha256']
 assert second['record']['expiresAtEpoch']==first['record']['expiresAtEpoch']==start+90*86400
 result={'schemaVersion':1,'observedAtUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'result':'REPRODUCED_SCOPE_AMBIGUITY_EXISTING_CONTRACT','offlineMotoOnly':True,'sourceModified':False,'sourceCohortsHaveDisjointLogicalReferences':True,'firstObservedAccepted':1,'replacementObservedAccepted':2,'firstDecision':first['decision'],'replacementDecision':second['decision'],'revisionChainAndMonotonicChecksPass':True,'existingStoreKeyDimensions':['environment','localDate'],'producerCoverage':'not_qualified','ownerOrCohortIdentityInSummary':False,'retentionUnchanged':True,'actualAwsCalls':0,'endpointCalls':0,'runtimeChanges':0,'scriptSha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'sourcePins':{p:hashlib.sha256((root/p).read_bytes()).hexdigest()for p in ['src/operations_reporting/reducer.py','src/operations_reporting/revisions.py','src/operations_reporting/store.py']},'limit':'Synthetic disjoint source populations demonstrate absent scope binding; no actual selected actor or deployed store was read.'}
 p=Path('/tmp/amt-sec237-cohort-counterexample.json');fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w')as f:json.dump(result,f,indent=2);f.write('\n')
 print(json.dumps(result))
