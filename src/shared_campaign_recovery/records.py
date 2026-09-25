from copy import deepcopy
from decimal import Decimal
import re
from uuid import UUID
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

INDEX='CampaignRecoveryDueIndex'
CONTROL_SK='CAMPAIGN_RECOVERY_CONTROL'
JOB_PREFIX='CAMPAIGN_RECOVERY#'
SHARDS=16
SUBJECT=re.compile(r'[A-Za-z0-9][A-Za-z0-9._:@-]{0,199}')
JOB_FIELDS={'PK','SK','recordType','schemaVersion','environment','operationId','commandSK',
            'commandOccurredAtEpoch','revision','nextAttemptAtEpoch','campaignRecoveryPartition'}
CONTROL_FIELDS={'PK','SK','recordType','schemaVersion','environment','revision','pendingJobs','state'}
COMMAND_FIELDS={'PK','SK','schemaVersion','recordVersion','environment','eventType','accountId',
                'operationId','status','occurredAtEpoch','deleteByEpoch'}
INVENTORY_FIELDS={'PK','SK','recordType','schemaVersion','environment','revision','coverage',
                  'manifestSha256','approvedAtEpoch','legacyCoverageVerified','writers'}
WRITERS=['account_data_api','campaign_participation','campaign_recovery_backfill']
S,D=TypeSerializer(),TypeDeserializer()

class RecoveryUnavailable(RuntimeError):pass

def need(value):
    if not value:raise RecoveryUnavailable('CAMPAIGN_RECOVERY_UNVERIFIED')

def integer(value,minimum=0):
    need(type(value) in (int,Decimal))
    try:need(value==int(value) and minimum<=value<=9007199254740991)
    except (ValueError,OverflowError,ArithmeticError):raise RecoveryUnavailable('CAMPAIGN_RECOVERY_UNVERIFIED') from None
    return int(value)

def operation(value):
    try:
        result=UUID(value);need(result.version==4 and str(result)==value)
    except (ValueError,TypeError,AttributeError):raise RecoveryUnavailable('CAMPAIGN_RECOVERY_UNVERIFIED') from None
    return value

def account_key(value):
    need(type(value) is str and value.startswith('ACCOUNT#') and SUBJECT.fullmatch(value[8:]))
    return value

def wire(item):return {k:S.serialize(v) for k,v in item.items()}
def plain(item):return {k:D.deserialize(v) for k,v in item.items()}
def key(pk,sk):return {'PK':pk,'SK':sk}
def read(client,table,pk,sk):
    raw=client.get_item(TableName=table,Key=wire(key(pk,sk)),ConsistentRead=True).get('Item')
    return plain(raw) if raw else None

def match(row):
    fields=sorted(row)
    return {'ConditionExpression':' AND '.join(f'#f{i} = :v{i}' for i in range(len(fields))),
            'ExpressionAttributeNames':{f'#f{i}':f for i,f in enumerate(fields)},
            'ExpressionAttributeValues':{f':v{i}':row[f] for i,f in enumerate(fields)}}

def condition(table,row):return {'ConditionCheck':{'TableName':table,'Key':key(row['PK'],row['SK']),**match(row)}}
def absent(table,pk,sk):return {'ConditionCheck':{'TableName':table,'Key':key(pk,sk),'ConditionExpression':'attribute_not_exists(PK)'}}
def put(table,item,observed=None):
    return {'Put':{'TableName':table,'Item':item,**(match(observed) if observed else {'ConditionExpression':'attribute_not_exists(PK)'})}}
def delete(table,row):return {'Delete':{'TableName':table,'Key':key(row['PK'],row['SK']),**match(row)}}
def serialize_actions(actions):
    result=[]
    for action in actions:
        name,values=next(iter(action.items()));values=deepcopy(values)
        for f in ('Key','Item','ExpressionAttributeValues'):
            if f in values:values[f]=wire(values[f])
        result.append({name:values})
    return result

