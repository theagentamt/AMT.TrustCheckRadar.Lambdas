"""Bounded anonymous aggregate expiry; GSI discovery is never erasure proof."""
import hashlib
import os
import re
from uuid import UUID
from . import configuration as C,records as R

CURSOR_FIELDS={'PK','SK','recordType','schemaVersion','environment','generation','manifestSha256','inventoryRevision',
 'intelligenceTableId','revision','shard','highWaterEpoch','cursor','expiresAt','lastFullPassAtEpoch','passIncomplete'}
AGGREGATE_FIELDS={'PK','SK','campaignId','schemaVersion','taxonomyVersion','categoryId','periodWeek','state',
 'contributorCount','submissionCount','dimensionSchemaVersion','languageIds','tacticIds','channelIds',
 'contributorCountBand','submissionCountBand','riskBand','summaryKey','trendDirection','expiresAt','version','environment','expiryPartition'}
METRICS=('AggregateHeartbeat','AggregateFailures','AggregateWorkDeleted','AggregateWorkUnverified',
 'AggregateOverdueObserved','AggregateFullPassAgeSeconds')


def partition(environment,identifier):
    R.need(environment in ('dev','uat','prod') and R.canonical_uuid(identifier))
    return f'EXPIRY#{environment}#{int(hashlib.sha256(identifier.encode()).hexdigest(),16)%16:02d}'


def index_key(row,environment,shard,high):
    shard=R.integer(shard,0,15)
    R.need(type(row) is dict and set(row)=={'PK','SK','expiryPartition','expiresAt'})
    R.need(type(row['PK']) is str and row['PK'].startswith('CAMPAIGN#') and (row['SK']=='AGGREGATE' or type(row['SK']) is str and row['SK'].startswith('AUDIT#') and R.canonical_uuid(row['SK'][6:])))
    ident=row['PK'][9:];R.need(row['expiryPartition']==partition(environment,ident)==f'EXPIRY#{environment}#{shard:02d}')
    R.integer(row['expiresAt'],1,high)
    return row


def validate(row,environment):
    if type(row) is dict and type(row.get('SK')) is str and row['SK'].startswith('AUDIT#'):
        fields={'PK','SK','schemaVersion','action','fromState','toState','reasonCode','reviewerRole','auditId','expiresAt','expiryPartition'}
        R.need(set(row)==fields and R.canonical_uuid(row['auditId']) and row['SK']=='AUDIT#'+row['auditId']
               and type(row['PK']) is str and row['PK'].startswith('CAMPAIGN#')
               and row['expiryPartition']==partition(environment,row['PK'][9:]))
        R.integer(row['schemaVersion'],1,1);R.integer(row['expiresAt'],1)
        transitions={('PENDING_REVIEW','confirm','CONFIRMED'),('PENDING_REVIEW','suppress','SUPPRESSED'),
            ('CONFIRMED','publish','PUBLISHED'),('CONFIRMED','suppress','SUPPRESSED'),('PUBLISHED','emergency_suppress','SUPPRESSED')}
        R.need((row['fromState'],row['action'],row['toState']) in transitions
            and row['reasonCode'] in ('quality_verified','privacy_verified','policy_violation','false_positive','emergency')
            and type(row['reviewerRole']) is str and re.fullmatch('[A-Za-z0-9_-]{1,128}',row['reviewerRole']))
        return row
    R.need(type(row) is dict and set(row) in (AGGREGATE_FIELDS,AGGREGATE_FIELDS|{'GSI1PK','GSI1SK'}))
    R.need(R.canonical_uuid(row['campaignId']) and row['PK']=='CAMPAIGN#'+row['campaignId'] and row['SK']=='AGGREGATE'
           and row['environment']==environment and row['expiryPartition']==partition(environment,row['campaignId'])
           and row['state'] in ('PENDING_REVIEW','CONFIRMED','PUBLISHED','SUPPRESSED'))
    for key in ('schemaVersion','taxonomyVersion','dimensionSchemaVersion'):R.integer(row[key],1,1)
    R.integer(row['version'],1);R.integer(row['expiresAt'],1)
    R.integer(row['contributorCount'],10);R.integer(row['submissionCount'],row['contributorCount'],row['contributorCount']*3)
    if row['state']=='PUBLISHED':R.need(row.get('GSI1PK')=='STATE#PUBLISHED' and row.get('GSI1SK')==row['periodWeek']+'#'+row['PK'])
    else:R.need('GSI1PK' not in row and 'GSI1SK' not in row)
    for field in ('languageIds','tacticIds','channelIds'):
        R.need(type(row[field]) is list and len(row[field])<=100 and all(type(v) is str and re.fullmatch('[a-z][a-z0-9_.-]{0,63}',v) for v in row[field]))
    for field in ('categoryId','periodWeek','contributorCountBand','submissionCountBand','riskBand','summaryKey','trendDirection'):
        R.need(type(row[field]) is str and 0<len(row[field])<=128)
    return row


