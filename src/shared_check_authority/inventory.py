"""Verified retained-key inventory; never bootstrap trust from the current secret."""
import base64
import hashlib
import json
import os
import re

INVENTORY_KEY = {'PK': 'V1#CONTROL', 'SK': 'HMAC_KEY_INVENTORY'}


def load_keyring():
    """Independent cleanup-compatible loader. No active-account/feature dependency."""
    from .core import AuthorityError
    try:
        import boto3
        from botocore.config import Config
        if os.environ.get('STAGE') != 'dev': raise ValueError()
        arn = os.environ['AUTHORITY_HMAC_SECRET_ARN']
        if not re.fullmatch(r'arn:aws:secretsmanager:us-east-1:107827791950:secret:trustcheckradar/dev/v1-authority-hmac-[A-Za-z0-9]{6}', arn): raise ValueError()
        client = boto3.client('secretsmanager', region_name='us-east-1', config=Config(connect_timeout=.2, read_timeout=.3, retries={'total_max_attempts':1}))
        try: raw = client.get_secret_value(SecretId=arn, VersionStage='AWSCURRENT')['SecretString']
        finally: client.close()
        if not isinstance(raw, str) or len(raw)>4096: raise ValueError()
        obj = json.loads(raw)
        if not isinstance(obj,dict) or set(obj)!={'activeKeyId','keys'} or not isinstance(obj['keys'],dict) or not 1<=len(obj['keys'])<=4: raise ValueError()
        keys={k:base64.b64decode(v,validate=True) for k,v in obj['keys'].items()}
        if obj['activeKeyId'] not in keys or any(not isinstance(k,str) or not re.fullmatch(r'[A-Za-z0-9]{1,8}',k) or len(v)<32 for k,v in keys.items()): raise ValueError()
        return obj['activeKeyId'],keys
    except Exception: raise AuthorityError('KEY_INVENTORY_UNAVAILABLE') from None


def verified_inventory(resource, table, keys):
    from .core import AuthorityError, integral
    row=resource.Table(table).get_item(Key=INVENTORY_KEY,ConsistentRead=True).get('Item')
    expected={k:hashlib.sha256(v).hexdigest() for k,v in keys.items()}
    if (not row or set(row)!={'PK','SK','recordType','schemaVersion','revision','coverage','issuedKeys'}
            or row.get('recordType')!='V1_HMAC_KEY_INVENTORY' or integral(row.get('schemaVersion'))!=1
            or integral(row.get('revision')) is None or row['revision']<1
            or row.get('coverage')!='VERIFIED_COMPLETE' or row.get('issuedKeys')!=expected):
        raise AuthorityError('KEY_INVENTORY_UNAVAILABLE')
    return row


def inventory_condition(table, row):
    return {'ConditionCheck':{'TableName':table,'Key':INVENTORY_KEY,
        'ConditionExpression':'revision = :revision AND coverage = :coverage AND issuedKeys = :keys AND recordType = :type AND schemaVersion = :schema',
        'ExpressionAttributeValues':{':revision':row['revision'],':coverage':'VERIFIED_COMPLETE',':keys':row['issuedKeys'],':type':'V1_HMAC_KEY_INVENTORY',':schema':1}}}
