"""Whole-period races using real SDK transactions; synthetic generation only."""
import os
from copy import deepcopy
from uuid import uuid4
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('Isolated SDK only',allow_module_level=True)
from tests.shared_campaign_locators.test_dynamodb import world,publish,aggregate,get,wire,EVENT,PERIOD,NOW,PINS
from tests.campaign_period_fixtures import GENERATION,row
from shared_campaign_locators import period as P
from shared_campaign_locators.core import LocatorUnavailable

END=(PERIOD+1)*P.PERIOD_SECONDS

def close(d,**kw):return P.close(d,'pipeline','dev',PERIOD,'a'*64,1,END,**kw)
def state(d):return get(d,'pipeline',f'PERIOD#{PERIOD}','HMAC_KEY')

def test_close_is_irreversible_idempotent_and_does_not_change_retention(world):
    d,put=world;before=state(d)
    assert close(d)=={'state':'CLOSING','changed':True,'periodComplete':False,'retirementEligible':False}
    after=state(d)
    assert after==before|{'admissionState':'CLOSING','admissionRevision':2,'admissionChangedAtEpoch':END}
    assert close(d)['changed'] is False and state(d)==after
    with pytest.raises(LocatorUnavailable):publish(d)

@pytest.mark.parametrize('change',[{'admissionGeneration':str(uuid4())},{'admissionState':'SEALED'},
    {'admissionRevision':True},{'admissionSchemaVersion':2},{'admissionManifestSha256':'b'*64},
    {'admissionInventoryRevision':2},{'admissionChangedAtEpoch':END+1},{'status':'RETIRED'},
    {'keyArn':'alias/unchecked'},{'unknown':1}])
def test_unknown_or_unqualified_record_preserved_without_mutation(world,change):
    d,put=world;put(state(d)|change);before=state(d)
    with pytest.raises(LocatorUnavailable):close(d)
    assert state(d)==before

@pytest.mark.parametrize('mode',['disabled','legacy','absent','direct_key'])
def test_no_legacy_or_direct_key_bypass_before_mac(world,monkeypatch,mode):
    d,put=world
    if mode=='disabled':monkeypatch.setenv('CAMPAIGN_PERIOD_ADMISSION_ENABLED','false')
    elif mode=='legacy':put({k:v for k,v in state(d).items() if not k.startswith('admission')})
    elif mode=='absent':d.delete_item(TableName='pipeline',Key=wire({'PK':f'PERIOD#{PERIOD}','SK':'HMAC_KEY'}))
    if mode=='direct_key':
        # A syntactically valid registry ARN cannot be replaced by a supplied alias.
        from tests.shared_campaign_locators.test_dynamodb import publisher,observation
        with pytest.raises(RuntimeError):publisher.publish_observation(observation(),pipeline_table_name='pipeline',users_table_name='users',deletion_ledger_table_name='ledger',cluster_queue_url='unused',hmac_key_id='alias/unchecked',transient_retention_days=21,dynamodb_client=d,kms_client=None,sqs_client=None,now_epoch=NOW,**PINS)
    else:
        with pytest.raises(LocatorUnavailable):publish(d)
    assert get(d,'pipeline','EVENT#'+EVENT,'FEATURE') is None

@pytest.mark.parametrize('stage',['first','last','send'])
def test_publisher_close_race_never_admits_after_fence(world,stage):
    d,put=world
    from tests.shared_campaign_locators.test_dynamodb import publisher,observation
    real=d.transact_write_items;calls=[];sent=[]
    def racing(**kw):
        calls.append(kw)
        if (stage=='first' and len(calls)==1) or (stage=='last' and len(calls)==2):close(d)
        return real(**kw)
    class Client:
        def __getattr__(self,n):return racing if n=='transact_write_items' else getattr(d,n)
    class Sqs:
        def send_message(self,**kw):
            sent.append(kw)
            if stage=='send':close(d)
    from types import SimpleNamespace
    with pytest.raises(Exception):publisher.publish_observation(observation(),pipeline_table_name='pipeline',users_table_name='users',deletion_ledger_table_name='ledger',cluster_queue_url='unused',hmac_key_id=state(d)['keyArn'],transient_retention_days=21,dynamodb_client=Client(),kms_client=SimpleNamespace(generate_mac=lambda **_:{'Mac':b'x'*32}),sqs_client=Sqs(),now_epoch=NOW,**PINS)
    if stage=='first':assert not sent and get(d,'pipeline','EVENT#'+EVENT,'FEATURE') is None
    else:
        assert get(d,'pipeline','EVENT#'+EVENT,'DEDUPE')['status']=='PENDING'
        with pytest.raises(LocatorUnavailable):aggregate(d)
        assert get(d,'pipeline','EVENT#'+EVENT,'CLUSTERED') is None

