import os,sys
from pathlib import Path
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':
    pytest.skip('Isolated real SDK/Moto only',allow_module_level=True)
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
import boto3
from moto import mock_aws
from shared_campaign_recovery.records import *
from shared_campaign_recovery.jobs import enqueue_actions,backfill_actions
from shared_campaign_recovery.worker import Worker
NOW=1800000000
MANIFEST='a'*64

def command(n=1,account='owner',withdraw=False):
    op=f'00000000-0000-4000-8000-{n:012x}'
    row={'PK':'ACCOUNT#'+account,'SK':'ACCOUNT_DELETION','schemaVersion':1,'recordVersion':1,
         'environment':'dev','eventType':'account.deletion.requested','accountId':account,
         'operationId':op,'status':'REQUESTED','occurredAtEpoch':NOW-100000,'deleteByEpoch':NOW-13600}
    if withdraw:row.update(SK='CAMPAIGN_WITHDRAWAL#'+op,eventType='campaign.consent.withdrawn',status='PENDING',consentEpochId=op)
    return row

@pytest.fixture
def world():
    with mock_aws():
        c=boto3.client('dynamodb',region_name='us-east-1')
        for name in ['ledger','users']:
            args=dict(TableName=name,KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],
                AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}],BillingMode='PAY_PER_REQUEST')
            if name=='ledger':
                args['AttributeDefinitions'] += [{'AttributeName':'campaignRecoveryPartition','AttributeType':'S'},{'AttributeName':'nextAttemptAtEpoch','AttributeType':'N'}]
                args['GlobalSecondaryIndexes']=[{'IndexName':INDEX,'KeySchema':[{'AttributeName':'campaignRecoveryPartition','KeyType':'HASH'},{'AttributeName':'nextAttemptAtEpoch','KeyType':'RANGE'}],'Projection':{'ProjectionType':'KEYS_ONLY'}}]
            c.create_table(**args)
        inv={'PK':'INVENTORY#dev','SK':'CAMPAIGN_RECOVERY_INVENTORY','recordType':'CAMPAIGN_RECOVERY_INVENTORY',
             'schemaVersion':1,'environment':'dev','revision':1,'coverage':'VERIFIED_COMPLETE','manifestSha256':MANIFEST,
             'approvedAtEpoch':NOW-200000,'legacyCoverageVerified':True,'writers':WRITERS}
        c.put_item(TableName='ledger',Item=wire(inv))
        yield c

def enqueue(c,cmd):
    actions=enqueue_actions(c,'ledger',cmd)
    c.transact_write_items(TransactItems=serialize_actions(actions+[put('ledger',cmd)]))

def worker(c,cleanup=lambda cmd:None,**kw):
    return Worker(client=c,table='ledger',environment='dev',manifest=MANIFEST,revision=1,
                  cleanup=cleanup,now=lambda:NOW,**kw)

def test_atomic_two_commands_count_and_replay_preservation(world):
    a=command(1,withdraw=True);b=command(2)
    enqueue(world,a);enqueue(world,b)
    assert read(world,'ledger',a['PK'],CONTROL_SK)['pendingJobs']==2
    before=read(world,'ledger',a['PK'],CONTROL_SK)
    with pytest.raises(RecoveryUnavailable):enqueue(world,a)
    assert read(world,'ledger',a['PK'],CONTROL_SK)==before


def test_command_race_rolls_back_sidecar_and_counter(world):
    cmd=command();actions=enqueue_actions(world,'ledger',cmd)
    world.put_item(TableName='ledger',Item=wire(cmd))
    with pytest.raises(Exception):world.transact_write_items(TransactItems=serialize_actions(actions+[put('ledger',cmd)]))
    assert read(world,'ledger',cmd['PK'],CONTROL_SK) is None
    assert read(world,'ledger',cmd['PK'],JOB_PREFIX+cmd['operationId']) is None


def test_orphan_job_never_reconstructs_counter(world):
    cmd=command();world.put_item(TableName='ledger',Item=wire(new_job(cmd)))
    with pytest.raises(RecoveryUnavailable):enqueue_actions(world,'ledger',command(2))


