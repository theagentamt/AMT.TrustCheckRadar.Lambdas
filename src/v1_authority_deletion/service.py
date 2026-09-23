"""Bounded stream work and fair scheduled continuation; no provider permissions."""
from shared_check_authority.core import AuthorityError, integral, SUBJECT_PATTERN

CURSOR_KEY={'PK':'V1#CONTROL','SK':'DELETION_RECONCILIATION_CURSOR'}


class DeletionWorker:
    def __init__(self,bridge,*,stream_arn,remaining_ms,allowed_subjects,cursor_key=None):
        if not isinstance(allowed_subjects,frozenset) or not allowed_subjects or any(not isinstance(x,str) or not SUBJECT_PATTERN.fullmatch(x) for x in allowed_subjects):raise AuthorityError('DELETION_CONFIGURATION_UNAVAILABLE')
        self.cursor_key=CURSOR_KEY if cursor_key is None else cursor_key
        if self.cursor_key not in (CURSOR_KEY,{'PK':'PLAY#CONTROL','SK':'TOKEN_DELETION_CURSOR'}):raise AuthorityError('DELETION_CONFIGURATION_UNAVAILABLE')
        self.allowed_subjects=allowed_subjects
        self.bridge,self.stream_arn,self.remaining_ms=bridge,stream_arn,remaining_ms

    def _overdue(self,command):
        deadline=integral(command.get('deleteByEpoch')) if isinstance(command,dict) else None
        return int(deadline is not None and deadline<self.bridge.now())

    def _run(self,command):
        return self.bridge.delete_batch(command,page_size=20,max_pages=2,can_continue=lambda:self.remaining_ms()>=5000)

    def stream(self,event):
        from boto3.dynamodb.types import TypeDeserializer
        records=event.get('Records')
        if not isinstance(records,list) or not 1<=len(records)<=5:raise AuthorityError('DELETION_EVENT_INVALID')
        failures=[];counts={'examined':0,'completed':0,'pending':0,'failed':0,'deleted':0,'overdue':0,'skipped':0}
        decoder=TypeDeserializer()
        for record in records:
            sequence=record.get('dynamodb',{}).get('SequenceNumber') if isinstance(record,dict) else None
            if not isinstance(sequence,str) or not sequence.isdigit():raise AuthorityError('DELETION_EVENT_INVALID')
            try:
                if record.get('eventSource')!='aws:dynamodb' or record.get('eventSourceARN')!=self.stream_arn or record.get('eventName') not in ('INSERT','MODIFY'):raise AuthorityError('DELETION_EVENT_INVALID')
                if self.remaining_ms()<10000:raise AuthorityError('DELETION_DEADLINE')
                image=record['dynamodb']['NewImage']
                command={k:decoder.deserialize(v) for k,v in image.items()}
                if command.get('SK')!='ACCOUNT_DELETION':continue
                counts['examined']+=1
                if command.get('accountId') not in self.allowed_subjects:
                    counts['skipped']+=1;continue
                result=self._run(command)
                counts['deleted']+=result['deleted']
                counts['overdue']+=self._overdue(command) if not result['alreadyComplete'] else 0
                counts['completed' if result['complete'] else 'pending']+=1
                # Incomplete work is durable in the source command/progress and
                # retried both by stream partial-batch retry and reconciliation.
                if not result['complete']:failures.append({'itemIdentifier':sequence})
            except Exception:
                counts['failed']+=1;failures.append({'itemIdentifier':sequence})
        return {'batchItemFailures':failures},counts

    def reconcile(self):
        resource=self.bridge.ddb;table=resource.Table(self.bridge.ledger)
        saved=table.get_item(Key=self.cursor_key,ConsistentRead=True).get('Item')
        if saved is not None and (set(saved)!={'PK','SK','revision','cursor','scanStartedAtEpoch','lastFullPassAtEpoch'} or integral(saved.get('revision')) is None or saved['revision']<1):raise AuthorityError('DELETION_CURSOR_INVALID')
        revision=saved['revision'] if saved else 0
        cursor=saved.get('cursor') if saved else None
        now=self.bridge.now()
        started=saved['scanStartedAtEpoch'] if saved else now
        last_pass=saved['lastFullPassAtEpoch'] if saved else None
        if integral(started) is None or started>now or (last_pass is not None and (integral(last_pass) is None or last_pass>now)):
            raise AuthorityError('DELETION_CURSOR_INVALID')
        if cursor is not None and (not isinstance(cursor,dict) or set(cursor)!={'PK','SK'} or any(not isinstance(v,str) for v in cursor.values())):raise AuthorityError('DELETION_CURSOR_INVALID')
        counts={'examined':0,'completed':0,'pending':0,'failed':0,'deleted':0,'overdue':0,'skipped':0,'pages':0,'fullPassCompleted':0,'fullPassAgeSeconds':now-(last_pass if last_pass is not None else started)}
        stop=False
        for _ in range(2):
            if self.remaining_ms()<10000:break
            args={'Limit':25,'ConsistentRead':True}
            if cursor:args['ExclusiveStartKey']=cursor
            page=table.scan(**args);counts['pages']+=1
            for command in page.get('Items',[]):
                if self.remaining_ms()<10000 or counts['examined']>=5:
                    stop=True;break
                cursor={'PK':command['PK'],'SK':command['SK']}
                if command.get('SK')!='ACCOUNT_DELETION':continue
                counts['examined']+=1
                if command.get('accountId') not in self.allowed_subjects:
                    counts['skipped']+=1;continue
                try:
                    result=self._run(command);counts['deleted']+=result['deleted']
                    counts['overdue']+=self._overdue(command) if not result['alreadyComplete'] else 0
                    counts['completed' if result['complete'] else 'pending']+=1
                except Exception:
                    counts['failed']+=1;counts['overdue']+=self._overdue(command)
            if stop:break
            cursor=page.get('LastEvaluatedKey')
            if cursor is None:
                counts['fullPassCompleted']=1;counts['fullPassAgeSeconds']=0
                last_pass=now;started=now
                break
        # Advance after failed/poison commands; next full scan retries them.
        # CAS prevents concurrent invocations silently overwriting progress.
        operation={'TableName':self.bridge.ledger,'Key':self.cursor_key,
            'UpdateExpression':'SET revision = :next, #cursor = :cursor, scanStartedAtEpoch = :started, lastFullPassAtEpoch = :last',
            'ConditionExpression':'attribute_not_exists(revision)' if revision==0 else 'revision = :old',
            'ExpressionAttributeNames':{'#cursor':'cursor'},'ExpressionAttributeValues':{':next':revision+1,':cursor':cursor,':started':started,':last':last_pass}}
        if revision:operation['ExpressionAttributeValues'][':old']=revision
        self.bridge._transact([{'Update':operation}])
        return counts
