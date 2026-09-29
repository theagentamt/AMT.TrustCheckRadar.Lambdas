"""Exact installed39 reader comparison against newly versioned admitted receipts."""
import sys,types,subprocess
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(Path(__file__).parent)]
from test_transactions import world,PAYLOAD,ACCOUNT
from shared_check_authority import core,purchase_usage
BASE='39cce61623794a123e61d25f5248b0c081eccf4a'

def old_module(name):
    package='sec233_installed_reader'
    if package not in sys.modules:
        p=types.ModuleType(package);p.__path__=[];sys.modules[package]=p;sys.modules[package+'.core']=core
    qualified=package+'.'+name
    if qualified not in sys.modules:
        source=subprocess.check_output(['git','show',BASE+':src/shared_check_authority/'+name+'.py'],cwd=ROOT,text=True)
        m=types.ModuleType(qualified);m.__package__=package;sys.modules[qualified]=m;exec(compile(source,BASE+':'+name,'exec'),m.__dict__)
    return sys.modules[qualified]

@pytest.mark.parametrize('modern',[False,True])
def test_old_recovery_consumes_new_admitted_url_without_provider(world,modern):
    a,e,_,row,_,clock=world;payload=PAYLOAD|({'urlTransportVersion':'1.0.0-candidate.2'} if modern else {})
    proof=a.prepare(e,payload,'case',client_check_id='case');a.admit(e,payload,proof,client_check_id='case')
    partition=a._partition(ACCOUNT,'k1');old=old_module('purchase_usage')
    # Deletion's paid reservation reader also calls this exact unchanged primitive.
    assert old.period_for_receipt(a.ddb,'authority',partition,row('CHECK#'+proof))==row('PERIOD#p1')
    clock[0]+=a.s.worker_settlement_seconds
    assert old_module('recovery').Recovery(a.ddb,'authority',now=lambda:clock[0]).expire(partition,proof)
    assert row('CHECK#'+proof)['chargedChecks']==0 and row('PERIOD#p1')['reservedChecks']==0

@pytest.mark.parametrize('modern',[False,True])
def test_closed_entitlement_reader_requires_compatibility_for_modern_pending(world,modern):
    a,e,_,row,_,clock=world;payload=PAYLOAD|({'urlTransportVersion':'1.0.0-candidate.2'} if modern else {})
    proof=a.prepare(e,payload,'case',client_check_id='case');a.admit(e,payload,proof,client_check_id='case')
    period=row('PERIOD#p1');global_row=a._get('authority',period['purchaseUsageKey']);clock[0]=int(global_row['expiresAt'])+1
    args=(a.ddb,'authority',period,global_row,period['endEpoch'])
    before=row('CHECK#'+proof)
    if modern:
        with pytest.raises(core.AuthorityError,match='PURCHASE_USAGE_RECONCILIATION_REQUIRED'):
            old_module('purchase_usage').due_reservation_actions(*args,now=clock[0])
        assert row('CHECK#'+proof)==before
    else:assert old_module('purchase_usage').due_reservation_actions(*args,now=clock[0])
    actions=purchase_usage.due_reservation_actions(*args,now=clock[0])
    a.client.transact_write_items(TransactItems=actions)
    assert row('CHECK#'+proof) is None and row('INFLIGHT')['activeCount']==0
