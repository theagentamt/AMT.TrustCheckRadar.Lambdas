"""Dev deletion worker; disabled before secret/network access by default."""
import json
import os
import re
import time


def lambda_handler(event,context):
    if os.environ.get('STAGE')!='dev' or os.environ.get('V1_AUTHORITY_DELETION_ENABLED')!='true':
        return {'enabled':False}
    try:
        if event!={'schemaVersion':1,'operation':'reconcile-v1-authority-deletion'} and not (isinstance(event,dict) and set(event)=={'Records'}):
            raise ValueError()
        worker=_worker(context)
        if 'Records' in event:
            response,counts=worker.stream(event)
        else:
            counts=worker.reconcile();response=counts
        print(json.dumps({'event':'v1_authority_deletion',**counts,'reason':'BATCH_FAILED' if counts['failed'] else 'OK'}))
        return response
    except Exception:
        print(json.dumps({'event':'v1_authority_deletion','failed':1,'reason':'DELETION_UNAVAILABLE'}))
        raise RuntimeError('DELETION_UNAVAILABLE') from None


def _worker(context):
    from shared_check_authority.inventory import load_keyring,verified_inventory
    from shared_check_authority.deletion import AuthorityDeletion
    from v1_authority_deletion.service import DeletionWorker
    import boto3
    from botocore.config import Config
    from shared_check_authority.engineering import allowed_subjects
    subjects=allowed_subjects()
    _,keys=load_keyring()
    config=Config(connect_timeout=.2,read_timeout=.3,retries={'total_max_attempts':1})
    resource=boto3.resource('dynamodb',region_name='us-east-1',config=config)
    authority=os.environ['AUTHORITY_TABLE_NAME'];ledger=os.environ['DELETION_LEDGER_TABLE_NAME']
    stream=os.environ['DELETION_LEDGER_STREAM_ARN']
    if not re.fullmatch(r'arn:aws:dynamodb:us-east-1:107827791950:table/[A-Za-z0-9_.-]+/stream/[0-9T:.-]+',stream):raise ValueError()
    if stream.split(':table/',1)[1].split('/stream/',1)[0]!=ledger:raise ValueError()
    verified_inventory(resource,authority,keys)
    bridge=AuthorityDeletion(resource,authority_table=authority,ledger_table=ledger,environment='dev',keyring=keys,
        receipt_retention_seconds=int(os.environ['DELETION_RECEIPT_RETENTION_SECONDS']),now=lambda:int(time.time()))
    if context is None or not callable(getattr(context,'get_remaining_time_in_millis',None)):raise ValueError()
    return DeletionWorker(bridge,stream_arn=stream,remaining_ms=context.get_remaining_time_in_millis,allowed_subjects=subjects)
