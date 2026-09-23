"""Explicit token/mapping erasure; no decrypt, provider call or identity deletion."""
from shared_check_authority.deletion import AuthorityDeletion
from shared_check_authority.core import AuthorityError,integral
from shared_check_authority.purchase_usage import exact_condition
from shared_account_finalization.service import completed_fence
from .tokens import validate as validate_token,key as token_key
from .bindings import reverse_key,locator_key,validate_pair
from v1_play_handoff.service import account_binding

COMPONENT='PLAY_TOKENS'


class TokenDeletion(AuthorityDeletion):
    def __init__(self,*args,token_table,**kwargs):
        super().__init__(*args,**kwargs)
        if not isinstance(token_table,str) or not token_table or token_table==self.table:
            raise AuthorityError('PLAY_TOKEN_CONFIGURATION_REQUIRED')
        self.token_table=token_table

    def _receipt(self,command):
        now=self.now()
        return {'PK':command['PK'],'SK':'ACCOUNT_DELETION#'+COMPONENT,'schemaVersion':1,'recordVersion':1,
                'environment':self.environment,'eventType':'account.deletion.component.completed','component':COMPONENT,
                'status':'COMPLETE','operationId':command['operationId'],'requestOccurredAtEpoch':command['occurredAtEpoch'],
                'occurredAtEpoch':now,'retainUntilEpoch':now+self.retention}

    def delete_batch(self,command,*,page_size=20,max_pages=2,can_continue=lambda:True):
        if type(page_size) is not int or not 1<=page_size<=20 or type(max_pages) is not int or not 1<=max_pages<=4:
            raise AuthorityError('DELETION_CONFIGURATION_UNAVAILABLE')
        current=self._get(self.ledger,{'PK':command.get('PK'),'SK':'ACCOUNT_DELETION'}) if isinstance(command,dict) else None
        if completed_fence(current,self.environment,self.now(),None if current==command else command):
            return {'deleted':0,'complete':True,'alreadyComplete':True}
        account=self._command(command);inventory=self._inventory()
        receipt_key={'PK':command['PK'],'SK':'ACCOUNT_DELETION#'+COMPONENT}
        receipt=self._get(self.ledger,receipt_key)
        if receipt is not None:
            expected=self._receipt(command)
            if (set(receipt)!=set(expected) or any(receipt.get(k)!=v for k,v in expected.items() if k not in ('occurredAtEpoch','retainUntilEpoch'))
                or integral(receipt.get('schemaVersion'))!=1 or integral(receipt.get('recordVersion'))!=1
                or integral(receipt.get('requestOccurredAtEpoch'))!=command['occurredAtEpoch']
                or integral(receipt.get('occurredAtEpoch')) is None or not command['occurredAtEpoch']<=receipt['occurredAtEpoch']<=self.now()
                or integral(receipt.get('retainUntilEpoch'))!=receipt['occurredAtEpoch']+self.retention):
                raise AuthorityError('DELETION_RECEIPT_INVALID')
            return {'deleted':0,'complete':True,'alreadyComplete':True}
        # At most four retained namespaces, one bounded page each. Return after
        # the first mutation; empty namespaces must not starve later keys.
        deleted=pages=0
        for kid in sorted(self.keys):
            if not can_continue():return {'deleted':deleted,'complete':False,'alreadyComplete':False}
            partition=self._partition(account,kid)
            page=self.ddb.Table(self.token_table).query(KeyConditionExpression='PK = :pk',ExpressionAttributeValues={':pk':partition},ConsistentRead=True,Limit=page_size)
            pages+=1;rows=page.get('Items',[])
            if rows:
                actions=self._guards(command,inventory)
                for row in rows:
                    if row.get('SK','').startswith('PLAY_TOKEN#'):
                        validate_token(row,token_key(partition,row.get('tokenDigest')),self.now(),allow_expired=True)
                    elif row.get('SK')=='PLAY_BINDING':
                        reverse=self._get(self.token_table,reverse_key(row.get('bindingHash')))
                        validate_pair(reverse,row,account=account,partition=partition)
                        actions.append({'Delete':{'TableName':self.token_table,'Key':reverse_key(row['bindingHash']),**exact_condition(reverse)}})
                    else:raise AuthorityError('PLAY_TOKEN_INVENTORY_UNAVAILABLE')
                    actions.append({'Delete':{'TableName':self.token_table,'Key':{k:row[k] for k in ('PK','SK')},**exact_condition(row)}})
                self._transact(actions);deleted+=len(rows)
                # A subsequent empty strong pass, under the same fixed fence,
                # proves completion. Never infer it from a bounded deleted page.
                return {'deleted':deleted,'complete':False,'alreadyComplete':False}
        if self._get(self.token_table,reverse_key(account_binding(account))) is not None:
            raise AuthorityError('PLAY_TOKEN_INVENTORY_UNAVAILABLE')
        from .checkpoint import KEY as CHECKPOINT_KEY,validate as validate_checkpoint
        cursor=validate_checkpoint(self._get(self.token_table,CHECKPOINT_KEY),self.now())
        extra=[]
        if cursor and cursor['cursor'] is not None and cursor['cursor']['PK'] in {self._partition(account,kid) for kid in self.keys}:
            extra.append({'Delete':{'TableName':self.token_table,'Key':CHECKPOINT_KEY,**exact_condition(cursor)}})
        self._transact(self._guards(command,inventory)+extra+[{'Put':{'TableName':self.ledger,'Item':self._receipt(command),'ConditionExpression':'attribute_not_exists(PK)'}}])
        return {'deleted':0,'complete':True,'alreadyComplete':False}
