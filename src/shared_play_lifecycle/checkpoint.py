"""Five-minute operational position; no ciphertext/raw token; explicit policy gate.

The position contains pseudonymous keys, so it is erased with its account and
never described as anonymous. Each advancement proves its target still exists.
"""
import re
from .tokens import PARTITION
from shared_check_authority.core import AuthorityError,integral
from shared_check_authority.purchase_usage import exact_condition
KEY={'PK':'PLAY#CONTROL','SK':'LIFECYCLE_CURSOR'}
FIELDS={'PK','SK','schemaVersion','revision','cursor','expiresAt','scanStartedAtEpoch','lastFullPassAtEpoch'}


def validate(row,now):
    if row is None:return None
    cursor=row.get('cursor')
    if (set(row)!=FIELDS or any(row.get(k)!=v for k,v in KEY.items()) or integral(row.get('schemaVersion'))!=1
        or integral(row.get('revision')) is None or row['revision']<1
        or integral(row.get('expiresAt')) is None or not 0<row['expiresAt']<=now+300
        or integral(row.get('scanStartedAtEpoch')) is None or not 0<=row['scanStartedAtEpoch']<=now
        or row['lastFullPassAtEpoch'] is not None and (integral(row['lastFullPassAtEpoch']) is None or not 0<=row['lastFullPassAtEpoch']<=now)
        or cursor is not None and (not isinstance(cursor,dict) or set(cursor)!={'PK','SK','GSI1PK','GSI1SK'}
            or any(not isinstance(v,str) or len(v)>512 for v in cursor.values()) or cursor['GSI1PK']!='V1_PLAY_RECONCILE'
            or not PARTITION.fullmatch(cursor['PK']) or not re.fullmatch(r'PLAY_TOKEN#[a-f0-9]{64}',cursor['SK'])
            or not re.fullmatch(r'[0-9]{12}#'+re.escape(cursor['PK'])+'#'+re.escape(cursor['SK']),cursor['GSI1SK']))):
        raise AuthorityError('PLAY_CHECKPOINT_INVALID')
    return row


class Checkpoint:
    def __init__(self,resource,table,now):
        self.ddb,self.table,self.now=resource,table,now
        self.saved=validate(resource.Table(table).get_item(Key=KEY,ConsistentRead=True).get('Item'),now())
        self.cursor=self.saved['cursor'] if self.saved and now()<self.saved['expiresAt'] else None
        self.started=int(self.saved['scanStartedAtEpoch']) if self.saved and self.cursor else now()
        self.last=self.saved['lastFullPassAtEpoch'] if self.saved else None

    def advance(self,row):
        cursor={k:row[k] for k in ('PK','SK','GSI1PK','GSI1SK')}
        self._write(cursor,[{'ConditionCheck':{'TableName':self.table,'Key':{k:row[k] for k in ('PK','SK')},'ConditionExpression':'attribute_exists(PK)'}}])

    def finish(self):
        self.last=self.now();self.started=self.now();self._write(None,[])

    def _write(self,cursor,guards):
        value=KEY|{'schemaVersion':1,'revision':1 if self.saved is None else int(self.saved['revision'])+1,
            'cursor':cursor,'expiresAt':self.now()+300,'scanStartedAtEpoch':self.started,'lastFullPassAtEpoch':self.last}
        validate(value,self.now())
        condition=exact_condition(self.saved) if self.saved else {'ConditionExpression':'attribute_not_exists(PK)'}
        self.ddb.meta.client.transact_write_items(TransactItems=guards+[{'Put':{'TableName':self.table,'Item':value,**condition}}])
        self.saved,self.cursor=value,cursor