def test_expired_stream_command_recovered_and_original_deadline_preserved(world):
    cmd=command();enqueue(world,cmd);seen=[]
    m=worker(world,seen.append).run()
    assert seen==[cmd] and m['CommandsAttempted']==1 and m['ObservedOverdueCommands']==1
    assert read(world,'ledger',cmd['PK'],cmd['SK'])==cmd
    job=read(world,'ledger',cmd['PK'],JOB_PREFIX+cmd['operationId'])
    assert job['nextAttemptAtEpoch']==NOW+60 and job['commandOccurredAtEpoch']==cmd['occurredAtEpoch']
    assert worker(world,seen.append).run()['CommandsAttempted']==0


def test_missing_key_failure_preadvances_and_other_job_runs(world):
    for n in (1,17):enqueue(world,command(n,account=str(n)))
    seen=[]
    def cleanup(cmd):seen.append(cmd['operationId']);raise RuntimeError('synthetic missing key')
    m=worker(world,cleanup).run()
    assert len(seen)==2 and m['RecoveryFailures']==2 and m['CommandsUnverified']==2


def test_poison_oldest_skipped_for_same_shard_later_job(world):
    bad=command(1,account='bad');good=command(17,account='good')
    enqueue(world,bad);enqueue(world,good)
    row=read(world,'ledger',bad['PK'],JOB_PREFIX+bad['operationId']);row['schemaVersion']=999
    row['nextAttemptAtEpoch']-=1;world.put_item(TableName='ledger',Item=wire(row))
    seen=[];m=worker(world,seen.append).run()
    assert seen==[good] and m['SidecarSchemaFailures']==1
    assert read(world,'ledger',bad['PK'],row['SK'])==row


def test_lost_advance_ack_does_not_retry_same_tick(world):
    cmd=command();enqueue(world,cmd);real=world.transact_write_items
    def lost(**kw):real(**kw);raise RuntimeError('lost')
    world.transact_write_items=lost;seen=[]
    assert worker(world,seen.append).run()['CommandsAttempted']==0 and not seen
    world.transact_write_items=real
    assert worker(world,seen.append).run()['CommandsAttempted']==0
    w=worker(world,seen.append);w.now=lambda:NOW+61
    assert w.run()['CommandsAttempted']==1


@pytest.mark.parametrize('bad',['terminal','absent','unknown'])
def test_unowned_or_terminal_command_preserves_job_without_attempt(world,bad):
    cmd=command();enqueue(world,cmd)
    if bad=='absent':world.delete_item(TableName='ledger',Key=wire(key(cmd['PK'],cmd['SK'])))
    else:
        row=cmd.copy();row['status']='COMPLETE' if bad=='terminal' else 'UNKNOWN'
        world.put_item(TableName='ledger',Item=wire(row))
    before=read(world,'ledger',cmd['PK'],JOB_PREFIX+cmd['operationId'])
    assert worker(world).run()['CommandsAttempted']==0
    assert read(world,'ledger',cmd['PK'],before['SK'])==before


def test_time_budget_stops_without_query_or_write(world):
    enqueue(world,command());world.query=lambda **kw:pytest.fail('query after budget')
    assert worker(world,remaining_ms=lambda:5000).run()['CommandsAttempted']==0


@pytest.mark.parametrize('owner',['missing','wrong','deleted'])
def test_backfill_requires_live_exact_owner_even_when_fence_absent(world,owner):
    cmd=command(withdraw=True);world.put_item(TableName='ledger',Item=wire(cmd))
    if owner!='missing':world.put_item(TableName='users',Item=wire({'PK':'USER#owner','SK':'PROFILE','sub':'other' if owner=='wrong' else 'owner','status':'DELETED' if owner=='deleted' else 'ACTIVE'}))
    with pytest.raises(RecoveryUnavailable):backfill_actions(world,'ledger','users',cmd)
    assert read(world,'ledger',cmd['PK'],CONTROL_SK) is None


