"""Fair retained-period cleanup against actual SDK/Moto, synthetic KMS only."""
import os,json
from copy import deepcopy
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('Isolated SDK suite',allow_module_level=True)
from tests.campaign_deletion_bridge.test_coverage_dynamodb import (
    world,CMD,INV,NOW,ARN,ACCOUNT,OP,TOKEN,partition,locator,plain,key,tomb)
from progress import get
from retained_periods import delete_retained_contributions


def run(w,**overrides):
    args=dict(command=CMD,environment='dev',aws_account_id='107827791950',aws_region='us-east-1',table_name='pipeline',
        deletion_ledger_table_name='ledger',retention_days=21,dynamodb=w.d,kms=w.kms,
        locator_manifest_sha256='a'*64,locator_inventory_revision=1,now_epoch=NOW+1,max_steps=5)
    result=delete_retained_contributions(**(args|overrides))
    assert result['complete'] is False and result['receiptEligible'] is False
    assert all(x not in json.dumps(result) for x in (ACCOUNT,OP,TOKEN,ARN))
    return result


def cursor(w):return get(w.d,'pipeline',partition(1500),'TOMBSTONE')['retainedPeriodSweep']


def test_all_retained_periods_progress_and_wrap_without_receipts(world):
    rows=[]
    for period in (1498,1499,1500):
        item=locator(period);world.put(item);rows.append(item)
    for expected in (1499,1500,1498):
        result=run(world);assert result['periodsExpected']==3 and result['periodsAttempted']==1
        assert cursor(world)['nextPeriodId']==expected
    for item in rows:assert get(world.d,'pipeline',item['PK'],item['SK']) is None
    assert get(world.d,'ledger',CMD['PK'],'ACCOUNT_DELETION#CAMPAIGN') is None
    assert all(get(world.d,'pipeline',partition(p),'TOMBSTONE')['deletionDeadlineEpoch']==NOW+21*86400 for p in (1498,1499,1500))


@pytest.mark.parametrize('status',['missing','RETIRED','unknown'])
def test_missing_selected_key_does_not_starve_later_periods(world,status):
    old=get(world.d,'pipeline','PERIOD#1498','HMAC_KEY')
    if status=='missing':world.d.delete_item(TableName='pipeline',Key=key('PERIOD#1498','HMAC_KEY'))
    else:world.put(old|{'status':status})
    blocked=locator(1498);other=locator(1499);world.put(blocked);world.put(other)
    assert run(world)['reason']=='SELECTED_PERIOD_UNVERIFIED'
    assert get(world.d,'pipeline',blocked['PK'],blocked['SK'])==blocked
    assert run(world)['deleted']==1
    assert get(world.d,'pipeline',other['PK'],other['SK']) is None


def test_missing_anchor_has_no_cursor_or_selected_mutation(world):
    world.d.delete_item(TableName='pipeline',Key=key('PERIOD#1500','HMAC_KEY'));before=world.snapshot()
    with pytest.raises(Exception):run(world)
    assert world.snapshot()==before


def test_lost_advance_ack_skips_attempt_but_recovers_on_next_wrap(world):
    item=locator(1498);world.put(item)
    class Lost:
        def __getattr__(self,name):return getattr(world.d,name)
        def transact_write_items(self,**kwargs):
            world.d.transact_write_items(**kwargs);raise RuntimeError('synthetic lost ack')
    with pytest.raises(RuntimeError):run(world,dynamodb=Lost())
    assert cursor(world)['nextPeriodId']==1499
    assert get(world.d,'pipeline',item['PK'],item['SK']) is not None
    run(world);run(world);assert run(world)['deleted']==1


def test_anchor_cursor_preserves_existing_locator_progress(world):
    before=get(world.d,'pipeline',partition(1500),'TOMBSTONE')
    run(world,max_steps=1)
    after=get(world.d,'pipeline',partition(1500),'TOMBSTONE')
    for name in ('locatorCleanupRevision','locatorCleanupState','createdAtEpoch','deletionDeadlineEpoch','GSI3SK'):
        assert after[name]==before[name]


