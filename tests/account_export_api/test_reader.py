import sys
from pathlib import Path
from types import SimpleNamespace
from decimal import Decimal
from unittest import mock
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from account_export_api.reader import Reader
from account_export_api.cursor import ExportError
from account_export_api import runtime,app

NOW=1800000000
CONTEXT={'account':'account-a','device':'device-a','bindingVersion':1}

class Table:
    def __init__(self, rows=None, page=None):
        self.rows=rows or {};self.page=page or {'Items':[]};self.calls=[]
    def get_item(self,**kw):
        self.calls.append(('get',kw));return {'Item':self.rows.get((kw['Key']['PK'],kw['Key']['SK']))}
    def query(self,**kw):self.calls.append(('query',kw));return self.page

class Authority:
    def __init__(self,tables):
        self.ddb=SimpleNamespace(Table=lambda name:tables[name]);self.s=SimpleNamespace(authority_table='authority',hmac_keys={'k1':b'x'*32})
    def now(self):return NOW
    def _account(self,event):return CONTEXT['account']
    def _device(self,event,account):return CONTEXT['device'],CONTEXT['bindingVersion']
    def _partition(self,account,kid):return 'V1#'+kid+'#owned'


def reader(tables=None,**kwargs):
    tables=tables or {};a=Authority(tables)
    return Reader(a,{name:name for name in tables},kwargs.get('cognito'), 'pool',
                  kwargs.get('purchase'),kwargs.get('kms'))


def test_recent_authentication_is_checked_on_every_page():
    r=reader();event={'requestContext':{'authorizer':{'jwt':{'claims':{'auth_time':str(NOW-300),'iat':str(NOW-1)}}}}}
    assert r.auth(event)==CONTEXT
    event['requestContext']['authorizer']['jwt']['claims']['auth_time']=str(NOW-301)
    with pytest.raises(ExportError,match='REAUTHENTICATION_REQUIRED'):r.auth(event)


def test_inventory_never_treats_missing_campaign_key_as_empty(monkeypatch):
    monkeypatch.setattr('account_export_api.reader.verified_inventory',lambda *args:{'revision':1})
    r=reader({'authority':Table(),'history_control':Table(),'pipeline':Table()},purchase=SimpleNamespace(inventory=lambda:{'revision':1}))
    with pytest.raises(ExportError,match='SOURCE_UNAVAILABLE'):r.inventory(CONTEXT)


def test_cross_account_query_row_is_never_projected():
    table=Table(page={'Items':[{'PK':'USER#victim','SK':'DEVICE#private','platform':'android'}]})
    r=reader({'devices':table})
    with pytest.raises(ExportError,match='SOURCE_UNAVAILABLE'):
        r.read(CONTEXT,('devices','devices','USER#account-a','DEVICE#',False),None,NOW)


def test_response_byte_pagination_does_not_skip_unemitted_record():
    rows=[{'PK':'USER#account-a','SK':f'DEVICE#{n:02}','platform':'a'*4096,'osVersion':'b'*4096} for n in range(10)]
    table=Table(page={'Items':rows});r=reader({'devices':table})
    family,items,next_key=r.read(CONTEXT,('devices','devices','USER#account-a','DEVICE#',False),None,NOW)
    assert family=='devices' and len(items)==5 and next_key=='DEVICE#04'
    assert table.calls[0][1]['ConsistentRead'] is True


def test_purchase_projection_cannot_grow_arbitrary_fields():
    purchase=SimpleNamespace(owned_page=lambda *a,**kw:{'records':[{'platform':'google_play','productId':'p','verifiedAtEpoch':NOW,'token':'secret'}],'cursor':None})
    r=reader(purchase=purchase)
    with pytest.raises(ExportError,match='SOURCE_UNAVAILABLE'):
        r.read(CONTEXT,('purchases','entitlements','USER#account-a','PURCHASE_TOKEN#',False),None,NOW)


def test_identity_mapping_is_verified_not_guessed():
    cognito=SimpleNamespace(admin_get_user=lambda **kw:{'Username':'account-a','UserAttributes':[{'Name':'sub','Value':'victim'}]})
    r=reader(cognito=cognito)
    with pytest.raises(ExportError,match='SOURCE_UNAVAILABLE'):
        r.read(CONTEXT,('identity',None,None,None,True),None,NOW)


def test_own_identity_attributes_exclude_unknown_claims_and_security_metadata():
    cognito=SimpleNamespace(admin_get_user=lambda **kw:{'Username':'account-a','UserAttributes':[{'Name':'sub','Value':'account-a'},{'Name':'email','Value':'synthetic@example.invalid'},{'Name':'custom:private_token','Value':'secret'}]})
    family,items,cursor=reader(cognito=cognito).read(CONTEXT,('identity',None,None,None,True),None,NOW)
    assert items==[{'email':'synthetic@example.invalid'}] and cursor is None


def test_stale_or_forged_outbox_locator_never_targets_someone_else():
    import hashlib
    digest=hashlib.sha256(b'account-a').hexdigest()
    locator={'recordType':'CAMPAIGN_OUTBOX_LOCATOR','accountIdHash':digest,'environment':'dev','statisticsEventId':'event-a','SK':'OUTBOX#event-a','eventPK':'EVENT#event-a','eventSK':'OBSERVATION_READY','eventExpiresAt':NOW+60}
    table=Table(rows={('EVENT#event-a','OBSERVATION_READY'):{'PK':'EVENT#event-a','SK':'OBSERVATION_READY','accountId':'victim','statisticsEventId':'event-a','environment':'dev'}})
    with pytest.raises(ExportError,match='SOURCE_UNAVAILABLE'):reader({'outbox':table})._observation(CONTEXT,locator)


def test_default_disabled_bootstrap_performs_no_aws(monkeypatch):
    monkeypatch.delenv('ACCOUNT_EXPORT_ENABLED',raising=False)
    with pytest.raises(ExportError,match='SERVICE_NOT_ENABLED'):runtime.load()


def test_handled_system_failure_is_metric_without_private_exception(monkeypatch,caplog):
    monkeypatch.setattr(app,'load',lambda:(_ for _ in ()).throw(RuntimeError('private-account-token')))
    with caplog.at_level('INFO'):
        response=app.lambda_handler({'body':'private-input'},None)
    assert response['statusCode']==503 and 'private' not in response['body']
    assert 'private' not in caplog.text and 'AccountExportUnavailable' in caplog.text
