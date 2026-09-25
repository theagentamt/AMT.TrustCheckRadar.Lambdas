"""Unwired completion with real SDK transactions and explicit synthetic qualification."""
import os,json
from copy import deepcopy
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('Isolated SDK only',allow_module_level=True)
from tests.campaign_deletion_bridge.test_coverage_dynamodb import (
    world,CMD,INV,NOW,ARN,ACCOUNT,OP,TOKEN,partition,locator,plain,key,tomb)
from completion import Completion,CompletionUnavailable,INVARIANTS,stamp
from shared_campaign_recovery import records as R

@pytest.fixture
def qualified(world):
    w=world
    w.d.create_table(TableName='users',BillingMode='PAY_PER_REQUEST',
        KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],
        AttributeDefinitions=[{'AttributeName':n,'AttributeType':'S'} for n in ('PK','SK')])
    recovery={'PK':'INVENTORY#dev','SK':'CAMPAIGN_RECOVERY_INVENTORY','recordType':'CAMPAIGN_RECOVERY_INVENTORY',
        'schemaVersion':1,'environment':'dev','revision':1,'coverage':'VERIFIED_COMPLETE','manifestSha256':'b'*64,
        'approvedAtEpoch':NOW-20,'legacyCoverageVerified':True,'writers':R.WRITERS}
    marker={'PK':'INVENTORY#dev','SK':'CAMPAIGN_COMPLETION_INVENTORY','recordType':'CAMPAIGN_COMPLETION_INVENTORY',
        'schemaVersion':1,'environment':'dev','revision':1,'coverage':'VERIFIED_COMPLETE','manifestSha256':'c'*64,
        'approvedAtEpoch':NOW-10,'locatorManifestSha256':'a'*64,'locatorInventoryRevision':1,
        'recoveryManifestSha256':'b'*64,'recoveryInventoryRevision':1,'invariants':INVARIANTS}
    w.put(recovery,'ledger');w.put(marker,'ledger');w.put(R.new_job(CMD),'ledger');w.put(R.new_control(CMD['PK'],'dev'),'ledger')
    w.put({'PK':'USER#'+ACCOUNT,'SK':'PROFILE','sub':ACCOUNT,'status':'DELETION_REQUESTED','deletionOperationId':OP},'users')
    w.marker=marker
    return w

def get(w,table,pk,sk):return R.read(w.d,table,pk,sk)

def candidate(w,**overrides):
    args=dict(dynamodb=w.d,kms=w.kms,pipeline_table='pipeline',ledger_table='ledger',users_table='users',environment='dev',
        aws_account_id='107827791950',aws_region='us-east-1',manifest_sha256='c'*64,inventory_revision=1,
        locator_manifest_sha256='a'*64,locator_inventory_revision=1,recovery_manifest_sha256='b'*64,
        recovery_inventory_revision=1,now=lambda:NOW+1)
    return Completion(**(args|overrides))

def result(w,cmd=CMD,**kw):
    value=candidate(w,**kw).complete(cmd)
    assert value['campaignComplete'] and value['accountComplete'] is False
    assert all(s not in json.dumps(value) for s in (ACCOUNT,OP,TOKEN,ARN))
    return value

def test_account_completion_consumes_last_job_seals_and_receipts_atomically(qualified):
    w=qualified;before=w.snapshot();calls=[];real=w.d.transact_write_items
    def capture(**kw):calls.append(kw);return real(**kw)
    w.d.transact_write_items=capture
    assert result(w)['alreadyComplete'] is False
    assert len(calls)==1
    assert get(w,'ledger',CMD['PK'],R.JOB_PREFIX+OP) is None
    assert get(w,'ledger',CMD['PK'],R.CONTROL_SK)['state']=='SEALED'
    receipt=get(w,'ledger',CMD['PK'],'ACCOUNT_DELETION#CAMPAIGN')
    assert receipt['retainUntilEpoch']==NOW+1+120*86400
    assert get(w,'ledger',CMD['PK'],CMD['SK'])==CMD
    assert w.snapshot()['pipeline']==before['pipeline']
    assert result(w)['alreadyComplete'] is True and len(calls)==1
    assert get(w,'ledger',CMD['PK'],'ACCOUNT_DELETION#CAMPAIGN')==receipt

