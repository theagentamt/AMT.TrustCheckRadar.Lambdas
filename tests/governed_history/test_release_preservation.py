"""Independent source-reconciliation safety checks; local Git and Moto only."""
import ast
from copy import deepcopy
from dataclasses import replace
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

ROOT=Path(__file__).resolve().parents[2]
BASE='29138fffefe06a398ffb224d0de3cda42d7f73a4'
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests/shared_check_authority')]

from test_transactions import ACCOUNT, world
from shared_check_authority import core
from message_evaluator.policy_v3 import evaluate
from shared_governed_history import RETENTION_SECONDS


def old_blob(path):
    return subprocess.check_output(['git','-C',str(ROOT),'show',BASE+':'+path])


def function_ast(source,name):
    tree=ast.parse(source)
    return ast.dump(next(n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name),include_attributes=False)


@pytest.mark.parametrize('name',['_account','_record_prepare_auth_failure','_transact','_account_conditions','_authority_conditions'])
def test_release_auth_diagnostic_and_transaction_fences_unchanged(name):
    assert function_ast(old_blob('src/shared_check_authority/core.py'),name)==function_ast((ROOT/'src/shared_check_authority/core.py').read_bytes(),name)


@pytest.mark.parametrize('path',[
    'src/shared_check_authority/entitlements.py',
    'src/shared_check_authority/purchase_usage.py',
    'src/shared_check_authority/prepare_auth_diagnostics.py',
    'src/v1_play_handoff/app.py','src/v1_play_handoff/service.py',
    'src/shared_purchase_ownership/service.py',
    'src/operations_reporting/reducer.py','src/operations_reporting/revisions.py',
    'scripts/lambda_release_scope.py','.github/workflows/publish.yml',
])
def test_release_billing_operations_and_publication_overlays_byte_exact(path,tmp_path):
    if path == 'src/shared_check_authority/purchase_usage.py':
        # SEC237 explicitly extends only typed optional metadata in this one
        # closure function. Every other original accounting statement stays exact;
        # legacy closure action equality is independently executed in Moto.
        before = ast.parse(old_blob(path))
        after = ast.parse((ROOT/path).read_bytes())
        original = next(n for n in before.body if isinstance(n, ast.FunctionDef) and n.name == 'due_reservation_actions')
        current = next(n for n in after.body if isinstance(n, ast.FunctionDef) and n.name == 'due_reservation_actions')
        after.body[after.body.index(current)] = original
        assert ast.dump(before, include_attributes=False) == ast.dump(after, include_attributes=False)
        return
    if path in ('src/operations_reporting/reducer.py','src/operations_reporting/revisions.py'):
        # SEC237 scoped candidate2 intentionally extends these files. Preserve
        # candidate1 executable output/error behavior against the same pinned
        # release, not an obsolete whole-file equality that forbids the change.
        assert_reporting_legacy_contract(tmp_path)
        return
    assert (ROOT/path).read_bytes()==old_blob(path)



def assert_reporting_legacy_contract(tmp_path):
    package=tmp_path/'operations_reporting';package.mkdir()
    for member in ('__init__.py','reducer.py','revisions.py','event.schema.json','rejection.schema.json'):
        (package/member).write_bytes(old_blob('src/operations_reporting/'+member))
    program = r"""
from copy import deepcopy
import json,sys
from operations_reporting.reducer import aggregate_day,day_bounds
from operations_reporting.revisions import plan_revision
f=json.load(open(sys.argv[1])); events=[x['event']for x in f['events']]
rejections=[x['aggregate']for x in f['rejectionAggregates']]
t=day_bounds('2026-10-03')[1]+60
cases=[(events,rejections,'dev',t),([],[],'dev',t),(events+events,rejections,'dev',t),
       (events,rejections,'wrong',t),(events,rejections,'dev',0)]
bad=deepcopy(events);bad[0]['privateIdentity']='synthetic-prohibited';cases.append((bad,rejections,'dev',t))
result=[]
for e,r,env,at in cases:
    try:
        report=aggregate_day(e,r,local_day='2026-10-03',environment=env,as_of_epoch=at)
        first=plan_revision(None,report,generated_at_epoch=at)
        replay=plan_revision(first['record'],report,generated_at_epoch=at+86400)
        result.append({'report':report,'first':first,'replay':replay})
    except Exception as error:result.append({'code':getattr(error,'code',type(error).__name__)})
print(json.dumps(result,sort_keys=True,separators=(',',':')))
"""
    fixture=ROOT/'tests/operations_reporting/fixtures.json'
    def run(pythonpath):
        return subprocess.check_output([sys.executable,'-B','-c',program,str(fixture)],cwd=tmp_path,
            env=dict(os.environ,PYTHONPATH=str(pythonpath),AWS_EC2_METADATA_DISABLED='true'))
    assert run(tmp_path)==run(ROOT/'src')


def baseline_module():
    name='shared_check_authority._independent_release_baseline'
    module=types.ModuleType(name);module.__package__='shared_check_authority'
    sys.modules[name]=module
    exec(compile(old_blob('src/shared_check_authority/core.py'),'<pinned release core>','exec'),module.__dict__)
    return module


def payload():
    return {'entryPoint':'message','language':'en','messageTransportVersion':'1.0.0-message-candidate.3',
            'target':{'scope':'sanitized_message','sourceType':'pasted_text',
                      'sanitizedText':'Send me your account password and the login code.',
                      'speakerRole':'other','entities':[],'withheldLinks':False,'reviewedLinks':[]}}


