"""DynamoDB worker/runtime integration; no live AWS."""
import os
import sys
from pathlib import Path
from copy import deepcopy
import base64
import json
import pytest

if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':
    pytest.skip('Isolated Moto environment required',allow_module_level=True)
sys.path.insert(0,str(Path(__file__).parent))
from test_transactions import world,ACCOUNT,PAYLOAD,admit,failure
from test_deletion import setup,receipt,items
from shared_check_authority.inventory import INVENTORY_KEY,load_keyring,verified_inventory
from shared_check_authority.core import AuthorityError
from v1_authority_deletion.service import DeletionWorker,CURSOR_KEY
from v1_authority_deletion import app

STREAM='arn:aws:dynamodb:us-east-1:107827791950:table/deletion/stream/2026-09-20T00:00:00.000'
SECRET='arn:aws:secretsmanager:us-east-1:107827791950:secret:trustcheckradar/dev/v1-authority-hmac-ABC123'


def event(cmd,sequence='100'):
    from boto3.dynamodb.types import TypeSerializer
    ser=TypeSerializer()
    return {'Records':[{'eventSource':'aws:dynamodb','eventSourceARN':STREAM,'eventName':'INSERT','dynamodb':{'SequenceNumber':sequence,'NewImage':{k:ser.serialize(v) for k,v in cmd.items()}}}]}


def test_stream_finishes_component_with_no_identifying_response(world):
    a,*_=world
    bridge,cmd,_=setup(world)
    response,counts=DeletionWorker(bridge,stream_arn=STREAM,allowed_subjects=frozenset({ACCOUNT,'aaa-poison'}),remaining_ms=lambda:30000).stream(event(cmd))
    assert response=={'batchItemFailures':[]}
    assert counts=={'examined':1,'completed':1,'pending':0,'failed':0,'deleted':2,'overdue':0,'skipped':0}
    assert receipt(a) and ACCOUNT not in str(counts)


def test_stream_partial_has_retry_and_schedule_completes(world):
    a,*_=world
    bridge,cmd,_=setup(world)
    pk=bridge._partition(ACCOUNT,'k1')
    for n in range(50):a.ddb.Table('authority').put_item(Item={'PK':pk,'SK':f'FUTURE#{n:03d}'})
    worker=DeletionWorker(bridge,stream_arn=STREAM,allowed_subjects=frozenset({ACCOUNT,'aaa-poison'}),remaining_ms=lambda:30000)
    response,counts=worker.stream(event(cmd))
    assert response=={'batchItemFailures':[{'itemIdentifier':'100'}]}
    assert counts['pending']==1 and receipt(a) is None
    for _ in range(5):worker.reconcile()
    assert receipt(a) and not items(a,pk)


@pytest.mark.parametrize('mutation',[
    lambda e:e['Records'][0].update(eventSourceARN=STREAM+'other'),
    lambda e:e['Records'][0].update(eventSource='untrusted'),
    lambda e:e['Records'][0].update(eventName='REMOVE'),
    lambda e:e['Records'][0]['dynamodb'].update(NewImage={}),
])
def test_bad_stream_source_or_payload_never_deletes(world,mutation):
    a,*_=world
    bridge,cmd,_=setup(world)
    incoming=event(cmd);mutation(incoming)
    result,counts=DeletionWorker(bridge,stream_arn=STREAM,allowed_subjects=frozenset({ACCOUNT,'aaa-poison'}),remaining_ms=lambda:30000).stream(incoming)
    # Empty unrelated image is ignored; all invalid source variants fail.
    assert receipt(a) is None and len(items(a,bridge._partition(ACCOUNT,'k1')))==2
    if incoming['Records'][0]['dynamodb']['NewImage']:
        assert result['batchItemFailures'] and counts['failed']==1


def test_schedule_advances_after_poison_command(world):
    a,*_=world
    bridge,cmd,_=setup(world)
    poison=cmd|{'PK':'ACCOUNT#aaa-poison','accountId':'aaa-poison','operationId':'invalid'}
    a.ddb.Table('deletion').put_item(Item=poison)
    counts=DeletionWorker(bridge,stream_arn=STREAM,allowed_subjects=frozenset({ACCOUNT,'aaa-poison'}),remaining_ms=lambda:30000).reconcile()
    assert counts['failed']==1 and counts['completed']==1 and receipt(a)
    assert a._get('deletion',CURSOR_KEY)['revision']==1