@pytest.mark.parametrize('bad',['missing','old_locator_only','late_approval','future','unknown','wrong_link','missing_invariant'])
def test_separate_completion_qualification_is_required_without_mutation(qualified,bad):
    w=qualified
    if bad in ('missing','old_locator_only'):w.d.delete_item(TableName='ledger',Key=key(w.marker['PK'],w.marker['SK']))
    else:
        changes={'late_approval':{'approvedAtEpoch':NOW},'future':{'approvedAtEpoch':NOW+100},
                 'unknown':{'extra':True},'wrong_link':{'locatorManifestSha256':'d'*64},'missing_invariant':{'invariants':INVARIANTS[:-1]}}
        w.put(w.marker|changes[bad],'ledger')
    before=w.snapshot()
    with pytest.raises(CompletionUnavailable):result(w)
    assert not w.calls and w.snapshot()==before

@pytest.mark.parametrize('kind',['locator','unknown','pending_repair','other_operation','retired_key','missing_key','missing_tomb'])
def test_no_receipt_from_unfenced_or_incomplete_period(qualified,kind):
    w=qualified
    if kind=='locator':w.put(locator(1498))
    elif kind=='unknown':w.put({'PK':partition(1498),'SK':'UNKNOWN','body':'synthetic-private'})
    elif kind in ('pending_repair','other_operation'):
        row=tomb(1498);row['locatorCleanupState']['cursor']='LOCATOR#EVENT#'+OP if kind=='pending_repair' else None
        if kind=='other_operation':row['locatorCleanupState']['operationId']='00000000-0000-4000-8000-000000000001'
        w.put(row)
    elif kind=='retired_key':
        row=get(w,'pipeline','PERIOD#1498','HMAC_KEY');w.put(row|{'status':'RETIRED'})
    else:w.d.delete_item(TableName='pipeline',Key=key('PERIOD#1498' if kind=='missing_key' else partition(1498),'HMAC_KEY' if kind=='missing_key' else 'TOMBSTONE'))
    before=w.snapshot()
    with pytest.raises(CompletionUnavailable):result(w)
    assert w.snapshot()==before

@pytest.mark.parametrize('target',['tomb','key','locator_inventory','recovery_inventory','completion_inventory','job','control','profile','command'])
def test_exact_transaction_proofs_refuse_concurrent_change(qualified,target):
    w=qualified;real=w.d.transact_write_items
    locations={'tomb':('pipeline',partition(1498),'TOMBSTONE'), 'key':('pipeline','PERIOD#1498','HMAC_KEY'),
        'locator_inventory':('pipeline','INVENTORY#dev','CAMPAIGN_LOCATORS'),
        'recovery_inventory':('ledger','INVENTORY#dev','CAMPAIGN_RECOVERY_INVENTORY'),
        'completion_inventory':('ledger','INVENTORY#dev','CAMPAIGN_COMPLETION_INVENTORY'),
        'job':('ledger',CMD['PK'],R.JOB_PREFIX+OP),'control':('ledger',CMD['PK'],R.CONTROL_SK),
        'profile':('users','USER#'+ACCOUNT,'PROFILE'),'command':('ledger',CMD['PK'],CMD['SK'])}
    def race(**kw):
        table,pk,sk=locations[target];row=get(w,table,pk,sk)
        field={'tomb':'locatorCleanupRevision','key':'status','profile':'status','command':'recordVersion'}.get(target,'revision')
        row[field]='CHANGED' if target in ('key','profile') else 99;w.put(row,table)
        return real(**kw)
    w.d.transact_write_items=race
    with pytest.raises(CompletionUnavailable):result(w)
    assert get(w,'ledger',CMD['PK'],R.JOB_PREFIX+OP) is not None
    assert get(w,'ledger',CMD['PK'],'ACCOUNT_DELETION#CAMPAIGN') is None


