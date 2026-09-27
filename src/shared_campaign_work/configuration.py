"""Closed runtime pins and resource-bound, externally approved work inventory."""
import os
import re
from uuid import UUID
from boto3.dynamodb.types import TypeDeserializer,TypeSerializer
from .records import need,integer,identity

WRITERS=['conversation_analysis','account_data_api','campaign_observation_publisher',
         'campaign_cluster_aggregator','campaign_deletion_bridge','campaign_lifecycle']
MARKER_FIELDS={'PK','SK','recordType','schemaVersion','environment','coverage','revision','manifestSha256',
               'approvedAtEpoch','admissionGeneration','locatorManifestSha256','locatorInventoryRevision',
               'minimumPeriodId','resources','writers','baseline','restoreInvalidation'}
S=TypeSerializer();D=TypeDeserializer()
def wire(row):return {k:S.serialize(v) for k,v in row.items()}
def plain(row):return {k:D.deserialize(v) for k,v in row.items()}


def enabled():return os.environ.get('CAMPAIGN_PERIOD_WORK_ENABLED','false')=='true'


def pins():
    need(enabled())
    from shared_campaign_locators import period
    generation=period.configuration();account,region=period.identity()
    environment=os.environ.get('APP_ENVIRONMENT','')
    identity(environment,generation)
    manifest=os.environ.get('CAMPAIGN_PERIOD_WORK_MANIFEST_SHA256','')
    raw=os.environ.get('CAMPAIGN_PERIOD_WORK_INVENTORY_REVISION','')
    need(re.fullmatch('[0-9a-f]{64}',manifest) and re.fullmatch('[1-9][0-9]*',raw))
    resources={}
    for family in ('pipeline','outbox'):
        name=os.environ.get('CAMPAIGN_PERIOD_WORK_'+family.upper()+'_TABLE_NAME','')
        table_id=os.environ.get('CAMPAIGN_PERIOD_WORK_'+family.upper()+'_TABLE_ID','')
        need(re.fullmatch('[A-Za-z0-9_.-]{3,255}',name))
        try:need(str(UUID(table_id))==table_id)
        except (ValueError,TypeError,AttributeError):need(False)
        resources[family]={'tableName':name,'tableId':table_id}
    need(resources['pipeline']['tableName']!=resources['outbox']['tableName'])
    return {'environment':environment,'generation':generation,'account':account,'region':region,
            'manifest':manifest,'revision':integer(int(raw),1),'resources':resources}


def validate_marker(row,config,now):
    need(type(row) is dict and set(row)==MARKER_FIELDS)
    need(row['PK']=='INVENTORY#'+config['environment'] and row['SK']=='CAMPAIGN_PERIOD_WORK'
         and row['recordType']=='CAMPAIGN_PERIOD_WORK_INVENTORY' and integer(row['schemaVersion'],1,1)==1
         and row['environment']==config['environment'] and row['coverage']=='VERIFIED_COMPLETE'
         and integer(row['revision'],1)==config['revision'] and row['manifestSha256']==config['manifest']
         and row['admissionGeneration']==config['generation'] and row['resources']==config['resources']
         and row['writers']==WRITERS and row['baseline']=='EXACT_ALL_TARGETS_INDEXED'
         and row['restoreInvalidation']=='REQUIRES_NEW_GENERATION')
    integer(row['approvedAtEpoch'],1,integer(now,1));integer(row['minimumPeriodId'])
    need(type(row['locatorManifestSha256']) is str and re.fullmatch('[0-9a-f]{64}',row['locatorManifestSha256']))
    integer(row['locatorInventoryRevision'],1)
    return row


def binding(client,config,remaining_ms):
    for family,resource in config['resources'].items():
        need(remaining_ms()>=6000)
        table=client.describe_table(TableName=resource['tableName']).get('Table')
        need(type(table) is dict and table.get('TableName')==resource['tableName']
             and table.get('TableId')==resource['tableId'] and table.get('TableStatus')=='ACTIVE'
             and table.get('TableArn')==f"arn:aws:dynamodb:{config['region']}:{config['account']}:table/{resource['tableName']}")


def exact(row):
    fields=[(k,v) for k,v in row.items() if k not in ('PK','SK')]
    need(bool(fields))
    return {'ConditionExpression':' AND '.join(f'#w{i} = :w{i}' for i in range(len(fields))),
            'ExpressionAttributeNames':{f'#w{i}':k for i,(k,_) in enumerate(fields)},
            'ExpressionAttributeValues':wire({f':w{i}':v for i,(_,v) in enumerate(fields)})}


def condition(table,row):
    return {'ConditionCheck':{'TableName':table,'Key':wire({'PK':row['PK'],'SK':row['SK']}),**exact(row)}}
