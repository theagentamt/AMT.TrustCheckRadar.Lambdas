import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from shared_play_lifecycle.tokens import KmsTokenCipher,prepare_put,validate,key,load_owned,RETENTION_SECONDS
from shared_check_authority.core import AuthorityError
from shared_purchase_ownership.service import token_hash
from types import SimpleNamespace

ARN='arn:aws:kms:us-east-1:107827791950:key/12345678-1234-1234-1234-123456789abc'
PK='V1#k1#'+'a'*64
TOKEN='synthetic-purchase-token'
NOW=1800000000
class Kms:
 def __init__(self):self.calls=[];self.saved={}
 def encrypt(self,**kw):
  self.calls.append(('encrypt',kw));opaque=b'opaque-ciphertext-'+str(len(self.calls)).encode();self.saved[opaque]=(kw['Plaintext'],kw['EncryptionContext']);return {'KeyId':ARN,'CiphertextBlob':opaque}
 def decrypt(self,**kw):
  self.calls.append(('decrypt',kw));plain,context=self.saved[kw['CiphertextBlob']]
  assert context==kw['EncryptionContext'];return {'KeyId':ARN,'Plaintext':plain}
@pytest.fixture
def world():
 kms=Kms();cipher=KmsTokenCipher(kms,active_key_arn=ARN,readable_key_arns=frozenset({ARN}))
 action=prepare_put('authority',PK,TOKEN,access_until_epoch=NOW+100,next_attempt_epoch=NOW+30,now=NOW,cipher=cipher)
 return kms,cipher,action['Put']['Item']

def test_ciphertext_only_and_exact_context(world):
 kms,cipher,row=world
 assert TOKEN not in str(row) and row['expiresAt']==NOW+100+RETENTION_SECONDS
 assert cipher.decrypt(row,NOW)==TOKEN
 assert kms.calls[0][1]['EncryptionContext']==dict(purpose='google-play-reconciliation',environment='dev',accountPartition=PK,tokenDigest=token_hash(TOKEN))

def test_shorter_verified_end_shortens_retention_not_max_ever(world):
 kms,cipher,row=world
 action=prepare_put('authority',PK,TOKEN,access_until_epoch=NOW+10,next_attempt_epoch=NOW+2,now=NOW,cipher=cipher,observed=row)
 assert action['Put']['Item']['expiresAt']==NOW+10+RETENTION_SECONDS
 assert 'ciphertext' in action['Put']['ExpressionAttributeNames'].values() and action['Put']['Item']['revision']==2

@pytest.mark.parametrize('field,value',[('schemaVersion',True),('revision',0),('expiresAt',NOW+999999),('kmsKeyArn','arn:aws:kms:us-east-1:000000000000:key/12345678-1234-1234-1234-123456789abc'),('ciphertext','bad base64'),('GSI1PK','V1_EXPIRING')])
def test_malformed_storage_never_decrypts(world,field,value):
 kms,cipher,row=world;bad=row|{field:value};before=len(kms.calls)
 with pytest.raises(AuthorityError):cipher.decrypt(bad,NOW)
 assert len(kms.calls)==before

def test_logical_expiry_refuses_decrypt_before_ttl_removal(world):
 kms,cipher,row=world;before=len(kms.calls)
 with pytest.raises(AuthorityError):cipher.decrypt(row,row['expiresAt'])
 assert len(kms.calls)==before

def test_copy_to_other_account_context_cannot_decrypt(world):
 kms,cipher,row=world;other=row|{'PK':'V1#k1#'+'b'*64};other['GSI1SK']=f"{other['nextAttemptAtEpoch']:012d}#{other['PK']}#{other['SK']}"
 with pytest.raises(AuthorityError,match='PLAY_TOKEN_CIPHER_UNAVAILABLE'):cipher.decrypt(other,NOW)

def test_expired_shortened_proof_not_retained(world):
 kms,cipher,row=world;before=len(kms.calls)
 with pytest.raises(AuthorityError):prepare_put('authority',PK,TOKEN,access_until_epoch=NOW-RETENTION_SECONDS,next_attempt_epoch=NOW,now=NOW,cipher=cipher,observed=row)
 assert len(kms.calls)==before

@pytest.mark.parametrize('phase',['before','during','changed'])
def test_account_deletion_and_record_races_do_not_return_token(world,phase):
 kms,cipher,row=world
 class A:
  s=SimpleNamespace(active_key_id='k1',authority_table='authority')
  checks=0;reads=0
  def _assert_account(self,account):
   self.checks+=1
   if phase=='before' or phase=='during' and self.checks==2:raise AuthorityError('ACCOUNT_UNAVAILABLE')
  def _partition(self,*a):return PK
  def _get(self,*a):
   self.reads+=1;return row if self.reads==1 or phase!='changed' else row|{'revision':2}
  def now(self):return NOW
 with pytest.raises(AuthorityError):load_owned(A(),'synthetic-account',token_hash(TOKEN),cipher,token_table='play-tokens')
 if phase=='before':assert len(kms.calls)==1