def test_qualified_producer_cannot_insert_locator_during_absence_proof(qualified):
    w=qualified;real=w.d.transact_write_items;attempted=[]
    def race(**kw):
        actions=[{'ConditionCheck':{'TableName':'pipeline','Key':key(partition(1498),'TOMBSTONE'),'ConditionExpression':'attribute_not_exists(PK)'}},
                 {'Put':{'TableName':'pipeline','Item':R.wire(locator(1498))}}]
        with pytest.raises(Exception):real(TransactItems=actions)
        attempted.append(True);return real(**kw)
    w.d.transact_write_items=race
    assert result(w)['campaignComplete'] and attempted==[True]


def test_lost_ack_recovers_exact_receipt_without_new_retention(qualified):
    w=qualified;real=w.d.transact_write_items
    def lost(**kw):real(**kw);raise RuntimeError('private lost acknowledgment')
    w.d.transact_write_items=lost
    assert result(w)['alreadyComplete']
    receipt=get(w,'ledger',CMD['PK'],'ACCOUNT_DELETION#CAMPAIGN')
    assert result(w,now=lambda:NOW+100)['alreadyComplete']
    assert get(w,'ledger',CMD['PK'],'ACCOUNT_DELETION#CAMPAIGN')==receipt


def withdrawal(w,op=OP,with_account=False):
    cmd=CMD|{'SK':'CAMPAIGN_WITHDRAWAL#'+op,'operationId':op,'consentEpochId':op,'eventType':'campaign.consent.withdrawn','status':'PENDING'}
    if not with_account:w.d.delete_item(TableName='ledger',Key=key(CMD['PK'],CMD['SK']))
    w.put(cmd,'ledger');w.put(R.new_job(cmd),'ledger')
    w.put({'PK':CMD['PK'],'SK':R.CONTROL_SK,'recordType':'CAMPAIGN_RECOVERY_CONTROL','schemaVersion':1,'environment':'dev',
           'revision':2,'pendingJobs':2 if with_account else 1,'state':'OPEN'},'ledger')
    if not with_account:w.put({'PK':'USER#'+ACCOUNT,'SK':'PROFILE','sub':ACCOUNT,'status':'ACTIVE'},'users')
    state={'PK':'USER#'+ACCOUNT,'SK':'CAMPAIGN_PARTICIPATION','schemaVersion':1,'recordVersion':1,'environment':'dev',
        'state':'withdrawal_pending','stateVersion':2,'noticeVersion':'research-consent-2026-09-21-v2','policyVersion':'independent-research-v1',
        'consentEpochId':op,'lastOperationId':op,'effectiveFrom':stamp(NOW-100),'updatedAt':stamp(NOW),
        'effectiveUntil':stamp(NOW),'withdrawalRequestedAt':stamp(NOW),'deletionDeadlineAt':stamp(NOW+86400)}
    w.put(state,'users')
    for p in range(1498,1501):
        row=tomb(p);row['locatorCleanupState']['operationId']=op;w.put(row)
    return cmd,state


def test_withdrawal_removes_zero_control_updates_exact_epoch_and_keeps_original_audit(qualified):
    w=qualified;cmd,state=withdrawal(w)
    original={'PK':state['PK'],'SK':f'CAMPAIGN_CONSENT#{OP}#{NOW}#{OP}','expiresAt':NOW+400*86400,'syntheticOriginal':True}
    w.put(original,'users')
    assert not result(w,cmd,now=lambda:NOW)['alreadyComplete']
    assert get(w,'ledger',CMD['PK'],R.CONTROL_SK) is None
    assert get(w,'users',state['PK'],state['SK'])['state']=='withdrawn'
    assert get(w,'users',original['PK'],original['SK'])==original
    audit=get(w,'users',state['PK'],f'CAMPAIGN_CONSENT#{OP}#{NOW}#{OP}#COMPLETED')
    assert audit['expiresAt']==NOW+400*86400
    new_state=state|{'state':'enrolled','stateVersion':4,'consentEpochId':'00000000-0000-4000-8000-000000000001'}
    w.put(new_state,'users')
    assert result(w,cmd,now=lambda:NOW+100)['alreadyComplete']
    assert get(w,'users',state['PK'],state['SK'])==new_state
    assert get(w,'users',audit['PK'],audit['SK'])==audit


