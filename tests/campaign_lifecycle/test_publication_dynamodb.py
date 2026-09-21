"""Isolated SDK transaction/replay coverage, synthetic records and no AWS calls."""
import os,sys,base64
from pathlib import Path
from uuid import uuid4
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':
    pytest.skip('Run isolated with AMT_AUTHORITY_INTEGRATION=1',allow_module_level=True)
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src/campaign_lifecycle'))
import boto3
from moto import mock_aws
from shared_campaign_locators import core
from publication import Publication,PublicationUnavailable

PERIOD=8
NOW=(PERIOD+1)*14*86400+7*86400
ID='33e21f1c-6a88-45c7-bbcb-1e0f281dcb34'

@pytest.fixture
def world():
    with mock_aws():
        client=boto3.client('dynamodb',region_name='us-east-1')
        for name in ('pipeline','intelligence'):
            client.create_table(TableName=name,KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}],BillingMode='PAY_PER_REQUEST')
        put=lambda table,row:client.put_item(TableName=table,Item=core.serialize(row))
        marker={'PK':'INVENTORY#dev','SK':'CAMPAIGN_LOCATORS','recordType':'CAMPAIGN_LOCATOR_INVENTORY','schemaVersion':1,'revision':1,'environment':'dev','coverage':'VERIFIED_COMPLETE','manifestSha256':'a'*64,'approvedAtEpoch':NOW-1,'locatorSchemaVersion':1,'minimumPeriodId':8,'priorPeriodsErased':True,'writers':core.WRITERS}
        put('pipeline',marker)
        summary={'PK':'CANDIDATE#'+ID,'SK':'SUMMARY','candidateId':ID,'periodId':PERIOD,'taxonomyBucket':'advance_fee','GSI2PK':f'PERIOD#{PERIOD}#BUCKET#advance_fee','GSI2SK':'CANDIDATE#'+ID,'GSI3PK':'EXPIRY#dev','GSI3SK':NOW+1000,'expiresAt':NOW+1000,'version':1,'contributorCount':12,'submissionCount':12}
        put('pipeline',summary)
        for n in range(12):
            token=base64.urlsafe_b64encode(n.to_bytes(32,'big')).decode().rstrip('=')
            contribution={'PK':summary['PK'],'SK':'CONTRIB#'+token,'GSI1PK':f'CONTRIB#{PERIOD}#{token}','GSI1SK':summary['PK'],'periodId':PERIOD,'expiresAt':NOW+1000,'submissionCount':1,'languageId':'en','signalIds':['tactic.urgency','channel.sms'],'vectorApplied':True,'vector':[1]}
            put('pipeline',contribution);put('pipeline',core.locator_for_target(contribution,'dev'))
        publication=Publication(client=client,pipeline='pipeline',intelligence='intelligence',environment='dev',manifest_sha256='a'*64,inventory_revision=1,now=lambda:NOW,enabled=True)
        def get(table,pk,sk):
            raw=client.get_item(TableName=table,Key=core.serialize({'PK':pk,'SK':sk}),ConsistentRead=True).get('Item')
            return core.deserialize(raw) if raw else None
        yield publication,client,put,get,summary,marker


def finish(publication):
    for _ in range(10):
        result=publication.process(ID)
        if result['complete']:return result
    pytest.fail('bounded publication did not complete')


def test_atomic_publication_then_paired_cleanup_keeps_summary_last(world):
    p,d,put,get,summary,marker=world
    assert p.process(ID)['state']=='FROZEN'
    assert get('intelligence','CAMPAIGN#'+ID,'AGGREGATE') is None
    assert p.process(ID)['state']=='PUBLISHED'
    aggregate=get('intelligence','CAMPAIGN#'+ID,'AGGREGATE')
    assert aggregate['contributorCount']==12 and aggregate['languageIds']==['en']
    assert p.process(ID)=={'state':'PUBLISHED','deleted':10,'complete':False}
    assert get('pipeline',summary['PK'],'SUMMARY') is not None
    assert finish(p)['complete']
    assert get('pipeline',summary['PK'],'SUMMARY') is None
    remaining=[core.deserialize(v) for v in d.scan(TableName='pipeline')['Items']]
    assert remaining==[core.deserialize(core.serialize(marker))]
    assert get('intelligence','CAMPAIGN#'+ID,'AGGREGATE')==aggregate


def test_lost_publication_ack_resumes_existing_aggregate_without_recomputing_partial_input(world):
    p,d,put,get,summary,_=world;p.process(ID)
    original=p._transaction
    def lost(*a,**kw):
        original(*a,**kw)
        raise RuntimeError('lost acknowledgment')
    p._transaction=lost
    with pytest.raises(RuntimeError):p.process(ID)
    aggregate=get('intelligence','CAMPAIGN#'+ID,'AGGREGATE')
    assert get('pipeline',summary['PK'],'SUMMARY')['lifecycleState']=='PUBLISHED'
    p._transaction=original;p.process(ID)
    assert finish(p)['complete']
    assert get('intelligence','CAMPAIGN#'+ID,'AGGREGATE')==aggregate


