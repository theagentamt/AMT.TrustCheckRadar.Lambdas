"""Disabled authenticated feedback adapter. Logs contain fixed metadata only."""
import json,os
from .validation import POLICY,APPROVAL_SHA,response

def http(status,body):return {'statusCode':status,'headers':{'Content-Type':'application/json','Cache-Control':'no-store'},'body':json.dumps(body,separators=(',',':'))}

def lambda_handler(event,context):
    if (os.environ.get('STAGE')!='dev' or os.environ.get('RESULT_FEEDBACK_ENABLED')!='true'
        or os.environ.get('RESULT_FEEDBACK_POLICY_VERSION')!=POLICY or os.environ.get('RESULT_FEEDBACK_POLICY_APPROVAL_SHA256')!=APPROVAL_SHA):
        return http(503,response('unknown',reason='SERVICE_NOT_ENABLED'))
    try:
        from shared_check_authority.engineering import require_engineering_subject
        require_engineering_subject(event)
        from .runtime import load_authority
        from .service import Feedback
        status,body=Feedback(load_authority()).handle(event)
    except Exception:return http(503,response('unknown',reason='SERVICE_UNAVAILABLE',retryable=True))
    print(json.dumps({'event':'result_feedback','httpStatus':status,'status':body['status'],'reason':body['reasonCode'] or 'NONE'}))
    return http(status,body)
