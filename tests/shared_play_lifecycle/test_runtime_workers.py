"""Real DynamoDB/Moto bounds, no provider/KMS network."""
import os,sys,importlib.util,base64,hashlib
from pathlib import Path
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('isolated SDK integration',allow_module_level=True)
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests/shared_check_authority')]
from test_transactions import world,ACCOUNT
from test_deletion import setup
from shared_play_lifecycle.tokens import prepare_put,key
from shared_play_lifecycle.deletion import TokenDeletion
from shared_play_lifecycle.checkpoint import KEY,Checkpoint
from play_lifecycle_worker.service import Worker
from shared_check_authority.core import AuthorityError
from shared_play_lifecycle.export import page
ARN='arn:aws:kms:us-east-1:107827791950:key/12345678-1234-1234-1234-123456789abc'
class Cipher:
 def encrypt(self,token,partition):return ARN,base64.b64encode(hashlib.sha256((partition+token).encode()).digest()).decode(),base64.b64encode(b'wrapped').decode()

@pytest.fixture
def tokens(world):
 a,e,*_=world
 a.ddb.create_table(TableName='play-tokens',BillingMode='PAY_PER_REQUEST',KeySchema=[{'AttributeName':'PK','KeyType':'HASH'},{'AttributeName':'SK','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':x,'AttributeType':'S'} for x in ('PK','SK','GSI1PK','GSI1SK')],GlobalSecondaryIndexes=[{'IndexName':'GSI1','KeySchema':[{'AttributeName':'GSI1PK','KeyType':'HASH'},{'AttributeName':'GSI1SK','KeyType':'RANGE'}],'Projection':{'ProjectionType':'KEYS_ONLY'}}])
 return world

def put_token(a,partition,token,end,due):
 row=prepare_put('play-tokens',partition,'synthetic-token-'+token,access_until_epoch=end,next_attempt_epoch=due,now=min(a.now(),due),cipher=Cipher())['Put']['Item']
 a.ddb.Table('play-tokens').put_item(Item=row);return row

def worker(a):
 return Worker(a.ddb,'play-tokens',now=a.now,remaining_ms=lambda:29000,reconciler_factory=lambda:pytest.fail('provider disabled'),lifecycle_enabled=False)

def test_fair_cleanup_after_more_than_fifty_due_live_rows(tokens):
 a,e,_,_,_,clock=tokens;pk=a._partition(ACCOUNT,'k1');now=clock[0]
 for i in range(55):put_token(a,pk,'live-'+str(i),now+10000,now-10)
 expired=put_token(a,pk,'expired',now-604801,now-604802)
 # Make expired row sort later than the overdue live rows while remaining valid.
 expired['nextAttemptAtEpoch']=now-1;expired['GSI1SK']=f'{now-1:012d}#{pk}#{expired["SK"]}'
 a.ddb.Table('play-tokens').put_item(Item=expired)
 first=worker(a).run();assert first['expiredDeleted']==0
 second=worker(a).run();assert second['expiredDeleted']==1
 assert a._get('play-tokens',key(pk,expired['tokenDigest'])) is None
 assert a._get('play-tokens',KEY)['cursor'] is None

def test_poison_rows_do_not_starve_expired_rows(tokens):
 a,e,_,_,_,clock=tokens;pk=a._partition(ACCOUNT,'k1');now=clock[0]
 for i in range(55):
  row=put_token(a,pk,'poison-'+str(i),now+1000,now-10);row['schemaVersion']=99;a.ddb.Table('play-tokens').put_item(Item=row)
 expired=put_token(a,pk,'expired',now-604801,now-604802);expired['nextAttemptAtEpoch']=now-1;expired['GSI1SK']=f'{now-1:012d}#{pk}#{expired["SK"]}';a.ddb.Table('play-tokens').put_item(Item=expired)
 assert worker(a).run()['failed']==50
 assert worker(a).run()['expiredDeleted']==1