def test_cursor_cas_conflict_never_claims_success(world,monkeypatch):
    a,*_=world
    bridge,cmd,_=setup(world)
    original=bridge._transact
    def racing(ops):
        if any(x.get('Update',{}).get('Key')==CURSOR_KEY for x in ops):
            a.ddb.Table('deletion').put_item(Item=CURSOR_KEY|{'revision':5,'cursor':None})
        return original(ops)
    monkeypatch.setattr(bridge,'_transact',racing)
    failure('DELETION_TRANSACTION_UNCERTAIN',lambda:DeletionWorker(bridge,stream_arn=STREAM,allowed_subjects=frozenset({ACCOUNT,'aaa-poison'}),remaining_ms=lambda:30000).reconcile())
    assert a._get('deletion',CURSOR_KEY)['revision']==5


def test_low_time_stream_marks_retry_without_deletion(world):
    a,*_=world
    bridge,cmd,_=setup(world)
    response,counts=DeletionWorker(bridge,stream_arn=STREAM,allowed_subjects=frozenset({ACCOUNT,'aaa-poison'}),remaining_ms=lambda:1000).stream(event(cmd))
    assert response['batchItemFailures']==[{'itemIdentifier':'100'}] and counts['failed']==1
    assert receipt(a) is None


def test_disabled_handler_does_not_load_secret_or_network(monkeypatch):
    monkeypatch.setenv('STAGE','dev');monkeypatch.setenv('V1_AUTHORITY_DELETION_ENABLED','false')
    monkeypatch.setattr(app,'_worker',lambda _:pytest.fail('disabled handler loaded dependencies'))
    assert app.lambda_handler({'anything':'ignored'},None)=={'enabled':False}


def test_handler_metrics_and_errors_never_expose_identifiers(world,monkeypatch,capsys):
    bridge,cmd,_=setup(world)
    monkeypatch.setenv('STAGE','dev');monkeypatch.setenv('V1_AUTHORITY_DELETION_ENABLED','true')
    monkeypatch.setattr(app,'_worker',lambda _:DeletionWorker(bridge,stream_arn=STREAM,allowed_subjects=frozenset({ACCOUNT,'aaa-poison'}),remaining_ms=lambda:30000))
    assert app.lambda_handler(event(cmd),None)=={'batchItemFailures':[]}
    logged=json.loads(capsys.readouterr().out)
    assert set(logged)=={'event','examined','completed','pending','failed','deleted','overdue','skipped','reason'}
    assert ACCOUNT not in str(logged) and SECRET not in str(logged)
    monkeypatch.setattr(app,'_worker',lambda _:(_ for _ in ()).throw(ValueError('sensitive-secret-url')))
    with pytest.raises(RuntimeError,match='^DELETION_UNAVAILABLE$'):app.lambda_handler(event(cmd),None)
    assert 'sensitive' not in capsys.readouterr().out


def test_independent_cleanup_loader_uses_current_even_when_authority_disabled(monkeypatch):
    import boto3
    monkeypatch.setenv('STAGE','dev');monkeypatch.setenv('AUTHORITY_ENABLED','false');monkeypatch.setenv('AUTHORITY_HMAC_SECRET_ARN',SECRET)
    calls=[]
    class Secret:
        def get_secret_value(self,**kwargs):
            calls.append(kwargs)
            return {'SecretString':json.dumps({'activeKeyId':'k1','keys':{'k1':base64.b64encode(b'synthetic-long-key-material-000000').decode()}})}
        def close(self):pass
    monkeypatch.setattr(boto3,'client',lambda *args,**kwargs:Secret())
    kid,keys=load_keyring()
    assert kid=='k1' and len(keys['k1'])>=32
    assert calls==[{'SecretId':SECRET,'VersionStage':'AWSCURRENT'}]


def test_live_authority_mutations_fail_closed_without_inventory(world):
    a,e,*_=world
    a.ddb.Table('authority').delete_item(Key=INVENTORY_KEY)
    failure('KEY_INVENTORY_UNAVAILABLE',lambda:a.prepare(e,PAYLOAD,'missing-inventory'))


