"""Purpose-limited encrypted Play proof in a dedicated store without backups.

Only transaction preparation is provided. The trusted caller must combine exact
account/deletion/ownership/provider observation guards before executing a Put.
No raw proof belongs in a request receipt, audit, log, queue or usage ledger.
"""
import base64
import binascii
import re
from shared_check_authority.core import AuthorityError, integral
from shared_check_authority.purchase_usage import exact_condition
from shared_purchase_ownership.service import token_hash, OwnershipError

RETENTION_SECONDS=604800
KEY_ARN=re.compile(r'arn:aws:kms:us-east-1:107827791950:key/[0-9a-f-]{36}')
PARTITION=re.compile(r'V1#[A-Za-z0-9]{1,8}#[0-9a-f]{64}')
FIELDS={'PK','SK','recordType','schemaVersion','tokenDigest','kmsKeyArn','ciphertext',
        'verifiedAccessUntilEpoch','expiresAt','nextAttemptAtEpoch','revision','GSI1PK','GSI1SK'}


def require(ok):
    if not ok:raise AuthorityError('PLAY_TOKEN_STATE_INVALID')


def key(partition,digest):
    require(isinstance(partition,str) and PARTITION.fullmatch(partition)
            and isinstance(digest,str) and re.fullmatch(r'[0-9a-f]{64}',digest))
    return {'PK':partition,'SK':'PLAY_TOKEN#'+digest}


def context(partition,digest):
    key(partition,digest)
    return {'purpose':'google-play-reconciliation','environment':'dev','accountPartition':partition,'tokenDigest':digest}


def validate(row,expected_key,now,*,allow_expired=False):
    require(isinstance(row,dict) and set(row)==FIELDS and all(row.get(k)==v for k,v in expected_key.items()))
    key(row['PK'],row['tokenDigest'])
    require(row['SK']=='PLAY_TOKEN#'+row['tokenDigest'] and row['recordType']=='V1_PLAY_TOKEN'
            and integral(row['schemaVersion'])==1 and integral(row['revision']) is not None and row['revision']>=1
            and all(integral(row[k]) is not None for k in ('verifiedAccessUntilEpoch','expiresAt','nextAttemptAtEpoch'))
            and 0<row['verifiedAccessUntilEpoch'] and row['expiresAt']==row['verifiedAccessUntilEpoch']+RETENTION_SECONDS
            and 0<=row['nextAttemptAtEpoch']<=row['expiresAt'] and type(now) is int and now>=0
            and (allow_expired or now<row['expiresAt'])
            and row['GSI1PK']=='V1_PLAY_RECONCILE'
            and row['GSI1SK']==f"{int(row['nextAttemptAtEpoch']):012d}#{row['PK']}#{row['SK']}"
            and isinstance(row['kmsKeyArn'],str) and KEY_ARN.fullmatch(row['kmsKeyArn'])
            and isinstance(row['ciphertext'],str) and 1<=len(row['ciphertext'])<=8192)
    try:
        decoded=base64.b64decode(row['ciphertext'],validate=True)
        require(1<=len(decoded)<=6144 and base64.b64encode(decoded).decode()==row['ciphertext'])
    except (ValueError,binascii.Error):raise AuthorityError('PLAY_TOKEN_STATE_INVALID') from None
    return row