@pytest.mark.parametrize('change',[{'schemaVersion':2},{'nextPeriodId':1497},{'inventoryRevision':2},{'minimumPeriodId':1499},{'unknown':'never retain'}])
def test_unknown_or_mismatched_cursor_is_preserved(world,change):
    run(world)
    item=get(world.d,'pipeline',partition(1500),'TOMBSTONE')
    item['retainedPeriodSweep'].update(change);world.put(item);before=world.snapshot()
    with pytest.raises(Exception):run(world)
    assert world.snapshot()==before


def test_overlapping_owned_withdrawal_and_account_commands_preserve_fair_cursor(world):
    other_id='47debb73-444b-4bb1-9889-fb56885b7922'
    withdrawal=CMD|{'SK':'CAMPAIGN_WITHDRAWAL#'+other_id,'eventType':'campaign.consent.withdrawn',
                   'status':'PENDING','operationId':other_id,'consentEpochId':other_id}
    world.put(withdrawal,'ledger')
    run(world,command=withdrawal,max_steps=1)
    before=get(world.d,'pipeline',partition(1500),'TOMBSTONE')
    assert cursor(world)['nextPeriodId']==1499
    run(world,max_steps=1)
    assert cursor(world)['nextPeriodId']==1500 and cursor(world)['operationId']==OP
    after=get(world.d,'pipeline',partition(1500),'TOMBSTONE')
    assert after['locatorCleanupState']==before['locatorCleanupState']
    run(world,command=withdrawal,max_steps=1)
    assert cursor(world)['nextPeriodId']==1498 and cursor(world)['operationId']==other_id
    assert get(world.d,'ledger',CMD['PK'],CMD['SK'])==CMD
    assert get(world.d,'ledger',withdrawal['PK'],withdrawal['SK'])==withdrawal
    assert get(world.d,'pipeline',partition(1500),'TOMBSTONE')['deletionDeadlineEpoch']==before['deletionDeadlineEpoch']


def test_cursor_cannot_be_adopted_by_wrong_owned_command(world):
    run(world);before=world.snapshot()
    with pytest.raises(Exception):run(world,command=CMD|{'accountId':'different-account'})
    assert world.snapshot()==before
    # A different subject uses a different HMAC-derived partition, never this cursor.
    from types import SimpleNamespace
    other=CMD|{'accountId':'different-account','PK':'ACCOUNT#different-account'}
    world.put(other,'ledger')
    prior=get(world.d,'pipeline',partition(1500),'TOMBSTONE')
    kms=SimpleNamespace(generate_mac=lambda **_:{'Mac':b'y'*32,'KeyId':ARN,'MacAlgorithm':'HMAC_SHA_256'})
    run(world,command=other,kms=kms,max_steps=1)
    assert get(world.d,'pipeline',partition(1500),'TOMBSTONE')==prior


@pytest.mark.parametrize('changed',['command','inventory','anchor'])
def test_commit_time_anchor_fence_change_prevents_cursor_advance(world,changed):
    class Race:
        def __getattr__(self,name):return getattr(world.d,name)
        def transact_write_items(self,**kwargs):
            if changed=='command':world.put(CMD|{'status':'COMPLETE'},'ledger')
            elif changed=='inventory':world.put(INV|{'revision':2})
            else:world.put(get(world.d,'pipeline','PERIOD#1500','HMAC_KEY')|{'status':'RETIRED'})
            return world.d.transact_write_items(**kwargs)
    with pytest.raises(world.d.exceptions.TransactionCanceledException):run(world,dynamodb=Race())
    assert 'retainedPeriodSweep' not in get(world.d,'pipeline',partition(1500),'TOMBSTONE')


def test_key_retirement_after_derivation_cancels_selected_deletion(world):
    item=locator(1498);world.put(item)
    class Race:
        def __getattr__(self,name):return getattr(world.d,name)
        def transact_write_items(self,**kwargs):
            if any('Delete' in a for a in kwargs['TransactItems']):
                world.put(get(world.d,'pipeline','PERIOD#1498','HMAC_KEY')|{'status':'RETIRED'})
            return world.d.transact_write_items(**kwargs)
    result=run(world,dynamodb=Race())
    assert result['reason']=='SELECTED_PERIOD_UNVERIFIED'
    assert get(world.d,'pipeline',item['PK'],item['SK']) is not None
    assert cursor(world)['nextPeriodId']==1499


def test_budget_and_range_bounds_do_not_create_cursor(world):
    before=world.snapshot()
    for args in ({'remaining_ms':lambda:0},{'max_periods':2},{'max_periods':True}):
        with pytest.raises(Exception):run(world,**args)
        assert world.snapshot()==before