def validate_command(row,environment):
    need(type(row) is dict and row.get('environment')==environment and environment in ('dev','uat','prod'))
    account_key(row.get('PK'));operation(row.get('operationId'))
    account=row.get('accountId');need(type(account) is str and row['PK']=='ACCOUNT#'+account)
    fields=COMMAND_FIELDS
    if row.get('eventType')=='campaign.consent.withdrawn':
        fields=fields|{'consentEpochId'};operation(row.get('consentEpochId'))
        need(row.get('status')=='PENDING' and row.get('SK')=='CAMPAIGN_WITHDRAWAL#'+row['operationId'])
    else:need(row.get('eventType')=='account.deletion.requested' and row.get('status')=='REQUESTED' and row.get('SK')=='ACCOUNT_DELETION')
    need(set(row)==fields and integer(row.get('schemaVersion'),1)==1 and integer(row.get('recordVersion'),1)==1)
    need(integer(row.get('deleteByEpoch'),1)==integer(row.get('occurredAtEpoch'),1)+86400)
    return row

def shard(op):return UUID(operation(op)).int%SHARDS
def partition(environment,number):return f'CAMPAIGN_RECOVERY#{environment}#{number:02}'

def new_job(command):
    return {'PK':command['PK'],'SK':JOB_PREFIX+command['operationId'],'recordType':'CAMPAIGN_RECOVERY',
            'schemaVersion':1,'environment':command['environment'],'operationId':command['operationId'],
            'commandSK':command['SK'],'commandOccurredAtEpoch':command['occurredAtEpoch'],'revision':1,
            'nextAttemptAtEpoch':command['occurredAtEpoch'],
            'campaignRecoveryPartition':partition(command['environment'],shard(command['operationId']))}

def validate_job(row,environment,command=None):
    need(type(row) is dict and set(row)==JOB_FIELDS)
    account_key(row['PK']);operation(row['operationId'])
    need(row['SK']==JOB_PREFIX+row['operationId'] and row['recordType']=='CAMPAIGN_RECOVERY'
         and integer(row['schemaVersion'],1)==1 and row['environment']==environment)
    need(row['commandSK'] in ('ACCOUNT_DELETION','CAMPAIGN_WITHDRAWAL#'+row['operationId']))
    integer(row['revision'],1);integer(row['commandOccurredAtEpoch'],1)
    need(integer(row['nextAttemptAtEpoch'],1)>=row['commandOccurredAtEpoch'])
    need(row['campaignRecoveryPartition']==partition(environment,shard(row['operationId'])))
    if command is not None:
        validate_command(command,environment)
        need(row['PK']==command['PK'] and row['operationId']==command['operationId']
             and row['commandSK']==command['SK'] and row['commandOccurredAtEpoch']==command['occurredAtEpoch'])
    return row

def validate_control(row,environment,pk):
    need(type(row) is dict and set(row)==CONTROL_FIELDS and row['PK']==account_key(pk)
         and row['SK']==CONTROL_SK and row['recordType']=='CAMPAIGN_RECOVERY_CONTROL'
         and integer(row['schemaVersion'],1)==1 and row['environment']==environment)
    integer(row['revision'],1);count=integer(row['pendingJobs'])
    need(row['state']=='OPEN' and count>0 or row['state']=='SEALED' and count==0)
    return row

def new_control(pk,environment):
    return {'PK':pk,'SK':CONTROL_SK,'recordType':'CAMPAIGN_RECOVERY_CONTROL','schemaVersion':1,
            'environment':environment,'revision':1,'pendingJobs':1,'state':'OPEN'}

def validate_inventory(row,environment,manifest,revision,now):
    need(type(manifest) is str and re.fullmatch(r'[0-9a-f]{64}',manifest) and type(revision) is int and revision>0)
    need(type(row) is dict and set(row)==INVENTORY_FIELDS and row['PK']=='INVENTORY#'+environment
         and row['SK']=='CAMPAIGN_RECOVERY_INVENTORY' and row['recordType']=='CAMPAIGN_RECOVERY_INVENTORY'
         and integer(row['schemaVersion'],1)==1 and row['environment']==environment
         and integer(row['revision'],1)==revision and row['manifestSha256']==manifest
         and row['coverage']=='VERIFIED_COMPLETE' and row['legacyCoverageVerified'] is True
         and row['writers']==WRITERS and 0<integer(row['approvedAtEpoch'],1)<=now)
    return row