def test_checkpoint_expiry_is_logical_and_target_absence_prevents_recreation(tokens):
 a,e,_,_,_,clock=tokens;pk=a._partition(ACCOUNT,'k1');row=put_token(a,pk,'proof',clock[0]+1000,clock[0])
 checkpoint=Checkpoint(a.ddb,'play-tokens',a.now);checkpoint.advance(row)
 clock[0]+=300;assert Checkpoint(a.ddb,'play-tokens',a.now).cursor is None
 a.ddb.Table('play-tokens').delete_item(Key=key(pk,row['tokenDigest']))
 with pytest.raises(Exception):checkpoint.advance(row)

def test_account_erasure_deletes_tokens_cursor_then_exact_component_receipt(tokens):
 a,e,_,_,_,clock=tokens;pk=a._partition(ACCOUNT,'k1');row=put_token(a,pk,'proof',clock[0]+1000,clock[0]);Checkpoint(a.ddb,'play-tokens',a.now).advance(row)
 _,command,_=setup(tokens)
 bridge=TokenDeletion(a.ddb,authority_table='authority',ledger_table='deletion',environment='dev',keyring=a.s.hmac_keys,receipt_retention_seconds=120*86400,now=a.now,token_table='play-tokens')
 assert not bridge.delete_batch(command)['complete']
 assert bridge.delete_batch(command)['complete']
 assert a._get('play-tokens',KEY) is None
 receipt=a._get('deletion',{'PK':command['PK'],'SK':'ACCOUNT_DELETION#PLAY_TOKENS'})
 assert receipt['status']=='COMPLETE' and receipt['retainUntilEpoch']==clock[0]+120*86400
 assert bridge.delete_batch(command)['alreadyComplete']

def test_unknown_account_row_blocks_completion_and_is_preserved(tokens):
 a,e,_,_,_,clock=tokens;pk=a._partition(ACCOUNT,'k1');a.ddb.Table('play-tokens').put_item(Item={'PK':pk,'SK':'UNKNOWN'})
 _,command,_=setup(tokens)
 bridge=TokenDeletion(a.ddb,authority_table='authority',ledger_table='deletion',environment='dev',keyring=a.s.hmac_keys,receipt_retention_seconds=120*86400,now=a.now,token_table='play-tokens')
 with pytest.raises(AuthorityError):bridge.delete_batch(command)
 assert a._get('play-tokens',{'PK':pk,'SK':'UNKNOWN'}) is not None

def test_export_projects_metadata_never_credentials(tokens):
 a,e,_,_,_,clock=tokens;pk=a._partition(ACCOUNT,'k1');row=put_token(a,pk,'proof',clock[0]+1000,clock[0])
 result,cursor=page(a,'play-tokens',ACCOUNT,pk,None)
 assert cursor is None and result==[{'kind':'google_play_verification','platform':'google_play','verifiedAccessUntilEpoch':clock[0]+1000,'expiresAtEpoch':clock[0]+605800,'acknowledgment':'pending'}]
 assert all(secret not in str(result) for secret in (row['ciphertext'],row['wrappedDataKey'],row['tokenDigest'],pk))


def test_checkpoint_refuses_noncanonical_pointer_content(tokens):
 a,e,_,_,_,clock=tokens;pk=a._partition(ACCOUNT,'k1');row=put_token(a,pk,'proof',clock[0]+1000,clock[0])
 checkpoint=Checkpoint(a.ddb,'play-tokens',a.now)
 for field,value in [('PK','V1#raw-secret'),('SK','PLAY_TOKEN#raw-secret'),('GSI1SK','raw-secret')]:
  with pytest.raises(AuthorityError):checkpoint.advance(row|{field:value})
 assert a._get('play-tokens',KEY) is None


