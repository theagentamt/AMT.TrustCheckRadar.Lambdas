"""Authenticate and decode bounded RTDN delivery without retaining store proof.

The injected verifier must cryptographically verify Google's signature and the
configured audience; decoded JWT payloads must never substitute for it. No runtime
route, provider call, persistence or entitlement mutation is implemented here.
"""
import base64
import binascii
from dataclasses import dataclass, field
import json
import re

from shared_play_verification.proof import PACKAGE, _token

SUBSCRIPTION_TYPES = frozenset({1,2,3,4,5,6,7,8,9,10,11,12,13,17,18,19,20,22})

class NotificationError(RuntimeError):
    """Fixed code only; caller must not log credentials or message bodies."""


def require(ok, code='NOTIFICATION_INVALID'):
    if not ok:
        raise NotificationError(code)


def unique(pairs):
    value = {}
    for key, member in pairs:
        require(key not in value)
        value[key] = member
    return value


@dataclass(frozen=True)
class Notification:
    message_id: str
    event_time_millis: int
    kind: str
    notification_type: int | None
    purchase_token: str | None = field(repr=False)
    order_id: str | None = field(default=None, repr=False)


def parse_delivery(body, authorization, *, verify_google_jwt, audience,
                   service_account_email, service_account_subject,
                   subscription_resource, now_epoch):
    """Return only a refresh hint after authenticated, exact-project delivery.

    message_id is a deduplication hint, not authority. event_time_millis must not
    order grants or suppress a fresh authoritative refresh. A test notification
    confirms transport only and never requires a purchase/Orders request.
    """
    require(type(now_epoch) is int and now_epoch > 0, 'NOTIFICATION_CONFIGURATION_INVALID')
    require(isinstance(audience,str) and audience.startswith('https://') and len(audience)<=2048
            and isinstance(service_account_email,str) and service_account_email.endswith('.gserviceaccount.com')
            and isinstance(service_account_subject,str) and re.fullmatch(r'[0-9]{6,64}',service_account_subject)
            and isinstance(subscription_resource,str) and re.fullmatch(r'projects/[A-Za-z0-9-]{3,64}/subscriptions/[A-Za-z][A-Za-z0-9._~+%-]{2,254}',subscription_resource),
            'NOTIFICATION_CONFIGURATION_INVALID')
    require(isinstance(body,str) and len(body.encode('utf-8'))<=65536)
    require(isinstance(authorization,str) and re.fullmatch(r'Bearer [A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',authorization) and len(authorization)<=8192,
            'NOTIFICATION_AUTHENTICATION_REQUIRED')
    try:
        claims = verify_google_jwt(authorization[7:], audience)
    except Exception:
        raise NotificationError('NOTIFICATION_AUTHENTICATION_REQUIRED') from None
    require(type(claims) is dict and claims.get('iss') in ('accounts.google.com','https://accounts.google.com')
            and claims.get('aud')==audience and claims.get('email')==service_account_email
            and claims.get('email_verified') is True and claims.get('sub')==service_account_subject
            and type(claims.get('exp')) is int and type(claims.get('iat')) is int
            and 0<claims['iat']<=now_epoch+30 and now_epoch<claims['exp']<=claims['iat']+3600,
            'NOTIFICATION_AUTHENTICATION_REQUIRED')
    try:
        envelope=json.loads(body,object_pairs_hook=unique)
        require(type(envelope) is dict and set(envelope)=={'message','subscription'}
                and envelope['subscription']==subscription_resource)
        message=envelope['message']
        require(type(message) is dict and {'data','messageId'}<=set(message)
                and set(message)<={'data','messageId','publishTime','attributes','orderingKey'})
        require(isinstance(message['messageId'],str) and re.fullmatch(r'[0-9]{1,128}',message['messageId']))
        if 'attributes' in message:
            require(type(message['attributes']) is dict and len(message['attributes'])<=32
                    and all(isinstance(k,str) and isinstance(v,str) and len(k)<=256 and len(v)<=2048 for k,v in message['attributes'].items()))
        require(isinstance(message['data'],str) and len(message['data'])<=32768)
        decoded=base64.b64decode(message['data'],validate=True)
        require(base64.b64encode(decoded).decode()==message['data'])
        notice=json.loads(decoded.decode('utf-8'),object_pairs_hook=unique)
        require(type(notice) is dict and notice.get('version')=='1.0' and notice.get('packageName')==PACKAGE)
        choices=set(notice)-{'version','packageName','eventTimeMillis'}
        require(len(choices)==1 and choices<= {'subscriptionNotification','voidedPurchaseNotification','testNotification'},'NOTIFICATION_UNSUPPORTED')
        stamp=notice.get('eventTimeMillis')
        require(isinstance(stamp,str) and re.fullmatch(r'[1-9][0-9]{0,15}',stamp) and int(stamp)<=(now_epoch+300)*1000)
        kind=next(iter(choices));part=notice[kind];require(type(part) is dict)
        if kind=='testNotification':
            require(part=={'version':'1.0'})
            return Notification(message['messageId'],int(stamp),'test',None,None)
        token=part.get('purchaseToken');_token(token)
        if kind=='subscriptionNotification':
            require(set(part)=={'version','notificationType','purchaseToken'} and part['version']=='1.0')
            require(type(part['notificationType']) is int and part['notificationType'] in SUBSCRIPTION_TYPES,'NOTIFICATION_UNSUPPORTED')
            return Notification(message['messageId'],int(stamp),'subscription',part['notificationType'],token)
        require(set(part)=={'purchaseToken','orderId','productType','refundType'}
                and type(part['productType']) is int and part['productType']==1
                and type(part['refundType']) is int and part['refundType']==1,'NOTIFICATION_UNSUPPORTED')
        order=part['orderId'];require(isinstance(order,str) and 1<=len(order)<=256)
        return Notification(message['messageId'],int(stamp),'voided',None,token,order)
    except NotificationError:
        raise
    except (ValueError,TypeError,KeyError,UnicodeError,binascii.Error,RuntimeError):
        raise NotificationError('NOTIFICATION_INVALID') from None