def tick(client,cloudwatch,*,now,remaining_ms=lambda:30000):
    R.need(os.environ.get('CAMPAIGN_PERIOD_LIFECYCLE_ENABLED','false')=='true')
    config=C.pins();d=C.BudgetClient(client,remaining_ms);start=R.integer(now(),1)
    table=os.environ.get('INTELLIGENCE_TABLE_NAME','');table_id=os.environ.get('CAMPAIGN_AGGREGATE_TABLE_ID','')
    R.need(re.fullmatch('[A-Za-z0-9_.-]{3,255}',table))
    try:R.need(str(UUID(table_id))==table_id)
    except (ValueError,TypeError,AttributeError):raise R.WorkUnavailable() from None
    pipeline=config['resources']['pipeline']['tableName'];metrics={name:0 for name in METRICS}
    def get(t,k):
        raw=d.get_item(TableName=t,Key=C.wire(k),ConsistentRead=True).get('Item');return C.plain(raw) if raw else None
    def emit():
        C.BudgetClient(cloudwatch,remaining_ms).put_metric_data(Namespace='TrustCheckRadar/Campaign',MetricData=[{
            'MetricName':name,'Dimensions':[{'Name':'Environment','Value':config['environment']}],
            'Value':value,'Unit':'Seconds' if name.endswith('AgeSeconds') else 'Count'} for name,value in metrics.items()])
    try:
        C.binding(d,config,remaining_ms)
        description=d.describe_table(TableName=table)['Table']
        R.need(description.get('TableId')==table_id and description.get('TableStatus')=='ACTIVE'
               and description.get('TableArn')==f"arn:aws:dynamodb:{config['region']}:{config['account']}:table/{table}")
        marker=C.validate_marker(get(pipeline,{'PK':'INVENTORY#'+config['environment'],'SK':'CAMPAIGN_PERIOD_WORK'}),config,start)
        key={'PK':'AGGREGATE_SWEEP#'+config['environment'],'SK':'STATE'}
        for _ in range(2):
            current=get(pipeline,key);clock=R.integer(now(),start)
            if current is not None:
                R.need(set(current)==CURSOR_FIELDS and current['recordType']=='CAMPAIGN_AGGREGATE_SWEEP'
                  and current['environment']==config['environment'] and current['generation']==config['generation']
                  and current['manifestSha256']==config['manifest'] and current['inventoryRevision']==config['revision']
                  and current['intelligenceTableId']==table_id)
                R.integer(current['schemaVersion'],1,1);R.integer(current['revision'],1);R.need(type(current['passIncomplete']) is bool)
                R.integer(current['shard'],0,15);R.integer(current['highWaterEpoch'],1,clock)
                R.integer(current['expiresAt'],current['highWaterEpoch']+1,current['highWaterEpoch']+86400)
                R.integer(current['lastFullPassAtEpoch'],0,clock)
                if current['cursor'] is not None:index_key(current['cursor'],config['environment'],current['shard'],current['highWaterEpoch'])
            active=current is not None and current['expiresAt']>clock
            expired=current is not None and not active
            shard=int(current['shard']) if active else (int(current['shard'])+1)%16 if expired else 0
            incomplete=current['passIncomplete'] if active else bool(expired and shard!=0)
            if expired:metrics['AggregateWorkUnverified']+=1
            high=int(current['highWaterEpoch']) if active else clock
            args={'TableName':table,'IndexName':'ExpirationIndex','KeyConditionExpression':'expiryPartition = :p AND expiresAt <= :end',
                  'ExpressionAttributeValues':C.wire({':p':f"EXPIRY#{config['environment']}#{shard:02d}",':end':high}),'Limit':16}
            if active and current['cursor'] is not None:args['ExclusiveStartKey']=C.wire(current['cursor'])
            page=d.query(**args);items=page.get('Items',[]);R.need(type(items) is list and len(items)<=16)
            lek=C.plain(page['LastEvaluatedKey']) if page.get('LastEvaluatedKey') else None
            if lek is not None:
                index_key(lek,config['environment'],shard,high)
                R.need(not active or lek!=current['cursor'])
            end=lek is None and shard==15
            changed=key|{'recordType':'CAMPAIGN_AGGREGATE_SWEEP','schemaVersion':1,'environment':config['environment'],
              'generation':config['generation'],'manifestSha256':config['manifest'],'inventoryRevision':config['revision'],
              'intelligenceTableId':table_id,'revision':int(current['revision'])+1 if current else 1,
              'shard':0 if end else shard+(lek is None),'highWaterEpoch':clock if end else high,'cursor':lek,
              'expiresAt':(clock if end else high)+86400,'passIncomplete':False if end else incomplete,
              'lastFullPassAtEpoch':clock if end and not incomplete else (current['lastFullPassAtEpoch'] if current else 0)}
            # Completed pass resets its exact LEK; no identifier survives the pass.
            guard=C.exact(current) if current else {'ConditionExpression':'attribute_not_exists(PK) AND attribute_not_exists(SK)'}
            d.transact_write_items(TransactItems=[C.condition(pipeline,marker),{'Put':{'TableName':pipeline,'Item':C.wire(changed),**guard}}])
            for raw in items:
                try:
                    projected=index_key(C.plain(raw),config['environment'],shard,high)
                    row=get(table,{k:projected[k] for k in ('PK','SK')})
                    if row is None:continue
                    validate(row,config['environment'])
                    if row['expiresAt']>R.integer(now(),clock):continue
                    metrics['AggregateOverdueObserved']+=1
                    d.transact_write_items(TransactItems=[C.condition(pipeline,marker),{'Delete':{
                        'TableName':table,'Key':C.wire({k:row[k] for k in ('PK','SK')}),**C.exact(row)}}])
                    metrics['AggregateWorkDeleted']+=1
                except Exception:metrics['AggregateWorkUnverified']+=1
            metrics['AggregateFullPassAgeSeconds']=clock-int(changed['lastFullPassAtEpoch']) if changed['lastFullPassAtEpoch'] else clock
        metrics['AggregateHeartbeat']=1
    except Exception:
        metrics['AggregateFailures']=1;emit();raise R.WorkUnavailable() from None
    emit();return {'schemaVersion':1,'aggregateErasureComplete':False,'metrics':metrics}
