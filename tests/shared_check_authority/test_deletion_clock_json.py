"""Real SDK/Moto checkpoint clocks through both deployed scheduled handlers."""
import importlib
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('isolated SDK integration',allow_module_level=True)
sys.path.insert(0,str(Path(__file__).parent))
from test_transactions import world,ACCOUNT
from test_deletion import setup
from v1_authority_deletion.service import DeletionWorker,CURSOR_KEY
from shared_play_lifecycle.deletion import TokenDeletion
from shared_check_authority.core import AuthorityError

@pytest.fixture(params=['play','v1'])
def scheduled(world,request,monkeypatch):
    a,_,_,_,_,clock=world
    bridge,command,_=setup(world)
    kind=request.param
    if kind=='play':
        a.ddb.create_table(TableName='tokens',BillingMode='PAY_PER_REQUEST',KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':k,'AttributeType':'S'} for k in ('PK','SK')])
        bridge=TokenDeletion(a.ddb,authority_table='authority',ledger_table='deletion',environment='dev',keyring=a.s.hmac_keys,receipt_retention_seconds=120*86400,now=a.now,token_table='tokens')
    key={'PK':'PLAY#CONTROL','SK':'TOKEN_DELETION_CURSOR'} if kind=='play' else CURSOR_KEY
    app=importlib.import_module('play_token_deletion.app' if kind=='play' else 'v1_authority_deletion.app')
    monkeypatch.setenv('STAGE','dev');monkeypatch.setenv('PLAY_TOKEN_CLEANUP_ENABLED' if kind=='play' else 'V1_AUTHORITY_DELETION_ENABLED','true')
    budget=[30000]
    worker=DeletionWorker(bridge,stream_arn='unused-scheduled-stream',remaining_ms=lambda:budget[0],allowed_subjects=frozenset({ACCOUNT}),cursor_key=key)
    monkeypatch.setattr(app,'_worker',lambda _:worker)
    event={'schemaVersion':1,'operation':'reconcile-play-token-deletion' if kind=='play' else 'reconcile-v1-authority-deletion'}
    return a,clock,bridge,key,budget,lambda:app.lambda_handler(event,None),command

@pytest.mark.parametrize('previous_pass',[None,1799999800])
def test_partial_and_complete_scheduled_responses_and_logs_are_json_integers(scheduled,previous_pass,capsys):
    a,clock,bridge,key,budget,invoke,command=scheduled
    ledger=a.ddb.Table('deletion')
    for i in range(60):ledger.put_item(Item={'PK':f'OTHER#{i:03d}','SK':'METADATA','recordType':'UNRELATED'})
    ledger.put_item(Item=key|{'revision':1,'cursor':None,'scanStartedAtEpoch':clock[0]-300,'lastFullPassAtEpoch':previous_pass})
    observed=ledger.get_item(Key=key,ConsistentRead=True)['Item']
    assert isinstance(observed['scanStartedAtEpoch'],Decimal)
    result=invoke();logged=json.loads(capsys.readouterr().out)
    assert result['fullPassCompleted']==0 and type(result['fullPassAgeSeconds']) is int
    assert result['fullPassAgeSeconds']==clock[0]-(previous_pass or clock[0]-300)
    assert type(logged['fullPassAgeSeconds']) is int and json.loads(json.dumps(result))==result
    saved=ledger.get_item(Key=key,ConsistentRead=True)['Item']
    assert saved['revision']==2 and saved['cursor'] is not None
    assert saved['scanStartedAtEpoch']==observed['scanStartedAtEpoch'] and saved['lastFullPassAtEpoch']==previous_pass
    component='PLAY_TOKENS' if key['PK']=='PLAY#CONTROL' else 'V1_AUTHORITY'
    assert ledger.get_item(Key={'PK':command['PK'],'SK':'ACCOUNT_DELETION#'+component},ConsistentRead=True)['Item']['status']=='COMPLETE'
    clock[0]+=60
    result=invoke();logged=json.loads(capsys.readouterr().out)
    assert result['fullPassCompleted']==1 and result['fullPassAgeSeconds']==0 and logged['fullPassAgeSeconds']==0
    assert ledger.get_item(Key={'PK':'OTHER#059','SK':'METADATA'},ConsistentRead=True)['Item']['recordType']=='UNRELATED'

def test_budget_limited_checkpoint_age_is_serializable_without_erasure(scheduled,capsys):
    a,clock,bridge,key,budget,invoke,command=scheduled
    row=key|{'revision':1,'cursor':None,'scanStartedAtEpoch':clock[0]-120,'lastFullPassAtEpoch':None}
    a.ddb.Table('deletion').put_item(Item=row);budget[0]=0
    result=invoke();logged=json.loads(capsys.readouterr().out)
    assert result['pages']==0 and result['deleted']==0 and result['fullPassAgeSeconds']==120
    assert type(result['fullPassAgeSeconds']) is int and logged['fullPassAgeSeconds']==120
    assert a._get('deletion',{'PK':command['PK'],'SK':'ACCOUNT_DELETION#PLAY_TOKENS' if key['PK']=='PLAY#CONTROL' else 'ACCOUNT_DELETION#V1_AUTHORITY'}) is None

@pytest.mark.parametrize('field,value',[('scanStartedAtEpoch',Decimal('1.5')),('lastFullPassAtEpoch',True),('lastFullPassAtEpoch',1800000001)])
def test_invalid_checkpoint_clocks_still_refuse_without_mutation(scheduled,field,value,capsys):
    a,clock,bridge,key,budget,invoke,command=scheduled
    row=key|{'revision':1,'cursor':None,'scanStartedAtEpoch':clock[0]-120,'lastFullPassAtEpoch':None,field:value}
    a.ddb.Table('deletion').put_item(Item=row)
    with pytest.raises(RuntimeError,match='DELETION_UNAVAILABLE'):invoke()
    log=json.loads(capsys.readouterr().out);assert log['failed']==1
    assert a._get('deletion',key)==row

def test_real_storage_error_remains_failed_and_diagnostic_is_fixed(scheduled,monkeypatch,capsys):
    a,clock,bridge,key,budget,invoke,command=scheduled
    def fail(_):raise AuthorityError('DELETION_TRANSACTION_UNCERTAIN')
    monkeypatch.setattr(bridge,'_transact',fail)
    with pytest.raises(RuntimeError,match='DELETION_UNAVAILABLE'):invoke()
    log=json.loads(capsys.readouterr().out);assert log['failed']==1
    assert 'DELETION_TRANSACTION_UNCERTAIN' not in json.dumps(log) and ACCOUNT not in json.dumps(log)
    assert a._get('deletion',key) is None
