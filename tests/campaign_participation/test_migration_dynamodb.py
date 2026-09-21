"""Actual SDK transaction acceptance, isolated from ordinary test SDK stubs."""
import importlib.util
import os
from pathlib import Path
import sys
import uuid
import json
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Run isolated with AMT_AUTHORITY_INTEGRATION=1', allow_module_level=True)
import boto3
from moto import mock_aws
from boto3.dynamodb.types import TypeDeserializer
ROOT = Path(__file__).resolve().parents[2]
NOW=1789992000
NOTICE='research-consent-2026-09-21-v2'
EPOCH='15c81ba4-2fa6-43c3-8895-889f08c931bf'
OP='47debb73-444b-4bb1-9889-fb56885b7922'

@pytest.fixture
def world(monkeypatch):
    with mock_aws():
        monkeypatch.setenv('AWS_DEFAULT_REGION','us-east-1')
        for k,v in {'USERS_TABLE_NAME':'users','DELETION_LEDGER_TABLE_NAME':'ledger','ENVIRONMENT':'dev',
                    'CAMPAIGN_PARTICIPATION_NOTICE_VERSION':NOTICE,'CAMPAIGN_PARTICIPATION_POLICY_VERSION':'independent-research-v1',
                    'CONSENT_INDEPENDENCE_ENABLED':'true'}.items(): monkeypatch.setenv(k,v)
        d=boto3.client('dynamodb')
        for table in ('users','ledger','entitlements'):
            d.create_table(TableName=table,KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],
                AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}],BillingMode='PAY_PER_REQUEST')
        for name in ('config','errors','service'):
            spec=importlib.util.spec_from_file_location(name,ROOT/'src/campaign_participation'/f'{name}.py')
            mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod)
        service=sys.modules['service'];users=boto3.resource('dynamodb').Table('users');ledger=boto3.resource('dynamodb').Table('ledger')
        users.put_item(Item={'PK':'USER#a','SK':'PROFILE','sub':'a','status':'ACTIVE','ageVerified':True})
        legacy={'PK':'USER#a','SK':'CAMPAIGN_PARTICIPATION','schemaVersion':1,'recordVersion':1,'environment':'dev','state':'enrolled','stateVersion':1,
                'noticeVersion':'2026-09-07','policyVersion':'policy-1','consentEpochId':EPOCH,'effectiveFrom':'2026-09-07T00:00:00Z',
                'updatedAt':'2026-09-07T00:00:00Z','lastOperationId':OP}
        yield service,d,users,ledger,legacy


def request(action='join', **changes):
    return {'schemaVersion':2,'expectedStateVersion':0,'action':action,'noticeVersion':NOTICE,'operationId':str(uuid.uuid4())}|changes


def update(s,p): return s.update_participation('a',p,now_epoch=NOW,now_iso='2026-09-21T12:00:00Z')


def test_join_withdraw_rejoin_never_touches_grants_usage_or_audit(world):
    s,d,u,l,legacy=world
    t=boto3.resource('dynamodb').Table('entitlements')
    for sk in ('ENTITLEMENT','ENTITLEMENT#google_play#trustcheck_radar_pro_monthly','USAGE#2026-09'):
        t.put_item(Item={'PK':'USER#a','SK':sk,'remaining':7,'arbitrary':'preserve'})
    before=t.scan()['Items'];u.put_item(Item=legacy)
    old_receipt={'PK':'USER#a','SK':'CAMPAIGN_CONSENT#old','noticeVersion':'2026-09-07','expiresAt':NOW+1}
    u.put_item(Item=old_receipt)
    join=request(expectedStateVersion=1); first=update(s,join)
    assert first['consentEpochId'] != EPOCH and first['stateVersion']==2
    assert update(s,join)==first
    withdraw=request('withdraw',expectedStateVersion=2); second=update(s,withdraw)
    assert second['state']=='withdrawal_pending' and second['withdrawalStatus']=='pending'
    assert update(s,withdraw)==second
    assert t.scan()['Items']==before
    assert u.get_item(Key={'PK':'USER#a','SK':'CAMPAIGN_CONSENT#old'})['Item']==old_receipt
    assert l.scan()['Count']==1
    assert all(int(row['expiresAt'])==NOW+400*86400 for row in u.scan()['Items'] if row['SK'].startswith('CAMPAIGN_OPERATION#'))


