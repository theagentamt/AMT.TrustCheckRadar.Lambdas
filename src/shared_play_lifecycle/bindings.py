"""Account-lifetime reverse billing identity, never an entitlement or token cache."""
import re
from shared_check_authority.core import AuthorityError, integral, SUBJECT_PATTERN
from shared_check_authority.purchase_usage import exact_condition
from v1_play_handoff.service import account_binding
from .tokens import PARTITION

REVERSE_FIELDS={'PK','SK','recordType','schemaVersion','accountId','accountPartition','bindingHash','createdAtEpoch'}
LOCATOR_FIELDS={'PK','SK','recordType','schemaVersion','bindingHash','createdAtEpoch'}


def require(value):
    if not value:raise AuthorityError('PLAY_BINDING_INVALID')


def reverse_key(binding):
    require(isinstance(binding,str) and re.fullmatch(r'[0-9a-f]{64}',binding))
    return {'PK':'PLAY_BINDING#'+binding,'SK':'ACCOUNT'}


def locator_key(partition):
    require(isinstance(partition,str) and PARTITION.fullmatch(partition))
    return {'PK':partition,'SK':'PLAY_BINDING'}


def validate_pair(reverse,locator,*,account=None,partition=None):
    require(type(reverse) is dict and set(reverse)==REVERSE_FIELDS and type(locator) is dict and set(locator)==LOCATOR_FIELDS)
    require(isinstance(reverse['accountId'],str) and SUBJECT_PATTERN.fullmatch(reverse['accountId']))
    require(reverse['bindingHash']==account_binding(reverse['accountId'])
            and all(reverse.get(k)==v for k,v in reverse_key(reverse['bindingHash']).items())
            and all(locator.get(k)==v for k,v in locator_key(reverse['accountPartition']).items())
            and reverse['recordType']=='PLAY_ACCOUNT_BINDING' and locator['recordType']=='PLAY_ACCOUNT_BINDING_LOCATOR'
            and integral(reverse['schemaVersion'])==1 and integral(locator['schemaVersion'])==1
            and integral(reverse['createdAtEpoch']) is not None and reverse['createdAtEpoch']>0
            and integral(locator['createdAtEpoch']) is not None and locator['createdAtEpoch']==reverse['createdAtEpoch'] and locator['bindingHash']==reverse['bindingHash']
            and (account is None or reverse['accountId']==account)
            and (partition is None or reverse['accountPartition']==partition))
    return reverse,locator


def conditions(table,reverse,locator):
    validate_pair(reverse,locator)
    return [{'ConditionCheck':{'TableName':table,'Key':{k:row[k] for k in ('PK','SK')},**exact_condition(row)}} for row in (reverse,locator)]


class Bindings:
    def __init__(self,writer,table):
        require(isinstance(table,str) and table and table!=writer.a.s.authority_table)
        self.w,self.a,self.table=writer,writer.a,table

    def prepare(self,event):
        account=self.a._account(event)
        self.a._device(event,account)
        partition=self.a._partition(account,self.a.s.active_key_id);binding=account_binding(account)
        reverse=self.a._get(self.table,reverse_key(binding));locator=self.a._get(self.table,locator_key(partition))
        if reverse is not None or locator is not None:
            validate_pair(reverse,locator,account=account,partition=partition)
            self.a._transact(self.a._account_conditions(account)+self.w._device_conditions(event,account)+conditions(self.table,reverse,locator))
        else:
            # Retained-key rotation cannot create a second untracked mapping.
            for old in self.a.deletion_partitions(account):
                if old!=partition and self.a._get(self.table,locator_key(old)) is not None:
                    raise AuthorityError('AUTHORITY_MIGRATION_REQUIRED')
            now=self.a.now()
            reverse=reverse_key(binding)|{'recordType':'PLAY_ACCOUNT_BINDING','schemaVersion':1,'accountId':account,
                'accountPartition':partition,'bindingHash':binding,'createdAtEpoch':now}
            locator=locator_key(partition)|{'recordType':'PLAY_ACCOUNT_BINDING_LOCATOR','schemaVersion':1,'bindingHash':binding,'createdAtEpoch':now}
            validate_pair(reverse,locator,account=account,partition=partition)
            actions=[{'Put':{'TableName':self.table,'Item':row,'ConditionExpression':'attribute_not_exists(PK)'}} for row in (reverse,locator)]
            self.a._transact(self.a._account_conditions(account)+self.w._device_conditions(event,account)+actions)
        return {'schemaVersion':1,'contractVersion':'v1-play-preparation-1.0.0-candidate.1','accountBinding':binding,'prepared':True}

    def resolve(self,binding):
        reverse=self.a._get(self.table,reverse_key(binding))
        if reverse is None:return None
        partition=reverse.get('accountPartition');locator=self.a._get(self.table,locator_key(partition))
        validate_pair(reverse,locator)
        account=reverse['accountId'];self.a._assert_account(account)
        require(partition in self.a.deletion_partitions(account))
        if self.a._get(self.table,reverse_key(binding))!=reverse or self.a._get(self.table,locator_key(partition))!=locator:
            raise AuthorityError('PLAY_BINDING_CHANGED')
        return account,reverse,locator