def test_older_withdrawal_prevents_account_seal_until_atomic_completion(qualified):
    w=qualified;other='00000000-0000-4000-8000-000000000002';cmd,state=withdrawal(w,other,True)
    # Bind proof to account to show the counter itself prevents premature seal.
    for p in range(1498,1501):w.put(tomb(p))
    with pytest.raises(CompletionUnavailable):result(w)
    assert get(w,'ledger',CMD['PK'],'ACCOUNT_DELETION#CAMPAIGN') is None
    for p in range(1498,1501):
        row=tomb(p);row['locatorCleanupState']['operationId']=other;w.put(row)
    result(w,cmd)
    assert get(w,'ledger',CMD['PK'],R.CONTROL_SK)['pendingJobs']==1
    for p in range(1498,1501):w.put(tomb(p))
    assert result(w)['campaignComplete']

@pytest.mark.parametrize('race',[False,True])
def test_newer_epoch_is_never_overwritten(qualified,race):
    w=qualified;cmd,state=withdrawal(w);new=state|{'consentEpochId':'00000000-0000-4000-8000-000000000003','stateVersion':3}
    if race:
        real=w.d.transact_write_items
        def change(**kw):w.put(new,'users');return real(**kw)
        w.d.transact_write_items=change
    else:w.put(new,'users')
    with pytest.raises(CompletionUnavailable):result(w,cmd)
    assert get(w,'users',state['PK'],state['SK'])==new
    assert get(w,'ledger',cmd['PK'],cmd['SK'])==cmd
    assert get(w,'ledger',cmd['PK'],R.JOB_PREFIX+OP) is not None


def test_budget_after_query_stops_before_next_sdk_or_write(qualified):
    w=qualified;remaining=[30000];real=w.d.query;realget=w.d.get_item
    def query(**kw):out=real(**kw);remaining[0]=5000;return out
    def getitem(**kw):assert remaining[0]>=6000;return realget(**kw)
    w.d.query=query;w.d.get_item=getitem
    w.d.transact_write_items=lambda **kw:pytest.fail('late write')
    with pytest.raises(CompletionUnavailable):result(w,remaining_ms=lambda:remaining[0])


def test_period_budget_is_checked_before_mac(qualified):
    w=qualified;w.put(INV|{'minimumPeriodId':1460})
    with pytest.raises(CompletionUnavailable):result(w,max_periods=32)
    assert not w.calls


def test_expired_component_receipt_is_not_reissued(qualified):
    w=qualified;result(w);before=w.snapshot()
    with pytest.raises(CompletionUnavailable):result(w,now=lambda:NOW+1+120*86400)
    assert w.snapshot()==before


def test_completion_audit_matches_existing_deletion_and_export_contracts(qualified,monkeypatch):
    import sys
    from pathlib import Path
    w=qualified;cmd,state=withdrawal(w);result(w,cmd)
    audit=get(w,'users',state['PK'],f'CAMPAIGN_CONSENT#{OP}#{NOW+1}#{OP}#COMPLETED')
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'src/account_data_api'))
    from account_data_api.service import _validate_consent_audit,CONSENT_COMPLETION_FIELDS
    from account_export_api.projection import pick
    assert set(audit)==CONSENT_COMPLETION_FIELDS
    _validate_consent_audit(audit,state['PK'])
    projected=pick(audit,'consent')
    assert projected=={'eventType':'campaign.participation.withdrawal_completed','occurredAt':stamp(NOW+1),'resultingState':'withdrawn'}
    assert OP not in json.dumps(projected) and ACCOUNT not in json.dumps(projected)


