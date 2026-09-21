import json
import sys
from pathlib import Path
from copy import deepcopy
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from account_export_api.cursor import Cursor,ExportError
from account_export_api.service import Export,parse,MAX_TOTAL_BYTES,MAX_PAGES,SCOPE
from account_export_api import projection

class Reader:
    def __init__(self):
        self.context={'account':'synthetic-account','device':'synthetic-device','bindingVersion':1}
        self.items=[{'status':'ACTIVE'}]
        self.entries=[('profile',),('consent',)]
        self.reads=0
        self.after=None
    def auth(self,event):
        if self.after and self.reads:self.after()
        return dict(self.context)
    def inventory(self,ctx):return {'version':1}
    def assert_inventory(self,ctx,inventory):assert inventory=={'version':1}
    def plan(self,ctx,inventory):return self.entries
    def read(self,ctx,entry,position,cutoff):
        self.reads+=1
        return entry[0],deepcopy(self.items),None

@pytest.fixture
def world():
    reader=Reader();clock=[1800000000]
    cursor=Cursor('dev','k1',{'k1':b'x'*32})
    return Export(reader,cursor,lambda:clock[0]),reader,clock

def start(service):return service.page({}, {'action':'START_EXPORT'})
def next_page(service,cursor):return service.page({}, {'action':'CONTINUE_EXPORT','cursor':cursor})

def test_complete_traversal_stable_identity_expiry_and_wire_budget(world):
    service,reader,clock=world
    first=start(service);clock[0]+=1
    token=service.cursor.open(first['nextCursor'])
    assert token['totalBytes']==len(json.dumps(first,separators=(',',':'),ensure_ascii=False).encode())
    final=next_page(service,first['nextCursor'])
    assert final['status']=='COMPLETE' and final['nextCursor'] is None
    assert final['operationId']==first['operationId']
    assert final['expiresAtEpoch']==first['startedAtEpoch']+900
    assert final['pageNumber']==1
    assert first['scope']==SCOPE


def test_same_cursor_retry_observes_new_values_without_advancing_page_identity(world):
    service,reader,_=world
    first=start(service);a=next_page(service,first['nextCursor'])
    reader.items=[{'status':'CHANGED'}]
    b=next_page(service,first['nextCursor'])
    assert a['pageNumber']==b['pageNumber']==1 and a['operationId']==b['operationId']
    assert a['items']!=b['items']


def test_start_retry_is_explicit_new_operation(world):
    service,_,_=world
    assert start(service)['operationId']!=start(service)['operationId']

@pytest.mark.parametrize('change',[{'account':'other'},{'device':'other'},{'bindingVersion':2}])
def test_cross_account_device_or_version_cursor_fails(world,change):
    service,reader,_=world;first=start(service);reader.context.update(change)
    with pytest.raises(ExportError,match='INVALID_CURSOR'):next_page(service,first['nextCursor'])


def test_exact_expiry_is_not_extended_by_retries(world):
    service,_,clock=world;first=start(service);clock[0]+=900
    with pytest.raises(ExportError,match='EXPORT_EXPIRED'):next_page(service,first['nextCursor'])


def test_deletion_or_account_change_during_read_returns_no_page(world):
    service,reader,_=world
    def fence():raise ExportError('ACCOUNT_UNAVAILABLE',403)
    reader.after=fence
    with pytest.raises(ExportError,match='ACCOUNT_UNAVAILABLE'):start(service)

@pytest.mark.parametrize('field,value',[('totalBytes',MAX_TOTAL_BYTES-1),('pageNumber',MAX_PAGES)])
def test_limit_never_truncates_into_complete(world,field,value):
    service,_,_=world;first=start(service);token=service.cursor.open(first['nextCursor']);token[field]=value
    with pytest.raises(ExportError,match='EXPORT_LIMIT_EXCEEDED'):next_page(service,service.cursor.seal(token))


def test_oversized_public_page_fails_before_complete(world):
    service,reader,_=world;reader.entries=[('profile',)];reader.items=[{'email':'x'*65536}]
    with pytest.raises(ExportError,match='EXPORT_LIMIT_EXCEEDED'):start(service)


def test_cursor_is_encrypted_bound_to_environment_and_detects_tampering():
    cursor=Cursor('dev','k1',{'k1':b'x'*32});token=cursor.seal({'account':'private-account','position':'TOKEN#private'})
    assert 'private' not in token
    assert cursor.open(token)['account']=='private-account'
    for other in [Cursor('prod','k1',{'k1':b'x'*32}),Cursor('dev','k1',{'k1':b'y'*32})]:
        with pytest.raises(ExportError,match='INVALID_CURSOR'):other.open(token)
    with pytest.raises(ExportError,match='INVALID_CURSOR'):cursor.open(token[:-3]+'bad')
    rotated=Cursor('dev','k2',{'k1':b'x'*32,'k2':b'y'*32})
    assert rotated.open(token)['position']=='TOKEN#private'


def event(body):return {'version':'2.0','routeKey':'POST /v1/users/account-export','body':body}
@pytest.mark.parametrize('body',['{}','{"schemaVersion":true,"action":"START_EXPORT"}', '{"schemaVersion":1,"schemaVersion":1,"action":"START_EXPORT"}', '{"schemaVersion":1,"action":"START_EXPORT","accountId":"victim"}', 'x'*12289,'NaN'])
def test_strict_requests(body):
    with pytest.raises(ExportError):parse(event(body))


def test_valid_start_and_unknown_query_rejected():
    value=event('{"schemaVersion":1,"action":"START_EXPORT"}')
    assert parse(value)['action']=='START_EXPORT'
    value['rawQueryString']='accountId=victim'
    with pytest.raises(ExportError):parse(value)


def test_public_projection_excludes_internal_fields_and_rejects_nested_poison():
    row={'email':'synthetic@example.invalid','status':'ACTIVE','sub':'secret-sub','payloadHash':'secret-hash'}
    assert projection.project(row,'profile',1800000000)=={'email':'synthetic@example.invalid','status':'ACTIVE'}
    with pytest.raises(ExportError):projection.project({'email':{'secret':'payload'}},'profile',1800000000)


def test_unknown_summary_fields_or_invalid_feedback_never_escape():
    row={'recordType':'V1_CHECK_RECEIPT','state':'SETTLED','retentionDeadlineEpoch':1800000500,
         'assessmentEpoch':1800000000,'processingOutcome':'complete','chargedChecks':1,
         'projectionScope':'full_url','clientCheckId':'synthetic','resultSummary':{'rawUrl':'private'}}
    with pytest.raises(Exception):projection.project(row,'receipts',1800000001)
    row['resultSummary']=None;row['processingOutcome']='failed';row['chargedChecks']=0;row['feedback']={'category':'looks_like_scam','private':'content'}
    with pytest.raises(ExportError):projection.project(row,'receipts',1800000001)


def test_logically_expired_rows_are_not_exported():
    assert projection.project({'expiresAt':1800000000,'email':'expired'},'profile',1800000000) is None
