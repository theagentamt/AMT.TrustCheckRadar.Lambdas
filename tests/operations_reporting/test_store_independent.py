"""Independent store-boundary probes; every client operation is inside Moto."""
import importlib.util
from pathlib import Path
import json

import pytest

spec=importlib.util.spec_from_file_location('_store_independent_local_fixtures',Path(__file__).with_name('test_store.py'))
local=importlib.util.module_from_spec(spec)
spec.loader.exec_module(local)
client=local.client
corrected,DAY,NEXT,rows,store,summary,Proxy=(getattr(local,k) for k in ['corrected','DAY','NEXT','rows','store','summary','Proxy'])
from operations_reporting.store import AggregateStoreFault



def test_invalid_utf8_stored_json_is_finite_storage_fault(client,capsys,caplog):
    store(client).commit(summary())
    p=Proxy(client)
    original=p.get_item
    def corrupt(**kwargs):
        result=original(**kwargs)
        if kwargs['Key']['SK']['S'].startswith('REVISION#'):
            result['Item']['recordJson']={'S':'\ud800'}
        return result
    p.get_item=corrupt
    with pytest.raises(AggregateStoreFault,match='^INVALID_STORED_RECORD$') as error:
        store(p).read(DAY)
    assert error.value.args==('INVALID_STORED_RECORD',)
    assert capsys.readouterr()==('', '') and caplog.text==''


def test_lost_second_revision_response_reconciles_without_duplicate_append(client):
    first=store(client).commit(summary())['record']
    p=Proxy(client)
    def lose_after_commit(**kwargs):
        p.writes+=1
        client.transact_write_items(**kwargs)
        raise TimeoutError('private service detail')
    p.transact_write_items=lose_after_commit
    with pytest.raises(AggregateStoreFault,match='^WRITE_OUTCOME_UNKNOWN$'):
        store(p,NEXT).commit(corrected())
    assert p.writes==1
    current=store(client,NEXT).read(DAY)['record']
    assert current['revision']==2 and current['previousRecordSha256']==first['recordSha256']
    assert store(client,NEXT).commit(corrected())['decision']=='NO_CHANGE'
    assert len(rows(client))==3


@pytest.mark.parametrize('value',['1e0','01','+1','1.0','-1'])
def test_noncanonical_dynamo_revision_numbers_are_not_coerced(client,value):
    store(client).commit(summary())
    p=Proxy(client);original=p.get_item
    def malformed(**kwargs):
        result=original(**kwargs)
        if kwargs['Key']['SK']['S']=='HEAD':result['Item']['revision']={'N':value}
        return result
    p.get_item=malformed
    with pytest.raises(AggregateStoreFault,match='^INVALID_STORED_RECORD$'):
        store(p).read(DAY)
    assert p.writes==0


def test_noncanonical_serialized_json_cannot_bypass_head_hash_join(client):
    store(client).commit(summary())
    p=Proxy(client);original=p.get_item
    def pretty(**kwargs):
        result=original(**kwargs)
        if kwargs['Key']['SK']['S'].startswith('REVISION#'):
            value=json.loads(result['Item']['recordJson']['S'])
            result['Item']['recordJson']['S']=json.dumps(value,indent=2)
        return result
    p.get_item=pretty
    with pytest.raises(AggregateStoreFault,match='^INVALID_STORED_RECORD$'):
        store(p).read(DAY)
    assert p.writes==0


@pytest.mark.parametrize('label',['20261003','private-label@invalid.test','2026-02-30'])
def test_invalid_scope_never_reaches_storage_or_echoes_input(client,label):
    p=Proxy(client)
    with pytest.raises(AggregateStoreFault,match='^INVALID_INPUT$') as error:
        store(p).read(label)
    assert error.value.args==('INVALID_INPUT',) and p.gets==p.writes==0
