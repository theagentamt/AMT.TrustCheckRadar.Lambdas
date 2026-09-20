"""Scheduled ledger-only cleanup. No URLs, secrets or provider permissions."""
import json
import os
import time


def lambda_handler(event,context):
    if os.environ.get('STAGE')!='dev' or os.environ.get('LEASE_SWEEP_ENABLED')!='true':
        return {'enabled':False,'recovered':0}
    if event!={'schemaVersion':1}:return {'enabled':True,'rejected':True}
    from shared_check_authority.recovery import Recovery
    import boto3
    from botocore.config import Config
    resource=boto3.resource('dynamodb',region_name='us-east-1',config=Config(connect_timeout=0.2,read_timeout=0.3,retries={'total_max_attempts':1}))
    table=os.environ['AUTHORITY_TABLE_NAME']
    key={'PK':'V1#CONTROL','SK':'LEASE_SWEEP_CURSOR'}
    current=resource.Table(table).get_item(Key=key,ConsistentRead=True).get('Item',{})
    revision=current.get('revision',0)
    # Small batches bound runtime; cursor persists even after poison leases.
    result=Recovery(resource,table,now=lambda:int(time.time())).sweep(cursor=current.get('cursor'),page_size=5,max_pages=2)
    operation={'TableName':table,'Key':key,'UpdateExpression':'SET #cursor = :cursor, revision = :next',
               'ConditionExpression':'attribute_not_exists(revision)' if revision==0 else 'revision = :old',
               'ExpressionAttributeNames':{'#cursor':'cursor'},'ExpressionAttributeValues':{':cursor':result['cursor'],':next':revision+1}}
    if revision:operation['ExpressionAttributeValues'][':old']=revision
    resource.meta.client.transact_write_items(TransactItems=[{'Update':operation}])
    counts={k:v for k,v in result.items() if k!='cursor'}
    print(json.dumps({'event':'url_lease_recovery',**counts}))
    return counts