@pytest.mark.parametrize('branch',['new','repeat','capped'])
def test_every_cluster_write_branch_has_atomic_open_guard(world,branch):
    d,put=world
    if branch!='new':
        publish(d);aggregate(d)
        if branch=='capped':
            for _ in range(2):
                e=str(uuid4());publish(d,e);aggregate(d,e)
    event=str(uuid4());publish(d,event)
    before=[v for v in d.scan(TableName='pipeline')['Items'] if v.get('SK',{}).get('S')!='HMAC_KEY']
    real=d.transact_write_items
    class Race:
        def __getattr__(self,n):return getattr(d,n)
        def transact_write_items(self,**kw):close(d);return real(**kw)
    with pytest.raises(d.exceptions.TransactionCanceledException):aggregate(Race(),event)
    after=[v for v in d.scan(TableName='pipeline')['Items'] if v.get('SK',{}).get('S')!='HMAC_KEY']
    assert after==before

@pytest.mark.parametrize('target',['inventory','record'])
def test_close_cas_refuses_changed_evidence(world,target):
    d,put=world;real=d.transact_write_items
    class Race:
        def __getattr__(self,n):return getattr(d,n)
        def transact_write_items(self,**kw):
            if target=='record':put(state(d)|{'admissionGeneration':str(uuid4())})
            else:
                inv=get(d,'pipeline','INVENTORY#dev','CAMPAIGN_LOCATORS');put(inv|{'revision':2})
            return real(**kw)
    with pytest.raises(d.exceptions.TransactionCanceledException):close(Race())
    assert state(d)['admissionState']=='OPEN'

def test_lost_close_ack_replays_without_second_mutation(world):
    d,put=world;real=d.transact_write_items
    class Lost:
        def __getattr__(self,n):return getattr(d,n)
        def transact_write_items(self,**kw):real(**kw);raise RuntimeError('synthetic lost ack')
    with pytest.raises(RuntimeError):close(Lost())
    before=state(d);assert close(d)['changed'] is False;assert state(d)==before

def test_budget_and_period_end_refuse_before_write(world):
    d,put=world;before=state(d)
    with pytest.raises(LocatorUnavailable):P.close(d,'pipeline','dev',PERIOD,'a'*64,1,NOW)
    with pytest.raises(LocatorUnavailable):close(d,remaining_ms=lambda:5999)
    assert state(d)==before

def test_duplicate_period_guard_deduplicates_only_exact_observation(world):
    d,put=world;record=state(d);guard=P.condition('pipeline',record)
    P.GuardedClient(d,'pipeline',record).transact_write_items(TransactItems=[guard])
    with pytest.raises(LocatorUnavailable):P.GuardedClient(d,'pipeline',record).transact_write_items(TransactItems=[P.condition('pipeline',record|{'admissionRevision':2})])

@pytest.mark.parametrize('change',[{'keyArn':row(PERIOD)['keyArn'].replace('107827791950','111111111111')},
    {'keyArn':row(PERIOD)['keyArn'].replace('us-east-1','us-west-2')},
    {'admissionRevision':9007199254740992}])
def test_foreign_identity_and_unbounded_revision_refused(world,change):
    d,put=world;put(state(d)|change)
    with pytest.raises(LocatorUnavailable):publish(d)
    with pytest.raises(LocatorUnavailable):close(d)


def test_duplicate_guard_collision_after_first_match_refused_locally(world):
    d,put=world;record=state(d);guard=P.condition('pipeline',record)
    with pytest.raises(LocatorUnavailable):P.GuardedClient(d,'pipeline',record).transact_write_items(TransactItems=[guard,guard])