def test_cleanup_failure_and_lost_ack_retries_do_not_overwrite_aggregate(world):
    p,d,put,get,summary,_=world;p.process(ID);p.process(ID)
    original=p._transaction;calls=[0]
    def lost(*a,**kw):
        original(*a,**kw);calls[0]+=1
        if calls[0]==1:raise RuntimeError('lost delete ack')
    p._transaction=lost
    with pytest.raises(RuntimeError):p.process(ID)
    assert get('pipeline',summary['PK'],'SUMMARY') is not None
    p._transaction=original
    assert finish(p)['complete']


def test_missing_locator_blocks_before_publication(world):
    p,d,put,get,summary,_=world
    locator=next(core.deserialize(v) for v in d.scan(TableName='pipeline')['Items'] if v.get('recordType',{}).get('S')=='CAMPAIGN_CONTRIBUTOR_LOCATOR')
    d.delete_item(TableName='pipeline',Key=core.serialize({'PK':locator['PK'],'SK':locator['SK']}))
    p.process(ID)
    with pytest.raises(core.LocatorUnavailable):p.process(ID)
    assert get('intelligence','CAMPAIGN#'+ID,'AGGREGATE') is None


def test_summary_change_during_publication_cannot_publish(world):
    p,d,put,get,summary,_=world;p.process(ID)
    original=p._transaction
    def race(inventory,actions):
        d.update_item(TableName='pipeline',Key=core.serialize({'PK':summary['PK'],'SK':'SUMMARY'}),UpdateExpression='SET #v=:v',ExpressionAttributeNames={'#v':'version'},ExpressionAttributeValues=core.serialize({':v':3}))
        return original(inventory,actions)
    p._transaction=race
    with pytest.raises(Exception):p.process(ID)
    assert get('intelligence','CAMPAIGN#'+ID,'AGGREGATE') is None


def test_inventory_change_during_cleanup_cannot_delete(world):
    p,d,put,get,summary,marker=world;p.process(ID);p.process(ID)
    original=p._transaction
    def race(inventory,actions):
        put('pipeline',marker|{'revision':2})
        return original(inventory,actions)
    p._transaction=race
    with pytest.raises(Exception):p.process(ID)
    assert get('pipeline',summary['PK'],'SUMMARY') is not None
    assert len(d.query(TableName='pipeline',KeyConditionExpression='PK=:p AND begins_with(SK,:s)',ExpressionAttributeValues=core.serialize({':p':summary['PK'],':s':'CONTRIB#'}))['Items'])==12


def test_review_state_and_publication_index_do_not_block_safe_cleanup(world):
    p,d,put,get,_,_=world;p.process(ID);p.process(ID)
    value=get('intelligence','CAMPAIGN#'+ID,'AGGREGATE')
    put('intelligence',value|{'state':'PUBLISHED','version':3,'GSI1PK':'STATE#PUBLISHED','GSI1SK':value['periodWeek']+'#CAMPAIGN#'+ID})
    assert finish(p)['complete']


def test_changed_immutable_aggregate_blocks_cleanup(world):
    p,d,put,get,summary,_=world;p.process(ID);p.process(ID)
    value=get('intelligence','CAMPAIGN#'+ID,'AGGREGATE')
    put('intelligence',value|{'contributorCount':999})
    with pytest.raises(PublicationUnavailable):p.process(ID)
    assert get('pipeline',summary['PK'],'SUMMARY') is not None


def test_expired_source_is_suppressed_without_new_aggregate_or_extended_deadline(world):
    p,d,put,get,summary,_=world
    put('pipeline',summary|{'expiresAt':NOW,'GSI3SK':NOW})
    p.process(ID)
    assert p.process(ID)['state']=='SUPPRESSED'
    assert get('pipeline',summary['PK'],'SUMMARY')['expiresAt']==NOW
    assert finish(p)['complete']
    assert get('intelligence','CAMPAIGN#'+ID,'AGGREGATE') is None


def test_unqualified_legacy_aggregate_and_disabled_candidate_fail_before_mutation(world):
    p,d,put,get,summary,_=world
    p.enabled=False
    with pytest.raises(PublicationUnavailable):p.process(ID)
    assert get('pipeline',summary['PK'],'SUMMARY')==summary
    p.enabled=True;put('intelligence',{'PK':'CAMPAIGN#'+ID,'SK':'AGGREGATE'})
    with pytest.raises(PublicationUnavailable):p.process(ID)
    assert get('pipeline',summary['PK'],'SUMMARY')==summary


def test_pending_deletion_recompute_blocks_atomic_freeze(world):
    p,d,put,get,summary,_=world
    put('pipeline', {'PK':summary['PK'],'SK':'DELETION_RECOMPUTE'})
    with pytest.raises(Exception):p.process(ID)
    assert get('pipeline',summary['PK'],'SUMMARY')==summary