class KmsTokenCipher:
    """Exact Dev key and authenticated context; SDK retries/budgets are caller-owned."""
    def __init__(self,client,*,active_key_arn,readable_key_arns):
        require(isinstance(active_key_arn,str) and KEY_ARN.fullmatch(active_key_arn)
                and isinstance(readable_key_arns,frozenset) and active_key_arn in readable_key_arns
                and 1<=len(readable_key_arns)<=4
                and all(isinstance(x,str) and KEY_ARN.fullmatch(x) for x in readable_key_arns))
        self.client,self.active,self.readable=client,active_key_arn,readable_key_arns

    def encrypt(self,token,partition):
        try:digest=token_hash(token)
        except OwnershipError:raise AuthorityError('PLAY_TOKEN_STATE_INVALID') from None
        try:
            result=self.client.encrypt(KeyId=self.active,Plaintext=token.encode(),EncryptionAlgorithm='SYMMETRIC_DEFAULT',EncryptionContext=context(partition,digest))
            require(result.get('KeyId')==self.active and isinstance(result.get('CiphertextBlob'),bytes) and 1<=len(result['CiphertextBlob'])<=6144)
            return self.active,base64.b64encode(result['CiphertextBlob']).decode()
        except AuthorityError:raise
        except Exception:raise AuthorityError('PLAY_TOKEN_CIPHER_UNAVAILABLE') from None

    def decrypt(self,row,now):
        validate(row,key(row.get('PK'),row.get('tokenDigest')),now)
        require(row['kmsKeyArn'] in self.readable)
        try:
            result=self.client.decrypt(KeyId=row['kmsKeyArn'],CiphertextBlob=base64.b64decode(row['ciphertext']),EncryptionAlgorithm='SYMMETRIC_DEFAULT',EncryptionContext=context(row['PK'],row['tokenDigest']))
            require(result.get('KeyId')==row['kmsKeyArn'] and isinstance(result.get('Plaintext'),bytes))
            token=result['Plaintext'].decode('utf-8');require(token_hash(token)==row['tokenDigest'])
            return token
        except AuthorityError:raise
        except Exception:raise AuthorityError('PLAY_TOKEN_CIPHER_UNAVAILABLE') from None


def prepare_put(table,partition,token,*,access_until_epoch,next_attempt_epoch,now,cipher,observed=None):
    """Exact latest verified end; never max-ever or notification arrival time.

    A fresh authoritative shorter end shortens retention too. If already expired,
    refuse to retain rather than quietly extending it. A worker must handle that
    explicit erasure/reconciliation state before claiming lifecycle completion.
    """
    try:digest=token_hash(token)
    except OwnershipError:raise AuthorityError('PLAY_TOKEN_STATE_INVALID') from None
    target=key(partition,digest)
    require(type(access_until_epoch) is int and access_until_epoch>0 and type(now) is int
            and type(next_attempt_epoch) is int and now<=next_attempt_epoch<=access_until_epoch+RETENTION_SECONDS
            and now<access_until_epoch+RETENTION_SECONDS)
    if observed is not None:validate(observed,target,now,allow_expired=True)
    arn,ciphertext=cipher.encrypt(token,partition)
    row=target|{'recordType':'V1_PLAY_TOKEN','schemaVersion':1,'tokenDigest':digest,'kmsKeyArn':arn,'ciphertext':ciphertext,
        'verifiedAccessUntilEpoch':access_until_epoch,'expiresAt':access_until_epoch+RETENTION_SECONDS,
        'nextAttemptAtEpoch':next_attempt_epoch,'revision':1 if observed is None else int(observed['revision'])+1,
        'GSI1PK':'V1_PLAY_RECONCILE','GSI1SK':f"{next_attempt_epoch:012d}#{partition}#{target['SK']}"}
    validate(row,target,now)
    condition={'ConditionExpression':'attribute_not_exists(PK)'} if observed is None else exact_condition(observed)
    return {'Put':{'TableName':table,'Item':row,**condition}}


def load_owned(authority,account,digest,cipher,*,token_table):
    require(isinstance(token_table,str) and token_table and token_table != authority.s.authority_table)
    authority._assert_account(account)
    partition=authority._partition(account,authority.s.active_key_id)
    row=authority._get(token_table,key(partition,digest))
    validate(row,key(partition,digest),authority.now())
    token=cipher.decrypt(row,authority.now())
    authority._assert_account(account)
    if authority._get(token_table,key(partition,digest))!=row:
        raise AuthorityError('PLAY_TOKEN_OBSERVATION_CHANGED')
    return token,row
