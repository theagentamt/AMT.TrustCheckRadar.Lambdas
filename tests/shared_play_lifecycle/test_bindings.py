import os,sys,importlib.util
from pathlib import Path
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('isolated SDK integration',allow_module_level=True)
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests/shared_check_authority'),str(ROOT/'tests/shared_play_verification')]
spec=importlib.util.spec_from_file_location('play_fixture_bindings',ROOT/'tests/v1_play_handoff/test_service.py');fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
from test_transactions import world,ACCOUNT
handoff=fixtures.handoff
from shared_play_lifecycle.bindings import Bindings,reverse_key,locator_key
from shared_check_authority.core import AuthorityError
from v1_play_handoff.service import account_binding

@pytest.fixture
def service(handoff):
 h,w,p=handoff;a,e,*_=w
 a.ddb.create_table(TableName='play-tokens',BillingMode='PAY_PER_REQUEST',KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':'PK','AttributeType':'S'},{'AttributeName':'SK','AttributeType':'S'}])
 return Bindings(h.writer,'play-tokens'),w

def test_prepare_is_account_bound_idempotent_metadata_only(service):
 b,w=service;a,e,*_=w
 expected=b.prepare(e);rows=a.ddb.Table('play-tokens').scan()['Items']
 assert expected['accountBinding']==account_binding(ACCOUNT) and len(rows)==2
 assert b.prepare(e)==expected and a.ddb.Table('play-tokens').scan()['Items']==rows
 assert b.resolve(expected['accountBinding'])[0]==ACCOUNT
 assert all('expiresAt' not in row and 'purchaseToken' not in row for row in rows)
 assert a._get('authority',{'PK':a._partition(ACCOUNT,'k1'),'SK':'ACCESS'}) is None

def test_deleted_account_cannot_prepare_or_resolve(service):
 b,w=service;a,e,*_=w;b.prepare(e)
 a.ddb.Table(a.s.deletion_table).put_item(Item={'PK':'ACCOUNT#'+ACCOUNT,'SK':'ACCOUNT_DELETION'})
 with pytest.raises(AuthorityError):b.prepare(e)
 with pytest.raises(AuthorityError):b.resolve(account_binding(ACCOUNT))

def test_missing_locator_is_not_recreated(service):
 b,w=service;a,e,*_=w;b.prepare(e)
 a.ddb.Table('play-tokens').delete_item(Key=locator_key(a._partition(ACCOUNT,'k1')))
 with pytest.raises(AuthorityError):b.prepare(e)
 assert len(a.ddb.Table('play-tokens').scan()['Items'])==1

def test_prepare_deletion_race_is_atomic(service):
 b,w=service;a,e,*_=w;original=a._transact
 def raced(actions):
  a.ddb.Table(a.s.deletion_table).put_item(Item={'PK':'ACCOUNT#'+ACCOUNT,'SK':'ACCOUNT_DELETION'})
  original(actions)
 a._transact=raced
 with pytest.raises(AuthorityError):b.prepare(e)
 assert a.ddb.Table('play-tokens').scan()['Items']==[]