def test_mapping_pair_and_all_four_retained_namespaces_are_erased(tokens):
 from shared_check_authority.entitlements import EntitlementWriter
 from shared_play_lifecycle.bindings import Bindings,reverse_key
 from v1_play_handoff.service import account_binding
 a,e,_,_,_,clock=tokens
 Bindings(EntitlementWriter(a,approved_products=frozenset(),operator_principals=frozenset(),verification_max_age_seconds=20),'play-tokens').prepare(e)
 keys={**a.s.hmac_keys,**{f'k{i}':('synthetic-extra-material-'+str(i)).encode().ljust(32,b'x') for i in (2,3,4)}}
 _,command,_=setup(tokens,keys=keys)
 bridge=TokenDeletion(a.ddb,authority_table='authority',ledger_table='deletion',environment='dev',keyring=keys,receipt_retention_seconds=120*86400,now=a.now,token_table='play-tokens')
 for kid in keys:put_token(a,bridge._partition(ACCOUNT,kid),'proof-'+kid,clock[0]+1000,clock[0])
 for _ in range(6):
  if bridge.delete_batch(command)['complete']:break
 else:pytest.fail('erasure did not finish')
 assert a.ddb.Table('play-tokens').scan()['Items']==[]
 assert a._get('play-tokens',reverse_key(account_binding(ACCOUNT))) is None


def test_deletion_command_race_preserves_token_and_no_receipt(tokens):
 a,e,_,_,_,clock=tokens;pk=a._partition(ACCOUNT,'k1');row=put_token(a,pk,'proof',clock[0]+1000,clock[0]);_,command,_=setup(tokens)
 bridge=TokenDeletion(a.ddb,authority_table='authority',ledger_table='deletion',environment='dev',keyring=a.s.hmac_keys,receipt_retention_seconds=120*86400,now=a.now,token_table='play-tokens');original=bridge._transact
 def race(actions):
  a.ddb.Table('deletion').put_item(Item=command|{'operationId':'82924f40-151f-469a-8648-fce7d620e51b'})
  original(actions)
 bridge._transact=race
 with pytest.raises(AuthorityError):bridge.delete_batch(command)
 assert a._get('play-tokens',key(pk,row['tokenDigest']))==row
 assert a._get('deletion',{'PK':command['PK'],'SK':'ACCOUNT_DELETION#PLAY_TOKENS'}) is None


def test_logical_deadline_hides_retained_token_then_explicit_cleanup_erases_it(tokens):
 a,e,_,_,_,clock=tokens;pk=a._partition(ACCOUNT,'k1');now=clock[0]
 row=put_token(a,pk,'deadline',now-604799,now)
 assert page(a,'play-tokens',ACCOUNT,pk,None)[0]
 assert worker(a).run()['expiredDeleted']==0
 clock[0]+=1
 # Moto does not implement TTL removal: omission is the reader's logical rule.
 assert a._get('play-tokens',key(pk,row['tokenDigest']))==row
 assert page(a,'play-tokens',ACCOUNT,pk,None)==([],None)
 assert worker(a).run()['expiredDeleted']==1
 assert a._get('play-tokens',key(pk,row['tokenDigest'])) is None


def test_refreshed_token_cannot_be_erased_by_stale_expiry_observation(tokens,monkeypatch):
 a,e,_,_,_,clock=tokens;pk=a._partition(ACCOUNT,'k1');now=clock[0]
 row=put_token(a,pk,'expiry-race',now-604801,now-604802)
 target=key(pk,row['tokenDigest']);original=a.ddb.meta.client.transact_write_items;refreshed=[]
 def race(**kwargs):
  if not refreshed and any('Delete' in action and action['Delete']['Key']==target for action in kwargs['TransactItems']):
   newer=prepare_put('play-tokens',pk,'synthetic-token-expiry-race',access_until_epoch=now+1000,next_attempt_epoch=now+60,now=now,cipher=Cipher(),observed=row)['Put']['Item']
   a.ddb.Table('play-tokens').put_item(Item=newer);refreshed.append(newer)
  return original(**kwargs)
 monkeypatch.setattr(a.ddb.meta.client,'transact_write_items',race)
 outcome=worker(a).run()
 assert outcome['failed']==1 and outcome['expiredDeleted']==0
 assert a._get('play-tokens',target)==refreshed[0]