def test_inventory_rotation_between_read_and_admission_is_atomic(world,monkeypatch):
    a,e,*_=world
    proof=a.prepare(e,PAYLOAD,'rotation-race')
    original=a.client.transact_write_items
    fired=[]
    def racing(**kwargs):
        if any(x.get('Put',{}).get('Item',{}).get('recordType')=='V1_CHECK_RECEIPT' for x in kwargs['TransactItems']):
            row=a._get('authority',INVENTORY_KEY)
            a.ddb.Table('authority').put_item(Item=row|{'revision':2});fired.append(True)
        return original(**kwargs)
    monkeypatch.setattr(a.client,'transact_write_items',racing)
    failure('TRANSACTION_UNCERTAIN',lambda:a.admit(e,PAYLOAD,proof))
    assert fired
    assert a._get('authority',{'PK':a._partition(ACCOUNT,'k1'),'SK':'CHECK#'+proof}) is None


def test_unlisted_command_is_never_mutated_by_stream_or_schedule(world):
    a,*_=world
    bridge,cmd,_=setup(world)
    worker=DeletionWorker(bridge,stream_arn=STREAM,allowed_subjects=frozenset({'different-subject'}),remaining_ms=lambda:30000)
    response,counts=worker.stream(event(cmd))
    assert response=={'batchItemFailures':[]} and counts['skipped']==1
    counts=worker.reconcile()
    assert counts['skipped']==1 and counts['completed']==0
    assert receipt(a) is None and len(items(a,bridge._partition(ACCOUNT,'k1')))==2


def test_overdue_count_does_not_repeat_for_completed_receipt(world):
    a,e,put,row,change,clock=world
    bridge,cmd,_=setup(world)
    clock[0]+=86401
    worker=DeletionWorker(bridge,stream_arn=STREAM,allowed_subjects=frozenset({ACCOUNT}),remaining_ms=lambda:30000)
    first=worker.reconcile()
    assert first['overdue']==1 and first['fullPassCompleted']==1 and first['fullPassAgeSeconds']==0
    assert worker.reconcile()['overdue']==0


def test_full_pass_age_survives_no_time_and_resets_after_progress(world):
    a,e,put,row,change,clock=world
    bridge,cmd,_=setup(world)
    budget=[0]
    worker=DeletionWorker(bridge,stream_arn=STREAM,allowed_subjects=frozenset({ACCOUNT}),remaining_ms=lambda:budget[0])
    first=worker.reconcile()
    assert first['fullPassCompleted']==0 and first['pages']==0
    clock[0]+=120
    second=worker.reconcile()
    assert second['fullPassCompleted']==0 and second['fullPassAgeSeconds']==120
    budget[0]=30000
    third=worker.reconcile()
    assert third['fullPassCompleted']==1 and third['fullPassAgeSeconds']==0


def test_empty_subject_set_cannot_enable_worker(world):
    bridge,_,_=setup(world)
    failure('DELETION_CONFIGURATION_UNAVAILABLE',lambda:DeletionWorker(bridge,stream_arn=STREAM,allowed_subjects=frozenset(),remaining_ms=lambda:30000))


def test_terminal_fence_and_old_requested_stream_replay_never_restart_cleanup(world):
    a,e,put,row,change,clock=world
    bridge,cmd,_=setup(world)
    completed=cmd|{'status':'COMPLETE','eventType':'account.deletion.completed','completedAtEpoch':clock[0],'retainUntilEpoch':clock[0]+120*86400}
    a.ddb.Table('deletion').put_item(Item=completed)
    worker=DeletionWorker(bridge,stream_arn=STREAM,allowed_subjects=frozenset({ACCOUNT}),remaining_ms=lambda:30000)
    before=items(a,bridge._partition(ACCOUNT,'k1'))
    for image in (cmd,completed):
        response,counts=worker.stream(event(image))
        assert response=={'batchItemFailures':[]} and counts['deleted']==0 and counts['completed']==1
    assert items(a,bridge._partition(ACCOUNT,'k1'))==before
    response,counts=worker.stream(event(cmd|{'operationId':'d2dca50d-d9de-43c8-b95f-83d0a02ccdc7'}))
    assert counts['failed']==1 and response['batchItemFailures']
    a.ddb.Table('deletion').put_item(Item=completed|{'completedAtEpoch':True})
    response,counts=worker.stream(event(completed|{'completedAtEpoch':True}))
    assert counts['failed']==1 and response['batchItemFailures']
