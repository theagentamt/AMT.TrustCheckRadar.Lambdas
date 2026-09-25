"""Native transaction builders. Callers must compose the exact owner/command write."""
from .records import *


def update(table, observed, changes):
    result=match(observed)
    result['UpdateExpression']='SET '+', '.join(f'#u{i} = :u{i}' for i in range(len(changes)))
    result['ExpressionAttributeNames'].update({f'#u{i}':name for i,name in enumerate(changes)})
    result['ExpressionAttributeValues'].update({f':u{i}':value for i,value in enumerate(changes.values())})
    return {'Update':{'TableName':table,'Key':key(observed['PK'],observed['SK']),**result}}


def enqueue_actions(client, table, command):
    """No standalone execution: exact command creation and live owner guard are mandatory."""
    env=command['environment'];validate_command(command,env);pk=command['PK']
    need(read(client,table,pk,JOB_PREFIX+command['operationId']) is None)
    control=read(client,table,pk,CONTROL_SK)
    if control is None:
        existing=client.query(TableName=table,ConsistentRead=True,Limit=1,
            KeyConditionExpression='PK = :pk AND begins_with(SK, :prefix)',
            ExpressionAttributeValues=wire({':pk':pk,':prefix':JOB_PREFIX}))
        need(not existing.get('Items') and not existing.get('LastEvaluatedKey'))
        action=put(table,new_control(pk,env))
    else:
        validate_control(control,env,pk);need(control['state']=='OPEN')
        count=integer(control['pendingJobs'],1);revision=integer(control['revision'],1)
        need(count<9007199254740991 and revision<9007199254740991)
        action=update(table,control,{'pendingJobs':count+1,'revision':revision+1})
    return [action,put(table,new_job(command)),absent(table,pk,'ACCOUNT_DELETION#CAMPAIGN')]


def backfill_actions(client, table, users_table, command):
    """Reviewable actions only; no default apply path or automatic inventory approval."""
    env=command['environment'];validate_command(command,env);pk=command['PK']
    current=read(client,table,pk,command['SK']);need(current==command)
    profile=read(client,users_table,'USER#'+command['accountId'],'PROFILE')
    need(type(profile) is dict and profile.get('sub')==command['accountId']
         and profile.get('status') in ('ACTIVE','PENDING_AGE_GATE','DELETION_REQUESTED'))
    fence=read(client,table,pk,'ACCOUNT_DELETION')
    if fence is not None:validate_command(fence,env);need(fence['SK']=='ACCOUNT_DELETION')
    actions=enqueue_actions(client,table,command)+[condition(table,command),condition(users_table,profile)]
    if command['SK']!='ACCOUNT_DELETION':
        actions.append(condition(table,fence) if fence else absent(table,pk,'ACCOUNT_DELETION'))
    return actions