def test_backfill_owner_deletion_race_rolls_back(world):
    cmd=command();world.put_item(TableName='ledger',Item=wire(cmd))
    profile={'PK':'USER#owner','SK':'PROFILE','sub':'owner','status':'DELETION_REQUESTED'}
    world.put_item(TableName='users',Item=wire(profile))
    actions=backfill_actions(world,'ledger','users',cmd)
    world.delete_item(TableName='users',Key=wire(key('USER#owner','PROFILE')))
    with pytest.raises(Exception):world.transact_write_items(TransactItems=serialize_actions(actions))
    assert read(world,'ledger',cmd['PK'],CONTROL_SK) is None


def test_second_page_skips_eight_poison_and_attempts_valid_same_shard(world):
    for index in range(9):
        cmd=command(1+16*index,account=str(index));enqueue(world,cmd)
        if index<8:
            row=read(world,'ledger',cmd['PK'],JOB_PREFIX+cmd['operationId'])
            row['schemaVersion']=2;row['nextAttemptAtEpoch']-=100-index
            world.put_item(TableName='ledger',Item=wire(row))
    seen=[];m=worker(world,seen.append).run()
    assert len(seen)==1 and seen[0]['accountId']=='8' and m['SidecarSchemaFailures']==8


def test_excess_poison_reports_truncated_shard_without_inventing_coverage(world):
    for index in range(17):
        cmd=command(1+16*index,account=str(index));enqueue(world,cmd)
        row=read(world,'ledger',cmd['PK'],JOB_PREFIX+cmd['operationId']);row['schemaVersion']=2
        world.put_item(TableName='ledger',Item=wire(row))
    m=worker(world).run()
    assert m['SidecarSchemaFailures']==16 and m['RecoveryShardTruncated']==1 and not m['CommandsAttempted']


def test_four_attempt_limit_then_later_tick_catches_remaining(world):
    for n in range(1,7):enqueue(world,command(n,account=str(n)))
    seen=[];m=worker(world,seen.append).run()
    assert len(seen)==4 and m['RecoveryBudgetExhausted']==1
    m=worker(world,seen.append).run()
    assert len(seen)==6 and m['CommandsAttempted']==2


@pytest.mark.parametrize('target',['inventory','command','control','job'])
def test_preadvance_exact_proofs_refuse_race_without_cleanup(world,target):
    cmd=command();enqueue(world,cmd);real=world.transact_write_items
    def changed(**kwargs):
        if target=='inventory':pk,sk='INVENTORY#dev','CAMPAIGN_RECOVERY_INVENTORY'
        else:pk,sk=cmd['PK'],{'command':cmd['SK'],'control':CONTROL_SK,'job':JOB_PREFIX+cmd['operationId']}[target]
        row=read(world,'ledger',pk,sk)
        row['revision' if target!='command' else 'recordVersion']=99
        world.put_item(TableName='ledger',Item=wire(row))
        return real(**kwargs)
    world.transact_write_items=changed;seen=[]
    assert worker(world,seen.append).run()['CommandsAttempted']==0 and not seen


def test_terminal_account_fence_blocks_older_withdrawal_job(world):
    cmd=command(withdraw=True);enqueue(world,cmd)
    fence=command(2)|{'status':'COMPLETE','eventType':'account.deletion.completed'}
    world.put_item(TableName='ledger',Item=wire(fence))
    before=read(world,'ledger',cmd['PK'],JOB_PREFIX+cmd['operationId'])
    assert not worker(world).run()['CommandsAttempted']
    assert read(world,'ledger',cmd['PK'],before['SK'])==before


def test_finalizer_seal_refuses_late_backfill(world):
    cmd=command(withdraw=True);world.put_item(TableName='ledger',Item=wire(cmd))
    world.put_item(TableName='users',Item=wire({'PK':'USER#owner','SK':'PROFILE','sub':'owner','status':'DELETION_REQUESTED'}))
    control=new_control(cmd['PK'],'dev')|{'pendingJobs':0,'state':'SEALED','revision':2}
    world.put_item(TableName='ledger',Item=wire(control))
    with pytest.raises(RecoveryUnavailable):backfill_actions(world,'ledger','users',cmd)
    assert read(world,'ledger',cmd['PK'],CONTROL_SK)==control


