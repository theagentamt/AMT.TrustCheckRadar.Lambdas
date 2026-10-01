"""Account-fenced token/mapping erasure, separately enabled from provider access."""
import json
import os
import re
import time


def _worker(context):
    from shared_check_authority.inventory import load_keyring,verified_inventory
    from shared_check_authority.engineering import allowed_subjects
    from shared_play_lifecycle.deletion import TokenDeletion
    from shared_play_lifecycle.runtime import resource,token_table
    from v1_authority_deletion.service import DeletionWorker
    subjects=allowed_subjects();_,keys=load_keyring();ddb=resource()
    authority=os.environ['AUTHORITY_TABLE_NAME'];ledger=os.environ['DELETION_LEDGER_TABLE_NAME'];stream=os.environ['DELETION_LEDGER_STREAM_ARN']
    if not re.fullmatch(r'arn:aws:dynamodb:us-east-1:107827791950:table/[A-Za-z0-9_.-]+/stream/[0-9T:.-]+',stream) or stream.split(':table/',1)[1].split('/stream/',1)[0]!=ledger:raise ValueError()
    verified_inventory(ddb,authority,keys)
    bridge=TokenDeletion(ddb,authority_table=authority,ledger_table=ledger,environment='dev',keyring=keys,
        receipt_retention_seconds=int(os.environ['DELETION_RECEIPT_RETENTION_SECONDS']),now=lambda:int(time.time()),token_table=token_table())
    if context is None or not callable(getattr(context,'get_remaining_time_in_millis',None)):raise ValueError()
    return DeletionWorker(bridge,stream_arn=stream,remaining_ms=context.get_remaining_time_in_millis,allowed_subjects=subjects,
        cursor_key={'PK':'PLAY#CONTROL','SK':'TOKEN_DELETION_CURSOR'})


def lambda_handler(event,context):
    if os.environ.get('STAGE')!='dev' or os.environ.get('PLAY_TOKEN_CLEANUP_ENABLED')!='true':return {'enabled':False}
    counts=dict(heartbeat=1,examined=0,completed=0,pending=0,failed=0,deleted=0,overdue=0,skipped=0)
    try:
        if event!={'schemaVersion':1,'operation':'reconcile-play-token-deletion'} and not (isinstance(event,dict) and set(event)=={'Records'}):raise ValueError()
        worker=_worker(context)
        if 'Records' in event:response,result=worker.stream(event)
        else:result=worker.reconcile();response=result
        counts.update({key:result[key] for key in counts if key in result})
        if 'fullPassAgeSeconds' in result:counts['fullPassAgeSeconds']=result['fullPassAgeSeconds']
        return response
    except Exception:
        counts['failed']+=1;raise RuntimeError('PLAY_TOKEN_DELETION_UNAVAILABLE') from None
    finally:print(json.dumps({'event':'play_token_deletion',**counts},separators=(',',':')))