def test_expired_but_physically_retained_owned_checkpoint_is_erased(tokens):
 a,e,_,_,_,clock=tokens;pk=a._partition(ACCOUNT,'k1')
 row=put_token(a,pk,'expired-cursor',clock[0]+1000,clock[0]);Checkpoint(a.ddb,'play-tokens',a.now).advance(row)
 clock[0]+=301
 assert a._get('play-tokens',KEY) is not None
 _,command,_=setup(tokens)
 bridge=TokenDeletion(a.ddb,authority_table='authority',ledger_table='deletion',environment='dev',keyring=a.s.hmac_keys,receipt_retention_seconds=120*86400,now=a.now,token_table='play-tokens')
 assert not bridge.delete_batch(command)['complete']
 assert bridge.delete_batch(command)['complete']
 assert a._get('play-tokens',KEY) is None


def test_erasure_preserves_other_accounts_token_and_checkpoint(tokens):
 a,e,_,_,_,clock=tokens;pk=a._partition(ACCOUNT,'k1');other=a._partition('other-synthetic-account','k1')
 put_token(a,pk,'own',clock[0]+1000,clock[0])
 other_row=put_token(a,other,'other',clock[0]+1000,clock[0]);Checkpoint(a.ddb,'play-tokens',a.now).advance(other_row)
 saved=a._get('play-tokens',KEY);_,command,_=setup(tokens)
 bridge=TokenDeletion(a.ddb,authority_table='authority',ledger_table='deletion',environment='dev',keyring=a.s.hmac_keys,receipt_retention_seconds=120*86400,now=a.now,token_table='play-tokens')
 assert not bridge.delete_batch(command)['complete']
 assert bridge.delete_batch(command)['complete']
 assert a._get('play-tokens',KEY)==saved
 assert a._get('play-tokens',key(other,other_row['tokenDigest']))==other_row


