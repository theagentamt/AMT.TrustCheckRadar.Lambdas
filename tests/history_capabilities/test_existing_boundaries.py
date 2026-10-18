"""Executable current endpoints/Moto evidence, not a new bridge implementation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from unittest import mock

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests/history_mutation_api'),str(ROOT/'tests/shared_check_authority')]
from test_read_errors import api_world,world,OP,FP
from test_transaction_errors import ACCOUNT as LEGACY_ACCOUNT
from test_transactions import world as authority_world,ACCOUNT
from tests.governed_history.test_service import enable,add,service
from governed_history import app as gov_app
from history_mutation_api import app as legacy_app
from shared_check_authority.core import AuthorityError,TrustedWorkerContext
from governed_history.service import GovernedHistoryError
from shared_governed_history.projection import canonical, validate_projection

GOV_ID='gh1_1800000005_9d24fa30d29e7517af26120a421bee02'


def legacy_delete(target=GOV_ID,extra=None):
    body={'schemaVersion':1,'operationId':OP}|(extra or {})
    return legacy_app.lambda_handler({'routeKey':'DELETE /v1/users/history/{requestId}',
        'body':json.dumps(body),'pathParameters':{'requestId':target},
        'headers':{'x-device-binding-fingerprint':FP}},None)


@pytest.mark.parametrize('route',[
 'POST /v1/users/history/bootstrap','GET /v1/users/progress',
 'DELETE /v1/users/history/{requestId}','DELETE /v1/users/history',
 'POST /v1/users/progress/reset','DELETE /v1/users/history/account',
 'DELETE /v1/users/analysis-history/{resultId}','POST /v1/users/analysis-history/reset'])
def test_current_governed_handler_has_no_mutation_award_or_new_route(route,monkeypatch,capsys):
    import boto3
    attempted=[]
    def deny(*a,**kw):attempted.append(1);raise AssertionError('SDK operation forbidden')
    monkeypatch.setattr(boto3,'resource',deny);monkeypatch.setattr(boto3,'client',deny)
    event={'version':'2.0','routeKey':route,'requestContext':{'http':{'method':route.split()[0]}},
        'body':None,'isBase64Encoded':False,'accountId':'synthetic-foreign-owner'}
    result=gov_app.lambda_handler(event,None)
    assert result['statusCode']==404 and json.loads(result['body'])=={'error':{'code':'NOT_FOUND','retryable':False}}
    assert not attempted and capsys.readouterr().out==''


def test_legacy_gh1_noop_receipt_replay_is_not_governed_deletion(api_world,world):
    _,tables,client,_=api_world
    resource=world[2]
    authority=resource.create_table(TableName='synthetic-governed-authority',
        KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],
        AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}],BillingMode='PAY_PER_REQUEST')
    public=json.loads((ROOT/'contracts/governed-history/1.0.0-candidate.1/fixtures.json').read_text())['fixtures'][0]['response']['items'][0]
    projection={k:v for k,v in public.items() if k!='presentation'}
    validate_projection(projection)
    item={'PK':'synthetic-HMAC-partition','SK':'CHECK#synthetic','recordType':'V1_CHECK_RECEIPT','state':'SETTLED',
        'governedHistory':projection,'governedHistoryDigest':hashlib.sha256(canonical(projection)).hexdigest(),
        'chargedChecks':projection['accounting']['chargedChecks'],'retentionDeadlineEpoch':projection['expiresAtEpoch']}
    authority.put_item(Item=deepcopy(item))
    with mock.patch.object(client,'transact_write_items',wraps=client.transact_write_items) as writes:
        first=legacy_delete();assert first['statusCode']==200
        assert json.loads(first['body'])['targetRequestId']==GOV_ID
        retained=tables['history-control'].scan()['Items']
        second=legacy_delete();assert second==first
        assert tables['history-control'].scan()['Items']==retained and writes.call_count==1
        conflict=legacy_delete('different-valid-target');assert conflict['statusCode']==409 and writes.call_count==1
    assert authority.get_item(Key={'PK':item['PK'],'SK':item['SK']})['Item']==item
    assert len([x for x in retained if x['SK'].startswith('MUTATION#')])==1


def test_client_account_field_does_not_select_owner_or_dispatch_mutation(api_world):
    _,tables,client,_=api_world
    before=tables['history-control'].scan()['Items']
    with mock.patch.object(client,'transact_write_items',wraps=client.transact_write_items) as writes:
        result=legacy_delete(extra={'accountId':'synthetic-foreign-owner'})
    assert result['statusCode']==400 and writes.call_count==0
    assert tables['history-control'].scan()['Items']==before
    assert 'synthetic-foreign-owner' not in result['body']


def test_same_operation_id_is_not_cross_account_authority(api_world,monkeypatch):
    _,tables,client,_=api_world
    assert legacy_delete()['statusCode']==200
    before=tables['history-control'].scan()['Items']
    monkeypatch.setattr(legacy_app,'jwt_subject',lambda *a:'synthetic-missing-foreign-owner')
    with mock.patch.object(client,'transact_write_items',wraps=client.transact_write_items) as writes:
        result=legacy_delete()
    assert result['statusCode']==403 and writes.call_count==0
    assert tables['history-control'].scan()['Items']==before


def test_governed_current_owner_read_replay_preserves_receipt_and_deadline(authority_world):
    enable(authority_world)
    authority,event,_,row,_,clock=authority_world
    proof=add(authority,event,'s10-logical-check','A request outside qualified coverage.')
    retained=deepcopy(row('CHECK#'+proof));api=service(authority_world)
    result=api.list()['items'][0];first=api.detail(result['resultId'])
    clock[0]+=1
    again=api.detail(result['resultId'])
    assert again['result']==first['result'] and row('CHECK#'+proof)==retained
    # Settlement replay cannot create an award, extra charge or deadline.
    original=authority.settle(TrustedWorkerContext(ACCOUNT),proof,retained['executionToken'],retained['processingOutcome'],result_summary=retained['resultSummary'])
    assert row('CHECK#'+proof)==retained and original['chargedChecks']==retained['chargedChecks']
    assert 'qualifyingChecks' not in result and 'recognitionGeneration' not in result
    clock[0]=retained['retentionDeadlineEpoch']
    with pytest.raises(GovernedHistoryError):api.detail(result['resultId'])
    assert row('CHECK#'+proof)==retained


def test_governed_result_reference_does_not_bypass_identity_device_deletion(authority_world):
    enable(authority_world)
    authority,event,_,_,change,_=authority_world
    add(authority,event,'s10-owned','A request outside qualified coverage.')
    result_id=service(authority_world).list()['items'][0]['resultId']
    # API authentication is independently re-evaluated, not inferred from gh1.
    from governed_history.security import authorize
    foreign=deepcopy(event);foreign['requestContext']['authorizer']['jwt']['claims']['sub']='synthetic-foreign-owner'
    with pytest.raises(AuthorityError):authorize(authority,foreign)
    moved=deepcopy(event);moved['headers']['x-device-binding-fingerprint']='synthetic-other-device'
    with pytest.raises(AuthorityError):authorize(authority,moved)
    change('deletion','ACCOUNT_DELETION',schemaVersion=1,status='REQUESTED')
    with pytest.raises(AuthorityError):authorize(authority,event)
    assert result_id.startswith('gh1_') # reference remains valid syntax, not access.


def test_existing_clear_and_reset_keep_independent_generations_and_replay(api_world):
    _,tables,client,_=api_world
    control=tables['history-control'];pk='USER#'+LEGACY_ACCOUNT
    progress={'PK':pk,'SK':'PROGRESS#3','recognitionGeneration':3,'qualifyingChecks':5,
        'awardedBadgeIds':['checks_1','checks_5'],'stateVersion':1}
    control.put_item(Item=deepcopy(progress))
    def call(route,op):
        return legacy_app.lambda_handler({'routeKey':route,'body':json.dumps({'schemaVersion':1,'operationId':op}),
            'headers':{'x-device-binding-fingerprint':FP}},None)
    with mock.patch.object(client,'transact_write_items',wraps=client.transact_write_items) as writes:
        cleared=call('DELETE /v1/users/history',OP);assert cleared['statusCode']==200
        state=control.get_item(Key={'PK':pk,'SK':'STATE'})['Item']
        assert (state['historyGeneration'],state['recognitionGeneration'])==(3,3)
        assert control.get_item(Key={'PK':pk,'SK':'PROGRESS#3'})['Item']==progress
        reset_id='22222222-2222-4222-8222-222222222222'
        reset=call('POST /v1/users/progress/reset',reset_id);assert reset['statusCode']==200
        state=control.get_item(Key={'PK':pk,'SK':'STATE'})['Item']
        assert (state['historyGeneration'],state['recognitionGeneration'])==(3,4)
        new=control.get_item(Key={'PK':pk,'SK':'PROGRESS#4'})['Item']
        assert new['qualifyingChecks']==0 and new['awardedBadgeIds']==[]
        assert call('DELETE /v1/users/history',OP)==cleared
        assert call('POST /v1/users/progress/reset',reset_id)==reset and writes.call_count==2
        assert control.get_item(Key={'PK':pk,'SK':'STATE'})['Item']==state


def test_two_eligible_governed_accounts_cannot_share_a_result_reference(authority_world):
    enable(authority_world)
    authority,event,_,row,_,clock=authority_world
    proof=add(authority,event,'s10-isolated-owner','A request outside qualified coverage.')
    original=deepcopy(row('CHECK#'+proof));result_id=service(authority_world).list()['items'][0]['resultId']
    other='synthetic-second-eligible-owner';device='synthetic-second-device'
    authority.ddb.Table('users').put_item(Item={'PK':'USER#'+other,'SK':'PROFILE','sub':other,'status':'ACTIVE','ageVerified':True})
    authority.ddb.Table('devices').put_item(Item={'PK':'USER#'+other,'SK':'ACTIVE_BINDING','recordType':'ACTIVE_BINDING_POINTER','stateVersion':1,'bindingFingerprint':device})
    authority.ddb.Table('devices').put_item(Item={'PK':'USER#'+other,'SK':'DEVICE#'+device,'accountId':other,'status':'ACTIVE','bindingFingerprint':device})
    second=deepcopy(event);second['requestContext']['authorizer']['jwt']['claims']['sub']=other
    second['headers']['x-device-binding-fingerprint']=device
    from governed_history.security import authorize
    assert authorize(authority,second)[0]==other
    from governed_history.service import Service
    from tests.governed_history.test_service import settings
    other_reader=Service(authority,settings(),account=other,fingerprint=device,device_version=1,inventory_revision=1,now=lambda:clock[0])
    assert other_reader.list()['items']==[]
    with pytest.raises(GovernedHistoryError,match='NOT_FOUND'):other_reader.detail(result_id)
    assert service(authority_world).detail(result_id)['result']['resultId']==result_id
    assert row('CHECK#'+proof)==original
