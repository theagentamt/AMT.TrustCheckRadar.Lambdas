"""Bounded synthetic-store expiry only. No identity, authority or provider client."""
import os
import time


def sweep(resource, table_name, now):
    from boto3.dynamodb.conditions import Key
    table = resource.Table(table_name)
    deleted = 0
    cursor = None
    certain = True
    for _ in range(4):
        request = {'IndexName': 'GSI1',
                   'KeyConditionExpression': Key('GSI1PK').eq('URL_QUALIFICATION_EXPIRING') & Key('GSI1SK').lt(f'{now + 1:012d}#'),
                   'ProjectionExpression': 'PK, SK, expiresAt', 'Select': 'SPECIFIC_ATTRIBUTES', 'Limit': 50}
        if cursor:
            request['ExclusiveStartKey'] = cursor
        page = table.query(**request)
        for row in page.get('Items', []):
            try:
                table.delete_item(Key={k: row[k] for k in ('PK', 'SK')},
                                  ConditionExpression='expiresAt = :expiry AND expiresAt <= :now',
                                  ExpressionAttributeValues={':expiry': row['expiresAt'], ':now': now})
                deleted += 1
            except resource.meta.client.exceptions.ConditionalCheckFailedException:
                # Concurrent refresh/deletion is safe; cannot delete newer data.
                certain = False
                continue
        cursor = page.get('LastEvaluatedKey')
        if not cursor:
            if not certain:
                return {'deleted': deleted, 'complete': False}
            table.update_item(Key={'PK': 'CONTROL', 'SK': 'CLEANUP'},
                              UpdateExpression='SET lastSweepEpoch = :now',
                              ExpressionAttributeValues={':now': now})
            return {'deleted': deleted, 'complete': True}
    return {'deleted': deleted, 'complete': False}


def lambda_handler(event, context):
    if (os.environ.get('STAGE') != 'dev' or os.environ.get('URL_CLEANUP_ENABLED') != 'true'
            or os.environ.get('URL_RETENTION_APPROVED') != 'true'):
        return {'enabled': False}
    import boto3
    from botocore.config import Config
    resource = boto3.resource('dynamodb', region_name='us-east-1',
                             config=Config(connect_timeout=0.2, read_timeout=0.3, retries={'total_max_attempts': 1}))
    # An exception never advances the freshness heartbeat. No raw event/log data.
    result = sweep(resource, os.environ['URL_QUALIFICATION_TABLE_NAME'], int(time.time()))
    if not isinstance(result, dict) or result.get('complete') is not True:
        # Fixed error activates Lambda Errors alarms without serializing any
        # sweep response, key or event. Incomplete sweeps never advance heartbeat.
        raise RuntimeError('URL_QUALIFICATION_CLEANUP_INCOMPLETE')
    return result