def test_actual_concurrent_completion_reconciles_winner_without_second_receipt(qualified):
    w=qualified;outer=candidate(w);other=candidate(w);real=w.d.transact_write_items;first=[True]
    def concurrently(**kw):
        if first[0]:
            first[0]=False;assert other.complete(CMD)['alreadyComplete'] is False
        return real(**kw)
    w.d.transact_write_items=concurrently
    assert outer.complete(CMD)['alreadyComplete'] is True
    assert get(w,'ledger',CMD['PK'],R.CONTROL_SK)['revision']==2


def test_withdrawal_lost_ack_does_not_repeat_audit_or_counter_decrement(qualified):
    w=qualified;cmd,state=withdrawal(w);real=w.d.transact_write_items
    def lost(**kw):real(**kw);raise RuntimeError('lost')
    w.d.transact_write_items=lost
    assert result(w,cmd)['alreadyComplete']
    rows=w.d.query(TableName='users',KeyConditionExpression='PK = :pk AND begins_with(SK, :prefix)',ExpressionAttributeValues=R.wire({':pk':state['PK'],':prefix':'CAMPAIGN_CONSENT#'}))['Items']
    assert len(rows)==1
    assert result(w,cmd,now=lambda:NOW+100)['alreadyComplete']
    assert get(w,'ledger',CMD['PK'],R.CONTROL_SK) is None


def test_full_account_terminal_replay_never_recreates_retired_receipts(qualified):
    w=qualified;terminal=CMD|{'eventType':'account.deletion.completed','status':'COMPLETE','completedAtEpoch':NOW+1,'retainUntilEpoch':NOW+1+120*86400}
    w.put(terminal,'ledger');before=w.snapshot()
    value=candidate(w,now=lambda:NOW+200*86400).complete(CMD)
    assert value['campaignComplete'] is False and value['terminalAcknowledged'] is True
    assert w.snapshot()==before and not w.calls


def test_contradictory_last_job_count_cannot_hide_older_sidecar(qualified):
    w=qualified
    old=CMD|{'SK':'CAMPAIGN_WITHDRAWAL#00000000-0000-4000-8000-000000000002','operationId':'00000000-0000-4000-8000-000000000002',
        'consentEpochId':'00000000-0000-4000-8000-000000000002','eventType':'campaign.consent.withdrawn','status':'PENDING'}
    w.put(R.new_job(old),'ledger')
    with pytest.raises(CompletionUnavailable):result(w)
    assert get(w,'ledger',CMD['PK'],R.CONTROL_SK)['state']=='OPEN'
    assert get(w,'ledger',CMD['PK'],'ACCOUNT_DELETION#CAMPAIGN') is None


@pytest.mark.parametrize('bad',['no_marker','surviving_job','no_audit','expired_audit'])
def test_legacy_withdrawal_terminal_does_not_manufacture_completion(qualified,bad):
    w=qualified;cmd,state=withdrawal(w)
    if bad in ('no_marker','surviving_job','expired_audit'):
        result(w,cmd)
        if bad=='no_marker':w.d.delete_item(TableName='ledger',Key=key(w.marker['PK'],w.marker['SK']))
        elif bad=='surviving_job':w.put(R.new_job(cmd),'ledger')
    else:
        w.put(cmd|{'status':'COMPLETE','completedAtEpoch':NOW},'ledger')
        w.d.delete_item(TableName='ledger',Key=key(cmd['PK'],R.JOB_PREFIX+cmd['operationId']))
        w.d.delete_item(TableName='ledger',Key=key(cmd['PK'],R.CONTROL_SK))
    before=w.snapshot()
    with pytest.raises(CompletionUnavailable):candidate(w,now=lambda:NOW+401*86400 if bad=='expired_audit' else NOW+1).complete(cmd)
    assert w.snapshot()==before


def test_completed_withdrawal_replay_permits_unrelated_remaining_jobs(qualified):
    w=qualified;cmd,state=withdrawal(w,'00000000-0000-4000-8000-000000000002',True)
    result(w,cmd)
    assert get(w,'ledger',CMD['PK'],R.CONTROL_SK)['pendingJobs']==1
    assert result(w,cmd)['alreadyComplete']
    assert get(w,'ledger',CMD['PK'],R.JOB_PREFIX+OP) is not None


