"""Scheduled expiry runs independently of paid/provider activation."""
import json
import os
import time
from .service import Worker
from shared_play_lifecycle import runtime


def lambda_handler(event,context):
    if os.environ.get('STAGE')!='dev' or os.environ.get('PLAY_TOKEN_CLEANUP_ENABLED')!='true' or os.environ.get('PLAY_CHECKPOINT_POLICY_APPROVED')!='true':return {'enabled':False}
    try:
        if event!={'schemaVersion':1,'operation':'reconcile-play-lifecycle'} or context is None:raise ValueError()
        worker=Worker(runtime.resource(),runtime.token_table(),now=lambda:int(time.time()),remaining_ms=context.get_remaining_time_in_millis,
            reconciler_factory=runtime.load,lifecycle_enabled=os.environ.get('PLAY_LIFECYCLE_ENABLED')=='true')
        counts=worker.run()
    except Exception:
        counts=dict(heartbeat=1,examined=0,reconciled=0,ackPending=0,expiredDeleted=0,failed=1,unresolved=0,exhausted=0,oldestDueSeconds=0)
    print(json.dumps({'event':'play_lifecycle_worker',**counts},separators=(',',':')))
    return counts