def test_orphan_expiration_pairs_target_and_locator_only_after_deadline(world):
    p,d,put,get,summary,_=world
    locator=next(core.deserialize(v) for v in d.scan(TableName='pipeline')['Items'] if v.get('recordType',{}).get('S')=='CAMPAIGN_CONTRIBUTOR_LOCATOR')
    with pytest.raises(PublicationUnavailable):p.expire_locator(locator['PK'],locator['SK'])
    p.now=lambda:NOW+1000
    with pytest.raises(PublicationUnavailable):p.expire_locator(locator['PK'],locator['SK'])
    d.delete_item(TableName='pipeline',Key=core.serialize({'PK':summary['PK'],'SK':'SUMMARY'}))
    assert p.expire_locator(locator['PK'],locator['SK'])=={'expiredPairs':1,'scopeComplete':False}
    assert get('pipeline',locator['PK'],locator['SK']) is None
    assert get('pipeline',locator['targetPK'],locator['targetSK']) is None
    with pytest.raises(PublicationUnavailable):p.expire_locator(locator['PK'],locator['SK'])


def test_ttl_removed_feature_still_erases_owned_siblings(world):
    p,d,put,get,summary,_=world
    token='A'*43;event=str(uuid4())
    feature={'PK':'EVENT#'+event,'SK':'FEATURE','eventId':event,'periodId':PERIOD,'expiresAt':NOW,
        'GSI1PK':f'CONTRIB#{PERIOD}#{token}','GSI1SK':'EVENT#'+event}
    locator=core.locator_for_target(feature,'dev')
    put('pipeline',locator)
    for sk in ('DEDUPE','CLUSTERED'):
        put('pipeline',{'PK':feature['PK'],'SK':sk,'expiresAt':NOW}|core.locator_pointer(locator))
    assert p.expire_locator(locator['PK'],locator['SK'])['expiredPairs']==1
    for sk in ('FEATURE','DEDUPE','CLUSTERED'):
        assert get('pipeline',feature['PK'],sk) is None
    assert get('pipeline',locator['PK'],locator['SK']) is None


def test_expiration_cannot_erase_replaced_sibling(world):
    p,d,put,get,summary,_=world
    token='A'*43;event=str(uuid4())
    feature={'PK':'EVENT#'+event,'SK':'FEATURE','eventId':event,'periodId':PERIOD,'expiresAt':NOW,
        'GSI1PK':f'CONTRIB#{PERIOD}#{token}','GSI1SK':'EVENT#'+event}
    locator=core.locator_for_target(feature,'dev')
    put('pipeline',locator);put('pipeline',feature)
    put('pipeline',{'PK':feature['PK'],'SK':'DEDUPE','expiresAt':NOW+1}|core.locator_pointer(locator))
    with pytest.raises(Exception):p.expire_locator(locator['PK'],locator['SK'])
    assert get('pipeline',feature['PK'],'FEATURE') is not None
    assert get('pipeline',locator['PK'],locator['SK']) is not None


def test_upgraded_handler_disabled_never_falls_back_to_legacy(world,monkeypatch):
    import importlib.util
    p,d,put,get,summary,_=world
    root=Path(__file__).resolve().parents[2]/'src/campaign_lifecycle'
    def load(name,path):
        spec=importlib.util.spec_from_file_location(name,path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module
    monkeypatch.setenv('APP_ENVIRONMENT','dev');monkeypatch.setenv('PIPELINE_TABLE_NAME','pipeline')
    monkeypatch.setenv('INTELLIGENCE_TABLE_NAME','intelligence');monkeypatch.setenv('EXPIRATION_INDEX_NAME','ExpirationIndex')
    monkeypatch.setenv('AWS_DEFAULT_REGION','us-east-1')
    configuration=load('lifecycle_candidate_config',root/'config.py')
    monkeypatch.setitem(sys.modules,'config',configuration)
    app=load('lifecycle_candidate_app',root/'app.py')
    event={'environment':'dev','schemaVersion':1,'operation':'manage_keys'}
    with pytest.raises(RuntimeError,match='disabled'):app.lambda_handler(event,None)
    monkeypatch.setattr(configuration,'CAMPAIGN_LIFECYCLE_CANDIDATE_ENABLED',True)
    with pytest.raises(RuntimeError,match='pins'):app.lambda_handler(event,None)
    monkeypatch.setattr(configuration,'CAMPAIGN_LOCATOR_MANIFEST_SHA256','a'*64)
    monkeypatch.setattr(configuration,'CAMPAIGN_LOCATOR_INVENTORY_REVISION','1')
    for operation in ('manage_keys','finalize_periods','expire_transient'):
        with pytest.raises(ValueError):app.lambda_handler(event|{'operation':operation},None)
    assert get('pipeline',summary['PK'],'SUMMARY')==summary
