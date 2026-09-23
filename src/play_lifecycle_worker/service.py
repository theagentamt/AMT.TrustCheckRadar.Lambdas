"""Bounded due traversal; KEYS_ONLY index is discovery, strong rows are authority."""
import hashlib
from shared_check_authority.core import AuthorityError
from shared_check_authority.purchase_usage import exact_condition
from shared_purchase_ownership.service import owner_key
from shared_play_lifecycle.tokens import validate,key,retry_action,load_owned


class Worker:
    def __init__(self,resource,table,*,now,remaining_ms,reconciler_factory,lifecycle_enabled):
        self.ddb,self.table,self.now,self.remaining=resource,table,now,remaining_ms
        self.factory,self.enabled=reconciler_factory,lifecycle_enabled

    def run(self):
        counts=dict(heartbeat=1,examined=0,reconciled=0,ackPending=0,expiredDeleted=0,failed=0,unresolved=0,exhausted=0,oldestDueSeconds=0)
        from shared_play_lifecycle.checkpoint import Checkpoint
        checkpoint=Checkpoint(self.ddb,self.table,self.now)
        cursor=checkpoint.cursor;service=None;provider_attempted=False
        stop=False
        try:
            for _ in range(10):
                if self.remaining()<5000:break
                query={'IndexName':'GSI1','KeyConditionExpression':'GSI1PK = :pk AND GSI1SK <= :end',
                    'ExpressionAttributeValues':{':pk':'V1_PLAY_RECONCILE',':end':f'{self.now():012d}#\uffff'},'Limit':5}
                if cursor:query['ExclusiveStartKey']=cursor
                page=self.ddb.Table(self.table).query(**query)
                for pointer in page.get('Items',[]):
                    if self.remaining()<5000:stop=True;break
                    row=None
                    try:
                        target={k:pointer[k] for k in ('PK','SK')}
                        row=self.ddb.Table(self.table).get_item(Key=target,ConsistentRead=True).get('Item')
                        if row is None:continue
                        checkpoint.advance(pointer)
                        validate(row,target,self.now(),allow_expired=True)
                        if row['nextAttemptAtEpoch']>self.now():continue
                        counts['examined']+=1;counts['oldestDueSeconds']=max(counts['oldestDueSeconds'],self.now()-int(row['nextAttemptAtEpoch']))
                        if self.now()>=row['expiresAt']:
                            self.ddb.meta.client.transact_write_items(TransactItems=[{'Delete':{'TableName':self.table,'Key':target,**exact_condition(row)}}])
                            counts['expiredDeleted']+=1;continue
                        if not self.enabled or provider_attempted or self.remaining()<24000:continue
                        provider_attempted=True;service=self.factory()
                        owner=service.ownership._get(owner_key(row['tokenDigest']))
                        account=owner.get('accountId') if owner else None
                        if account not in service.allowed_subjects:raise AuthorityError('LIFECYCLE_OBSERVATION_REQUIRED')
                        if service.a._partition(account,service.a.s.active_key_id)!=row['PK']:raise AuthorityError('LIFECYCLE_OBSERVATION_REQUIRED')
                        token,observed=load_owned(service.a,account,row['tokenDigest'],service.cipher,token_table=self.table)
                        if observed!=row:raise AuthorityError('PLAY_TOKEN_OBSERVATION_CHANGED')
                        operation=hashlib.sha256(('scheduled\0'+row['PK']+'\0'+row['SK']+'\0'+str(row['revision'])).encode()).hexdigest()
                        result=service.refresh(token,operation)
                        if result['state']!='committed':counts['unresolved']+=1;self._retry(row,counts)
                        else:
                            counts['reconciled']+=1;counts['ackPending']+=int(result['acknowledgment']=='pending')
                    except Exception:
                        counts['failed']+=1
                        if row is not None:
                            try:self._retry(row,counts)
                            except Exception:pass
                if stop:break
                cursor=page.get('LastEvaluatedKey')
                if not cursor:checkpoint.finish();break
            return counts
        finally:
            if service is not None:
                service.client.close();service.cipher.client.close()

    def _retry(self,row,counts):
        action=retry_action(self.table,row,now=self.now())
        self.ddb.meta.client.transact_write_items(TransactItems=[action])
        counts['exhausted']+=int(action.get('Put',{}).get('Item',{}).get('lastOutcome')=='exhausted')