def test_real_token_receipt_is_required_before_synthetic_identity_finalization(tokens):
 import boto3
 from shared_account_finalization.service import Finalizer,FinalizationError,REQUIRED_COMPONENTS,RETENTION_SECONDS
 from shared_play_lifecycle.bindings import Bindings
 from shared_check_authority.entitlements import EntitlementWriter
 a,e,_,_,_,clock=tokens;pk=a._partition(ACCOUNT,'k1');now=clock[0]
 Bindings(EntitlementWriter(a,approved_products=frozenset(),operator_principals=frozenset(),verification_max_age_seconds=20),'play-tokens').prepare(e)
 row=put_token(a,pk,'composed',now+1000,now)
 projected,_=page(a,'play-tokens',ACCOUNT,pk,None)
 assert {item['kind'] for item in projected}=={'google_play_preparation','google_play_verification'}
 _,command,_=setup(tokens)
 ledger=a.ddb.Table('deletion');manifest='a'*64
 ledger.put_item(Item={'PK':'INVENTORY#dev','SK':'ACCOUNT_DATA_INVENTORY','recordType':'ACCOUNT_DATA_INVENTORY','schemaVersion':1,'revision':1,'environment':'dev','coverage':'VERIFIED_COMPLETE','manifestSha256':manifest,'requiredComponents':list(REQUIRED_COMPONENTS),'usernameIsSubVerified':True,'approvedAtEpoch':now-1})
 # Other component proofs are synthetic preconditions, not their integration test.
 for component in REQUIRED_COMPONENTS:
  if component in ('PLAY_TOKENS','USER_PROFILE','IDENTITY'):continue
  ledger.put_item(Item={'PK':command['PK'],'SK':'ACCOUNT_DELETION#'+component,'schemaVersion':1,'recordVersion':1,'environment':'dev','eventType':'account.deletion.component.completed','component':component,'status':'COMPLETE','operationId':command['operationId'],'requestOccurredAtEpoch':now,'occurredAtEpoch':now,'retainUntilEpoch':now+RETENTION_SECONDS})
 class Identity:
  deletes=0
  def admin_get_user(self,**kwargs):return {'Username':ACCOUNT,'UserAttributes':[{'Name':'sub','Value':ACCOUNT}]}
  def admin_delete_user(self,**kwargs):self.deletes+=1
 identity=Identity()
 sys.path.insert(0,str(ROOT/'src/account_data_api'))
 from account_data_api.service import delete_user_profile_state
 users=a.ddb.Table('users')
 users.update_item(Key={'PK':'USER#'+ACCOUNT,'SK':'PROFILE'},UpdateExpression='SET deletionOperationId=:op, deletionRequestedAtEpoch=:at',ExpressionAttributeValues={':op':command['operationId'],':at':now})
 profile_kwargs=dict(users_table=users,ledger_table=ledger,policy_status='approved',now_epoch=now)
 blocked=delete_user_profile_state(command,**profile_kwargs)
 assert blocked['missingComponents']==['PLAY_TOKENS'] and not blocked['complete']
 assert a._get('users',{'PK':'USER#'+ACCOUNT,'SK':'PROFILE'}) is not None
 finalizer=Finalizer(ledger_table=ledger,ledger_table_name='deletion',client=boto3.client('dynamodb',region_name='us-east-1'),cognito=identity,user_pool_id='us-east-1_synthetic',environment='dev',now=a.now,enabled=True,manifest_sha256=manifest,inventory_revision=1)
 with pytest.raises(FinalizationError,match='COMPONENT_UNVERIFIED'):finalizer.finalize(command)
 assert identity.deletes==0
 bridge=TokenDeletion(a.ddb,authority_table='authority',ledger_table='deletion',environment='dev',keyring=a.s.hmac_keys,receipt_retention_seconds=RETENTION_SECONDS,now=a.now,token_table='play-tokens')
 assert not bridge.delete_batch(command)['complete']
 with pytest.raises(FinalizationError,match='COMPONENT_UNVERIFIED'):finalizer.finalize(command)
 assert bridge.delete_batch(command)['complete']
 assert a.ddb.Table('play-tokens').scan()['Items']==[]
 assert delete_user_profile_state(command,**profile_kwargs)['complete']
 assert a._get('users',{'PK':'USER#'+ACCOUNT,'SK':'PROFILE'}) is None
 assert finalizer.finalize(command)['complete'] and identity.deletes==1
 assert bridge.delete_batch(command)['alreadyComplete']
 assert finalizer.finalize(command)['alreadyComplete'] and identity.deletes==1
 assert a._get('deletion',{'PK':command['PK'],'SK':'ACCOUNT_DELETION'})['status']=='COMPLETE'


@pytest.mark.parametrize('change',['missing_reverse','mismatched_reverse'])
def test_broken_binding_pair_preserves_account_rows_and_blocks_erasure_receipt(tokens,change):
 from shared_check_authority.entitlements import EntitlementWriter
 from shared_play_lifecycle.bindings import Bindings,reverse_key
 from v1_play_handoff.service import account_binding
 a,e,_,_,_,clock=tokens;pk=a._partition(ACCOUNT,'k1')
 Bindings(EntitlementWriter(a,approved_products=frozenset(),operator_principals=frozenset(),verification_max_age_seconds=20),'play-tokens').prepare(e)
 target=reverse_key(account_binding(ACCOUNT))
 table=a.ddb.Table('play-tokens')
 if change=='missing_reverse':table.delete_item(Key=target)
 else:table.update_item(Key=target,UpdateExpression='SET accountId=:a',ExpressionAttributeValues={':a':'another-synthetic-account'})
 _,command,_=setup(tokens)
 before=table.scan()['Items']
 bridge=TokenDeletion(a.ddb,authority_table='authority',ledger_table='deletion',environment='dev',keyring=a.s.hmac_keys,receipt_retention_seconds=120*86400,now=a.now,token_table='play-tokens')
 with pytest.raises(AuthorityError):bridge.delete_batch(command)
 assert table.scan()['Items']==before
 assert a._get('deletion',{'PK':command['PK'],'SK':'ACCOUNT_DELETION#PLAY_TOKENS'}) is None