def test_terminal_replay_does_not_create_or_advance_cursor(world):
    world.put(CMD|{'status':'COMPLETE','eventType':'account.deletion.completed','completedAtEpoch':NOW+1,'retainUntilEpoch':NOW+1+120*86400},'ledger')
    before=world.snapshot();assert run(world)['alreadyCompleted'];assert world.snapshot()==before


def test_busy_first_period_does_not_starve_later_period_with_one_step_budget(world):
    for _ in range(12):world.put(locator(1498))
    later=locator(1499);world.put(later)
    for _ in range(9):run(world,max_steps=1)
    assert get(world.d,'pipeline',later['PK'],later['SK']) is None
    remaining=world.d.query(TableName='pipeline',KeyConditionExpression='PK = :pk',
        ExpressionAttributeValues={':pk':{'S':partition(1498)}})['Items']
    assert len(remaining)>1


def test_poison_locator_cannot_starve_next_period(world):
    poison=locator(1498)|{'schemaVersion':99};world.put(poison)
    later=locator(1499);world.put(later)
    assert run(world)['reason']=='SELECTED_PERIOD_UNVERIFIED'
    assert run(world)['deleted']==1
    assert get(world.d,'pipeline',poison['PK'],poison['SK'])==poison


def test_existing_tombstone_changed_before_advance_rejects_cursor(world):
    class Race:
        def __getattr__(self,name):return getattr(world.d,name)
        def transact_write_items(self,**kwargs):
            item=get(world.d,'pipeline',partition(1500),'TOMBSTONE')
            world.put(item|{'locatorCleanupRevision':2})
            return world.d.transact_write_items(**kwargs)
    with pytest.raises(world.d.exceptions.TransactionCanceledException):run(world,dynamodb=Race())
    assert 'retainedPeriodSweep' not in get(world.d,'pipeline',partition(1500),'TOMBSTONE')


def test_handler_uses_runtime_identity_and_still_rejects_incomplete_stream_work(world,monkeypatch):
    import importlib.util,sys
    from pathlib import Path
    from types import SimpleNamespace
    from progress import wire
    monkeypatch.setenv('AWS_DEFAULT_REGION','us-east-1')
    directory=Path(__file__).resolve().parents[2]/'src/campaign_deletion_bridge'
    spec=importlib.util.spec_from_file_location('retained_handler',directory/'app.py')
    app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
    app.config=SimpleNamespace(validate_config=lambda:None,APP_ENVIRONMENT='dev',CAMPAIGN_SCHEMA_VERSION=1,
        PIPELINE_TABLE_NAME='pipeline',TRANSIENT_RETENTION_DAYS=21,DELETION_LEDGER_TABLE_NAME='ledger',
        CAMPAIGN_LOCATOR_MANIFEST_SHA256='a'*64,CAMPAIGN_LOCATOR_INVENTORY_REVISION=1)
    app.dynamodb=world.d;app.kms=world.kms
    context=SimpleNamespace(invoked_function_arn='arn:aws:lambda:us-east-1:107827791950:function:synthetic:live',
                            get_remaining_time_in_millis=lambda:30000)
    # Real current time isn't this synthetic command's clock.
    import retained_periods
    monkeypatch.setattr(retained_periods.time,'time',lambda:NOW+1)
    event={'Records':[{'eventName':'INSERT','dynamodb':{'NewImage':wire(CMD),'SequenceNumber':'123'}}]}
    with pytest.raises(RuntimeError,match='requires reconciliation'):app.lambda_handler(event,context)
    assert cursor(world)['nextPeriodId']==1499
    assert app.SDK_CONFIG.retries['total_max_attempts']==1 and app.SDK_CONFIG.read_timeout==3


def test_new_tombstone_cursor_uses_original_request_deadline(world):
    world.d.delete_item(TableName='pipeline',Key=key(partition(1500),'TOMBSTONE'))
    run(world,now_epoch=NOW+86400)
    anchor=get(world.d,'pipeline',partition(1500),'TOMBSTONE')
    assert anchor['createdAtEpoch']==NOW and anchor['deletionDeadlineEpoch']==NOW+21*86400
    assert 'expiresAt' not in anchor