def test_account_component_replay_requires_current_completion_qualification(qualified):
    w=qualified;result(w)
    w.d.delete_item(TableName='ledger',Key=key(w.marker['PK'],w.marker['SK']))
    before=w.snapshot()
    with pytest.raises(CompletionUnavailable):candidate(w).complete(CMD)
    assert w.snapshot()==before


def test_exact_legacy_full_account_terminal_is_only_acknowledgement(qualified):
    w=qualified
    w.put(CMD|{'status':'COMPLETE','eventType':'account.deletion.completed','completedAtEpoch':NOW,'retainUntilEpoch':NOW+120*86400},'ledger')
    w.d.delete_item(TableName='ledger',Key=key(w.marker['PK'],w.marker['SK']))
    before=w.snapshot();value=candidate(w).complete(CMD)
    assert value['campaignComplete'] is False and value['terminalAcknowledged'] is True
    assert w.snapshot()==before


def test_real_campaign_seal_then_profile_cleanup_then_identity_consumes_control(qualified,monkeypatch):
    from pathlib import Path
    import boto3
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'src/account_data_api'))
    from account_data_api.service import delete_user_profile_state
    from shared_account_finalization.service import Finalizer,REQUIRED_COMPONENTS
    w=qualified;resource=boto3.resource('dynamodb',region_name='us-east-1')
    ledger=resource.Table('ledger');users=resource.Table('users')
    # Other components are explicit synthetic preconditions, not claims of their erasure.
    for component in REQUIRED_COMPONENTS:
        if component in ('CAMPAIGN','USER_PROFILE','IDENTITY'):continue
        w.put({'PK':CMD['PK'],'SK':'ACCOUNT_DELETION#'+component,'schemaVersion':1,'recordVersion':1,
            'environment':'dev','eventType':'account.deletion.component.completed','component':component,'status':'COMPLETE',
            'operationId':OP,'occurredAtEpoch':NOW+1,'requestOccurredAtEpoch':NOW,'retainUntilEpoch':NOW+1+120*86400},'ledger')
    w.put({'PK':'INVENTORY#dev','SK':'ACCOUNT_DATA_INVENTORY','recordType':'ACCOUNT_DATA_INVENTORY',
        'schemaVersion':1,'revision':1,'environment':'dev','coverage':'VERIFIED_COMPLETE','manifestSha256':'d'*64,
        'requiredComponents':list(REQUIRED_COMPONENTS),'usernameIsSubVerified':True,'approvedAtEpoch':NOW-10},'ledger')
    profile=get(w,'users','USER#'+ACCOUNT,'PROFILE');w.put(profile|{'deletionRequestedAtEpoch':NOW},'users')
    assert delete_user_profile_state(CMD,users_table=users,ledger_table=ledger,policy_status='approved',now_epoch=NOW+1)['complete'] is False
    result(w)
    assert delete_user_profile_state(CMD,users_table=users,ledger_table=ledger,policy_status='approved',now_epoch=NOW+1)['complete'] is True
    assert get(w,'users',profile['PK'],profile['SK']) is None
    class Identity:
        calls=0
        def admin_get_user(self,**kw):return {'Username':ACCOUNT,'UserAttributes':[{'Name':'sub','Value':ACCOUNT}]}
        def admin_delete_user(self,**kw):self.calls+=1
    identity=Identity()
    f=Finalizer(ledger_table=ledger,ledger_table_name='ledger',client=w.d,cognito=identity,user_pool_id='us-east-1_synthetic',
        environment='dev',now=lambda:NOW+1,enabled=True,manifest_sha256='d'*64,inventory_revision=1)
    assert f.finalize(CMD)['complete'] and identity.calls==1
    assert get(w,'ledger',CMD['PK'],R.CONTROL_SK) is None
    assert candidate(w).complete(CMD)['campaignComplete'] is False