@pytest.mark.parametrize('outcome',['complete','inconclusive','failed'])
def test_default_off_actual_admit_settle_rows_transactions_and_clock_match_release(world,monkeypatch,outcome):
    a,event,*_=world
    seed={name:deepcopy(a.ddb.Table(name).scan()['Items']) for name in ['users','devices','deletion','authority']}
    monkeypatch.setattr(core.secrets,'token_hex',lambda n:'a'*(2*n))
    old=baseline_module()
    def run(module):
        for table,rows in seed.items():
            resource=a.ddb.Table(table)
            for item in resource.scan()['Items']:resource.delete_item(Key={k:item[k] for k in ['PK','SK']})
            for item in rows:resource.put_item(Item=deepcopy(item))
        ticks=[]
        def clock():
            value=1800000000+len(ticks);ticks.append(value);return value
        authority=module.Authority(a.s,a.ddb,now=clock)
        calls=[];real=authority._transact
        def capture(items):calls.append(deepcopy(items));return real(items)
        authority._transact=capture
        p=payload();proof=authority.prepare(event,p,'release-preservation',client_check_id='release-preservation')
        admitted=authority.admit(event,p,proof,client_check_id='release-preservation')
        summary=None
        if outcome=='complete':summary=evaluate('release-preservation',{k:p[k] for k in ['entryPoint','language','target']},rules_only=True)
        result=authority.settle(module.TrustedWorkerContext(ACCOUNT),proof,admitted['executionToken'],outcome,result_summary=summary)
        rows={name:sorted(a.ddb.Table(name).scan()['Items'],key=lambda x:(x['PK'],x['SK'])) for name in seed}
        return proof,admitted,result,rows,calls,ticks
    assert run(old)==run(core)


def test_enabled_projection_retention_is_settlement_based_and_replay_fixed(world):
    a,event,_,get,_,clock=world
    a.s=replace(a.s,receipt_retention_seconds=RETENTION_SECONDS,counter_retention_seconds=RETENTION_SECONDS,
                governed_history_settlement_enabled=True)
    p=payload();proof=a.prepare(event,p,'late-settle',client_check_id='late-settle')
    admitted=a.admit(event,p,proof,client_check_id='late-settle')
    accepted=get('CHECK#'+proof)['governedHistoryAcceptedAtEpoch']
    clock[0]+=10
    summary=evaluate('late-settle',{k:p[k] for k in ['entryPoint','language','target']},rules_only=True)
    a.settle(core.TrustedWorkerContext(ACCOUNT),proof,admitted['executionToken'],'complete',result_summary=summary)
    row=get('CHECK#'+proof);assert row['expiresAt']==clock[0]+RETENTION_SECONDS
    assert row['governedHistory']['acceptedAtEpoch']==accepted
    assert row['governedHistory']['settledAtEpoch']==clock[0]
    before=deepcopy(row);clock[0]+=10
    a.settle(core.TrustedWorkerContext(ACCOUNT),proof,admitted['executionToken'],'complete',result_summary=summary)
    assert get('CHECK#'+proof)==before


def test_ci_event_permission_scope_and_job_set_preserved():
    # Only existing job commands may gain governed tests/package, never triggers.
    before=old_blob('.github/workflows/ci.yml').decode();after=(ROOT/'.github/workflows/ci.yml').read_text()
    assert before.split('jobs:',1)[0]==after.split('jobs:',1)[0]
    def jobs(s):return [line for line in s.splitlines() if line.startswith('  ') and not line.startswith('   ') and line.endswith(':')]
    assert jobs(before)==jobs(after)


@pytest.mark.parametrize('operation',['list','detail'])
def test_canonical_storage_failure_cannot_be_reported_as_empty_or_missing(world,monkeypatch,operation):
    from governed_history.service import Service,Settings,GovernedHistoryError
    a,event,_,get,_,clock=world
    a.s=replace(a.s,receipt_retention_seconds=RETENTION_SECONDS,counter_retention_seconds=RETENTION_SECONDS,
                governed_history_settlement_enabled=True)
    p=payload();proof=a.prepare(event,p,'read-fault',client_check_id='read-fault')
    admitted=a.admit(event,p,proof,client_check_id='read-fault')
    summary=evaluate('read-fault',{k:p[k] for k in ['entryPoint','language','target']},rules_only=True)
    a.settle(core.TrustedWorkerContext(ACCOUNT),proof,admitted['executionToken'],'complete',result_summary=summary)
    result_id=get('CHECK#'+proof)['governedHistory']['resultId']
    api=Service(a,Settings('GSI2',900,RETENTION_SECONDS,20,50,262144),account=ACCOUNT,
                fingerprint='device-one',device_version=1,inventory_revision=1,now=lambda:clock[0])
    original=a.ddb.Table
    class ReadFault:
        def __init__(self,table):self.table=table
        def query(self,**kwargs):return self.table.query(**kwargs)
        def get_item(self,**kwargs):raise TimeoutError('PRIVATE_SENTINEL cannot expose')
    monkeypatch.setattr(a.ddb,'Table',lambda name:ReadFault(original(name)))
    with pytest.raises(GovernedHistoryError,match='^SERVICE_UNAVAILABLE$'):
        api.list() if operation=='list' else api.detail(result_id)


def test_extreme_limit_parameter_remains_fixed_invalid_request(world):
    from governed_history.service import Service,Settings,GovernedHistoryError
    a,_,_,_,_,clock=world
    api=Service(a,Settings('GSI2',900,RETENTION_SECONDS,20,50,262144),account=ACCOUNT,
                fingerprint='device-one',device_version=1,inventory_revision=1,now=lambda:clock[0])
    with pytest.raises(GovernedHistoryError,match='^INVALID_REQUEST$'):
        api._limit('9'*5000)