def test_false_gate_keeps_old_withdrawal_and_exact_replay_available(world,monkeypatch):
    s,d,u,l,legacy=world;u.put_item(Item=legacy);monkeypatch.setattr(s.config,'CONSENT_INDEPENDENCE_ENABLED',False)
    assert s.get_participation('a')['state']=='review_required'
    p=request('withdraw',schemaVersion=1,noticeVersion='2026-09-07')
    result=update(s,p)
    assert result['acceptedNoticeVersion']=='2026-09-07'
    assert update(s,p)==result and l.scan()['Count']==1
    with pytest.raises(s.AppError):update(s,request())


@pytest.mark.parametrize('change',[{'noticeVersion':'other'},{'schemaVersion':1},{'action':'withdraw'}])
def test_same_operation_changed_identity_cannot_replay(world,change):
    s,d,u,l,_=world;p=request();update(s,p)
    with pytest.raises(s.AppError) as error:update(s,p|change)
    assert error.value.code=='CONFLICT'
    assert l.scan()['Count']==0


@pytest.mark.parametrize('race',['withdrawal','deletion','epoch','policy'])
def test_transaction_races_cannot_publish_new_consent_or_receipts(world,race):
    s,d,u,l,legacy=world;u.put_item(Item=legacy)
    class Race:
        def transact_write_items(self,**kwargs):
            if race=='deletion': l.put_item(Item={'PK':'ACCOUNT#a','SK':'ACCOUNT_DELETION','status':'REQUESTED'})
            else:
                changed={'withdrawal':{'state':'withdrawal_pending','stateVersion':2},'epoch':{'consentEpochId':str(uuid.uuid4())},'policy':{'policyVersion':'changed'}}[race]
                u.put_item(Item=legacy|changed)
            return d.transact_write_items(**kwargs)
    s.dynamodb=Race()
    with pytest.raises(s.AppError):update(s,request(expectedStateVersion=1))
    assert not [x for x in u.scan()['Items'] if x['SK'].startswith(('CAMPAIGN_CONSENT#','CAMPAIGN_OPERATION#'))]


def test_lost_commit_ack_resolves_same_operation_without_second_write(world):
    s,d,u,l,legacy=world;p=request()
    class Lost:
        calls=0
        def transact_write_items(self,**kwargs):
            self.calls+=1;d.transact_write_items(**kwargs);raise OSError('lost transport')
    lost=Lost();s.dynamodb=lost
    with pytest.raises(OSError):update(s,p)
    s.dynamodb=d
    result=update(s,p)
    assert result['operation']['operationId']==p['operationId'] and result['operation']['status']=='applied'
    assert lost.calls==1 and len([x for x in u.scan()['Items'] if x['SK'].startswith('CAMPAIGN_CONSENT#')])==1


def test_contract_fixtures_validate_and_real_response_matches(world):
    import jsonschema
    s,*_=world
    directory=ROOT/'contracts/campaign/research-consent-v2'
    schema=json.loads((directory/'response.schema.json').read_text())
    for row in json.loads((directory/'fixtures.json').read_text()).values():jsonschema.validate(row,schema)
    jsonschema.validate(update(s,request()),schema)


def test_expired_operation_is_unresolved_even_before_physical_ttl(world,monkeypatch):
    s,d,u,l,legacy=world;p=request();update(s,p)
    monkeypatch.setattr(s.time,'time',lambda:NOW+400*86400)
    result=s.get_participation('a',p['operationId'])
    assert result['operation']['status']=='not_found'
    assert result['operation']['action'] is None
    assert u.get_item(Key={'PK':'USER#a','SK':'CAMPAIGN_OPERATION#'+p['operationId']}).get('Item') is not None
    assert result['state']=='enrolled'  # current status never manufactures operation proof


def test_delayed_first_join_cannot_reenroll_after_another_device_withdraws(world):
    s,d,u,l,legacy=world;u.put_item(Item=legacy)
    delayed=request(expectedStateVersion=1)
    update(s,request('withdraw',expectedStateVersion=1))
    with pytest.raises(s.AppError) as error:update(s,delayed)
    assert error.value.code=='CONFLICT'
    assert s.get_participation('a')['state']=='withdrawal_pending'
    assert s.get_participation('a',delayed['operationId'])['operation']['status']=='not_found'


def test_exact_applied_join_replays_after_withdrawal_without_reenrolling(world):
    s,d,u,l,legacy=world;p=request();first=update(s,p)
    update(s,request('withdraw',expectedStateVersion=1))
    result=update(s,p)
    assert result['state']=='withdrawal_pending'
    assert result['operation']['action']=='join' and result['operation']['expectedStateVersion']==0
    with pytest.raises(s.AppError) as error:update(s,p|{'expectedStateVersion':2})
    assert error.value.code=='CONFLICT'
    assert l.scan()['Count']==1
