"""Authenticated transient Pub/Sub; no raw request/token/error logging or queues."""
import hashlib
import json
import os
import time
from shared_play_lifecycle.notification import parse_delivery,NotificationError
from shared_play_lifecycle import runtime


def lambda_handler(event,_context):
    if os.environ.get('STAGE')!='dev' or os.environ.get('PLAY_LIFECYCLE_ENABLED')!='true':return {'statusCode':503,'body':''}
    counts=dict(heartbeat=1,accepted=0,testNotification=0,rejected=0,failed=0,unresolved=0);service=None
    try:
        if (type(event) is not dict or event.get('version')!='2.0' or event.get('requestContext',{}).get('http',{}).get('method')!='POST'
                or event.get('isBase64Encoded') is True or event.get('rawQueryString') or event.get('queryStringParameters')):raise NotificationError('NOTIFICATION_INVALID')
        headers=event.get('headers',{})
        if not isinstance(headers,dict):raise NotificationError('NOTIFICATION_INVALID')
        notification=parse_delivery(event.get('body'),headers.get('authorization'),verify_google_jwt=runtime.verify_google_jwt,
            audience=os.environ['PLAY_PUBSUB_AUDIENCE'],service_account_email=os.environ['PLAY_PUBSUB_SERVICE_ACCOUNT_EMAIL'],
            service_account_subject=os.environ['PLAY_PUBSUB_SERVICE_ACCOUNT_SUBJECT'],subscription_resource=os.environ['PLAY_PUBSUB_SUBSCRIPTION'],now_epoch=int(time.time()))
        if notification.kind=='test':counts['testNotification']=1;return {'statusCode':204,'body':''}
        service=runtime.load()
        result=service.refresh(notification.purchase_token,hashlib.sha256(('rtdn\0'+notification.message_id).encode()).hexdigest())
        if result['state']!='committed':counts['unresolved']=1;return {'statusCode':503,'body':''}
        counts['accepted']=1
        return {'statusCode':204,'body':''}
    except NotificationError:
        counts['rejected']=1;return {'statusCode':401,'body':''}
    except Exception:
        counts['failed']=1;return {'statusCode':503,'body':''}
    finally:
        runtime.close(service)
        print(json.dumps({'event':'play_lifecycle_ingress',**counts},separators=(',',':')))