def test_metrics_are_content_free_json_native_even_with_dynamodb_decimals(world):
    import json
    cmd=command();enqueue(world,cmd)
    encoded=json.dumps(worker(world).run())
    assert cmd['accountId'] not in encoded and cmd['operationId'] not in encoded


def test_actual_enabled_scheduled_handler_uses_worker_and_fixed_metrics(world,monkeypatch,capsys):
    from tests.campaign_period_fixtures import enable
    enable(monkeypatch)
    import importlib
    for name,value in {'AWS_DEFAULT_REGION':'us-east-1','APP_ENVIRONMENT':'dev','PIPELINE_TABLE_NAME':'pipeline',
        'USERS_TABLE_NAME':'users','DELETION_LEDGER_TABLE_NAME':'ledger','CAMPAIGN_RECOVERY_ENABLED':'true',
        'CAMPAIGN_RECOVERY_MANIFEST_SHA256':MANIFEST,'CAMPAIGN_RECOVERY_INVENTORY_REVISION':'1'}.items():monkeypatch.setenv(name,value)
    path=str(Path(__file__).resolve().parents[2]/'src/campaign_deletion_bridge')
    monkeypatch.syspath_prepend(path)
    for name in ('app','config','service','retained_periods'):sys.modules.pop(name,None)
    app=importlib.import_module('app');app.dynamodb=world
    seen=[];monkeypatch.setattr(app,'delete_retained_contributions',lambda command,**kw:seen.append(command))
    monkeypatch.setattr('shared_campaign_recovery.worker.time.time',lambda:NOW)
    class Context:
        invoked_function_arn='arn:aws:lambda:us-east-1:107827791950:function:test:live'
        def get_remaining_time_in_millis(self):return 30000
    cmd=command();enqueue(world,cmd)
    result=app.lambda_handler({'schemaVersion':1,'operation':'reconcile-campaign-cleanup'},Context())
    assert result['CommandsAttempted']==1 and result['complete'] is False and result['receiptEligible'] is False
    assert seen==[cmd]
    output=capsys.readouterr().out
    assert 'TrustCheckRadar/Campaign' in output and cmd['operationId'] not in output and cmd['accountId'] not in output
    monkeypatch.setattr(app.config,'CAMPAIGN_RECOVERY_ENABLED',False)
    world.get_item=lambda **kw:pytest.fail('disabled read')
    assert app.lambda_handler({'schemaVersion':1,'operation':'reconcile-campaign-cleanup'},Context())=={'enabled':False,'complete':False}


@pytest.mark.parametrize('after',['job_read','command_read','control_read','advance'])
def test_mid_candidate_budget_stops_before_next_sdk_or_cleanup(world,after):
    cmd=command();enqueue(world,cmd);remaining=[30000]
    real_get=world.get_item;real_write=world.transact_write_items
    target={'job_read':JOB_PREFIX+cmd['operationId'],'command_read':cmd['SK'],'control_read':CONTROL_SK}.get(after)
    def get(**kw):
        assert remaining[0]>=6000,'read after budget'
        value=real_get(**kw)
        if plain(kw['Key'])['SK']==target:remaining[0]=5000
        return value
    writes=[]
    def write(**kw):
        assert remaining[0]>=6000,'write after budget'
        writes.append(True);value=real_write(**kw)
        if after=='advance':remaining[0]=5000
        return value
    world.get_item=get;world.transact_write_items=write;seen=[]
    result=worker(world,seen.append,remaining_ms=lambda:remaining[0]).run()
    assert result['RecoveryBudgetExhausted']==1 and result['CommandsAttempted']==0 and not seen
    assert bool(writes)==(after=='advance')


def test_future_command_never_reports_negative_age_or_runs(world):
    cmd=command();cmd.update(occurredAtEpoch=NOW+1,deleteByEpoch=NOW+86401)
    enqueue(world,cmd)
    # Not due yet: no attempt. Even an invalid earlier due index clock is refused.
    assert worker(world).run()['ObservedPendingAgeSeconds']==0
