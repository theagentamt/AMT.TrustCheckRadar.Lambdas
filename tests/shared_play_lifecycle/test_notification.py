import base64,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from shared_play_lifecycle.notification import parse_delivery,NotificationError

NOW=1800000000
CONFIG=dict(audience='https://api-dev.andmorethings.net/v1/notifications/google-play',service_account_email='push@example.iam.gserviceaccount.com',service_account_subject='123456789012345678901',subscription_resource='projects/test-project/subscriptions/play-rtdn',now_epoch=NOW)
CLAIMS=dict(iss='https://accounts.google.com',aud=CONFIG['audience'],email=CONFIG['service_account_email'],email_verified=True,sub=CONFIG['service_account_subject'],iat=NOW-10,exp=NOW+3590)
TOKEN='synthetic-purchase-token'
def body(part=None):
 notice=dict(version='1.0',packageName='com.andmorethings.trustcheckradar',eventTimeMillis=str(NOW*1000),subscriptionNotification=part or dict(version='1.0',notificationType=2,purchaseToken=TOKEN))
 return json.dumps(dict(subscription=CONFIG['subscription_resource'],message=dict(messageId='123456789',data=base64.b64encode(json.dumps(notice).encode()).decode())))
def read(value=None,claims=None):return parse_delivery(value or body(),'Bearer a.b.c',verify_google_jwt=lambda token,aud:dict(CLAIMS if claims is None else claims),**CONFIG)
def mutate(fn):
 envelope=json.loads(body());notice=json.loads(base64.b64decode(envelope['message']['data']));fn(notice);envelope['message']['data']=base64.b64encode(json.dumps(notice).encode()).decode();return json.dumps(envelope)

def test_subscription_is_only_refresh_hint_and_repr_hides_token():
 result=read();assert result.kind=='subscription' and result.notification_type==2 and result.purchase_token==TOKEN
 assert TOKEN not in repr(result) and not hasattr(result,'account_id') and not hasattr(result,'grant')

@pytest.mark.parametrize('field,value',[('iss','evil'),('aud','https://wrong.example'),('email','other@example.iam.gserviceaccount.com'),('email_verified',False),('email_verified','true'),('sub','987654321'),('exp',NOW),('exp',True),('iat',NOW+60)])
def test_wrong_signed_identity_or_time_rejected(field,value):
 with pytest.raises(NotificationError,match='NOTIFICATION_AUTHENTICATION_REQUIRED'):read(claims=CLAIMS|{field:value})

def test_signature_failure_no_parse_no_echo():
 def fail(*a):raise ValueError(TOKEN)
 with pytest.raises(NotificationError) as e:parse_delivery(body(),'Bearer a.b.c',verify_google_jwt=fail,**CONFIG)
 assert str(e.value)=='NOTIFICATION_AUTHENTICATION_REQUIRED'

@pytest.mark.parametrize('fn',[lambda n:n.update(packageName='other.app'),lambda n:n.update(eventTimeMillis=str((NOW+301)*1000)),lambda n:n.update(testNotification={'version':'1.0'}),lambda n:n['subscriptionNotification'].update(notificationType=999),lambda n:n['subscriptionNotification'].update(notificationType=True),lambda n:n['subscriptionNotification'].update(purchaseToken='')])
def test_wrong_scope_unknown_or_malformed_notification_rejected(fn):
 with pytest.raises(NotificationError):read(mutate(fn))

def test_test_notification_transport_only():
 def fn(n):n.pop('subscriptionNotification');n['testNotification']={'version':'1.0'}
 result=read(mutate(fn));assert result.kind=='test' and result.purchase_token is None

def test_voided_subscription_still_only_refresh_hint():
 def fn(n):n.pop('subscriptionNotification');n['voidedPurchaseNotification']=dict(purchaseToken=TOKEN,orderId='GPA.synthetic',productType=1,refundType=1)
 result=read(mutate(fn));assert result.kind=='voided' and result.purchase_token==TOKEN

def test_old_notification_is_not_discarded_as_stale_authority():
 result=read(mutate(lambda n:n.update(eventTimeMillis='1000')));assert result.event_time_millis==1000

@pytest.mark.parametrize('change',[lambda e:e.update(subscription='projects/other-project/subscriptions/play-rtdn'),lambda e:e['message'].update(data='not-base64'),lambda e:e['message'].update(data=base64.b64encode(b'{"version":"1.0","version":"2.0"}').decode())])
def test_envelope_scope_encoding_and_duplicate_members_rejected(change):
 e=json.loads(body());change(e)
 with pytest.raises(NotificationError):read(json.dumps(e))
