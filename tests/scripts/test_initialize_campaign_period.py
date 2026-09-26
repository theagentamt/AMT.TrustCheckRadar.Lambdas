"""Create-only initializer, real SDK/Moto DDB and injected key metadata only."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('Isolated SDK qualification',allow_module_level=True)
import boto3
from moto import mock_aws
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('initializer',ROOT/'scripts/initialize_campaign_period.py')
I=importlib.util.module_from_spec(spec);spec.loader.exec_module(I)
PERIOD=1480
NOW=PERIOD*I.PERIOD_SECONDS+100
ARN=f'arn:aws:kms:{I.REGION}:{I.ACCOUNT}:key/11111111-1111-4111-8111-111111111111'
REQUEST={'periodId':PERIOD,'keyArn':ARN,'generation':'22222222-2222-4222-8222-222222222222','locatorManifestSha256':'a'*64,'locatorInventoryRevision':1}

@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv('MOTO_ACCOUNT_ID',I.ACCOUNT)
    with mock_aws():
        d=boto3.client('dynamodb',region_name=I.REGION)
        d.create_table(TableName=I.TABLE,BillingMode='PAY_PER_REQUEST',KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':x,'AttributeType':'S'} for x in ('PK','SK')])
        from shared_campaign_locators.core import WRITERS
        inventory={'PK':'INVENTORY#dev','SK':'CAMPAIGN_LOCATORS','recordType':'CAMPAIGN_LOCATOR_INVENTORY','schemaVersion':1,'revision':1,'environment':'dev','coverage':'VERIFIED_COMPLETE','manifestSha256':'a'*64,'approvedAtEpoch':NOW-10,'locatorSchemaVersion':1,'minimumPeriodId':PERIOD-1,'priorPeriodsErased':True,'writers':WRITERS}
        d.put_item(TableName=I.TABLE,Item=I.serialize(inventory))
        meta=SimpleNamespace(region_name=I.REGION)
        key={'Arn':ARN,'KeyState':'Enabled','KeySpec':'HMAC_256','KeyUsage':'GENERATE_VERIFY_MAC','KeyManager':'CUSTOMER'}
        tags={'Project':'trustcheckradar','Environment':'dev','Purpose':'campaign-contributor-token','PeriodId':str(PERIOD)}
        kms=SimpleNamespace(meta=meta,describe_key=lambda **kw:{'KeyMetadata':key.copy()},list_resource_tags=lambda **kw:{'Tags':[{'TagKey':k,'TagValue':v} for k,v in tags.items()]})
        sts=SimpleNamespace(meta=meta,get_caller_identity=lambda:{'Account':I.ACCOUNT})
        clock=[NOW];tool=I.Initializer(d,kms,sts,now=lambda:clock[0])
        yield tool,d,key,tags,clock,inventory


def current(d):
    row=d.get_item(TableName=I.TABLE,Key=I.serialize({'PK':f'PERIOD#{PERIOD}','SK':'HMAC_KEY'}),ConsistentRead=True).get('Item')
    return I.plain(I.deserialize(row)) if row else None


def test_dry_run_never_writes_and_exact_apply_replay_preserves_timestamp(world):
    tool,d,*rest=world
    before=d.scan(TableName=I.TABLE)['Items'];plan=tool.plan(REQUEST)
    assert d.scan(TableName=I.TABLE)['Items']==before
    assert tool.apply(plan)['alreadyPresent'] is False
    first=current(d);rest[2][0]+=1
    assert tool.apply(plan)['alreadyPresent'] is True and current(d)==first==plan['row']
    assert first['retireAfterEpoch']==(PERIOD+1)*I.PERIOD_SECONDS+I.RECOVERY_SECONDS


@pytest.mark.parametrize('field,value',[('periodId',True),('periodId',PERIOD-1),('keyArn',ARN.replace(I.ACCOUNT,'111111111111')),('keyArn','alias/unsafe'),('generation','not-uuid'),('locatorInventoryRevision',True),('locatorManifestSha256','a')])
def test_invalid_request_preserves_registry(world,field,value):
    tool,d,*_=world
    with pytest.raises(I.Unavailable):tool.plan(REQUEST|{field:value})
    assert current(d) is None


@pytest.mark.parametrize('mutation',[{'KeyState':'PendingDeletion'},{'KeySpec':'SYMMETRIC_DEFAULT'},{'KeyUsage':'ENCRYPT_DECRYPT'},{'Arn':ARN.replace('us-east-1','us-west-2')},{'KeyManager':'AWS'}])
def test_wrong_key_never_writes(world,mutation):
    tool,d,key,*_=world;key.update(mutation)
    with pytest.raises(I.Unavailable):tool.plan(REQUEST)
    assert current(d) is None


def test_wrong_identity_tags_and_paginated_tags_refused(world):
    tool,d,key,tags,*_=world
    tags['PeriodId']=str(PERIOD-1)
    with pytest.raises(I.Unavailable):tool.plan(REQUEST)
    tags['PeriodId']=str(PERIOD)
    tool.sts.get_caller_identity=lambda:{'Account':'111111111111'}
    with pytest.raises(I.Unavailable):tool.plan(REQUEST)
    tool.sts.get_caller_identity=lambda:{'Account':I.ACCOUNT}
    tool.kms.list_resource_tags=lambda **kw:{'Tags':[],'Truncated':True,'NextMarker':'unknown'}
    with pytest.raises(I.Unavailable):tool.plan(REQUEST)
    assert current(d) is None


@pytest.mark.parametrize('mutation',[{'status':'RETIRED'},{'admissionState':'CLOSING'},{'admissionRevision':True},{'admissionGeneration':'33333333-3333-4333-8333-333333333333'},{'extra':'unknown'}])
def test_existing_conflict_is_never_repaired(world,mutation):
    tool,d,*_=world;plan=tool.plan(REQUEST);changed=plan['row']|mutation
    d.put_item(TableName=I.TABLE,Item=I.serialize(changed))
    with pytest.raises(I.Unavailable):tool.apply(plan)
    assert current(d)==changed


def test_legacy_row_is_preserved_without_adoption(world):
    tool,d,*_=world;plan=tool.plan(REQUEST);legacy={k:v for k,v in plan['row'].items() if not k.startswith('admission')}
    d.put_item(TableName=I.TABLE,Item=I.serialize(legacy))
    with pytest.raises(I.Unavailable):tool.plan(REQUEST)
    with pytest.raises(I.Unavailable):tool.apply(plan)
    assert current(d)==legacy


@pytest.mark.parametrize('race',['inventory','period'])
def test_transaction_races_cannot_partially_initialize(world,monkeypatch,race):
    tool,d,key,tags,clock,inventory=world;plan=tool.plan(REQUEST);original=d.transact_write_items
    other=plan['row']|{'keyArn':ARN.replace('11111111-1111','44444444-4444')}
    def racing(**kw):
        row=inventory|{'revision':2} if race=='inventory' else other
        d.put_item(TableName=I.TABLE,Item=I.serialize(row))
        return original(**kw)
    monkeypatch.setattr(d,'transact_write_items',racing)
    with pytest.raises(I.Unavailable):tool.apply(plan)
    assert current(d)==(None if race=='inventory' else other)


def test_lost_commit_ack_retry_does_check_only_and_no_timestamp_refresh(world,monkeypatch):
    tool,d,key,tags,clock,_=world;plan=tool.plan(REQUEST);original=d.transact_write_items
    def lost(**kw):original(**kw);raise RuntimeError('SYNTHETIC_PRIVATE_ACK_LOSS')
    monkeypatch.setattr(d,'transact_write_items',lost)
    with pytest.raises(I.Unavailable):tool.apply(plan)
    assert current(d)==plan['row'];clock[0]+=1;seen=[]
    def retry(**kw):seen.extend(kw['TransactItems']);return original(**kw)
    monkeypatch.setattr(d,'transact_write_items',retry)
    assert tool.apply(plan)['alreadyPresent'] is True
    assert all('ConditionCheck' in action for action in seen) and current(d)==plan['row']


@pytest.mark.parametrize('phase',['key','transaction'])
def test_rollover_before_or_after_commit_never_reports_ready(world,monkeypatch,phase):
    tool,d,key,tags,clock,_=world;plan=tool.plan(REQUEST)
    if phase=='key':
        real=tool.kms.describe_key
        def change(**kw):clock[0]=(PERIOD+1)*I.PERIOD_SECONDS;return real(**kw)
        tool.kms.describe_key=change
    else:
        real=d.transact_write_items
        def change(**kw):out=real(**kw);clock[0]=(PERIOD+1)*I.PERIOD_SECONDS;return out
        monkeypatch.setattr(d,'transact_write_items',change)
    with pytest.raises(I.Unavailable):tool.apply(plan)
    assert current(d)==(None if phase=='key' else plan['row'])


def test_plan_parser_duplicate_and_boolean_fields_are_rejected(world,tmp_path):
    tool,*_=world;plan=tool.plan(REQUEST);path=tmp_path/'plan.json'
    path.write_text('{"schemaVersion":1,"schemaVersion":1}')
    with pytest.raises(I.Unavailable):I.read_plan(path)
    plan['row']['admissionRevision']=True;path.write_text(json.dumps(plan))
    with pytest.raises(I.Unavailable):I.read_plan(path)


def test_cli_dry_run_and_apply_use_exact_plan_and_sanitized_errors(world,tmp_path,monkeypatch,capsys):
    tool,d,*_=world;clients={'dynamodb':d,'kms':tool.kms,'sts':tool.sts};closed=[]
    for name,c in clients.items():c.close=lambda name=name:closed.append(name)
    monkeypatch.setattr(I.time,'time',lambda:NOW)
    monkeypatch.setattr(boto3,'client',lambda name,**kw:clients[name])
    path=tmp_path/'plan.json'
    argv=['--plan-out',str(path),'--period-id',str(PERIOD),'--key-arn',ARN,'--generation',REQUEST['generation'],
        '--locator-manifest-sha256','a'*64,'--locator-inventory-revision','1']
    assert I.main(argv)==0 and current(d) is None
    assert json.loads(capsys.readouterr().out)['dryRun'] is True and len(closed)==3
    assert I.main(['--apply-plan',str(path)])==0
    assert json.loads(capsys.readouterr().out)['initialized'] is True and len(closed)==6
    tool.kms.describe_key=lambda **kw:(_ for _ in ()).throw(RuntimeError('private-key-detail'))
    assert I.main(['--apply-plan',str(path)])==1
    assert 'private-key-detail' not in capsys.readouterr().out


@pytest.mark.parametrize('field,value',[('admissionChangedAtEpoch',NOW-1000),('admissionChangedAtEpoch',NOW-10)])
def test_plan_cannot_predate_period_or_inventory_approval(world,field,value):
    tool,d,*_=world;plan=tool.plan(REQUEST);plan['row'][field]=value
    with pytest.raises(I.Unavailable):tool.apply(plan)
    assert current(d) is None


def test_changed_inventory_same_pins_is_not_accepted(world):
    tool,d,key,tags,clock,inventory=world;plan=tool.plan(REQUEST)
    d.put_item(TableName=I.TABLE,Item=I.serialize(inventory|{'approvedAtEpoch':NOW-5}))
    with pytest.raises(I.Unavailable):tool.apply(plan)
    assert current(d) is None
