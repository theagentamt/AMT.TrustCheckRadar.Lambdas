"""Unwired V1_AUTHORITY deletion component; never completes legacy ENTITLEMENTS.

Key inventory must be an independently verified, append-only history of all issued
key IDs/material fingerprints. This helper cannot discover partitions for lost
keys and refuses incomplete/mismatched inventories. No runtime handler uses it.
"""
import hashlib
import hmac
import re
import uuid
from .core import AuthorityError, integral, SUBJECT_PATTERN, PAID_COMPLETED_CHECKS

COMMAND_FIELDS={'PK','SK','schemaVersion','recordVersion','environment','eventType','accountId','operationId','status','occurredAtEpoch','deleteByEpoch'}
INVENTORY_KEY={'PK':'V1#CONTROL','SK':'HMAC_KEY_INVENTORY'}
COMPONENT='V1_AUTHORITY'


class AuthorityDeletion:
    def __init__(self,resource,*,authority_table,ledger_table,environment,keyring,receipt_retention_seconds,now):
        # Existing account-deletion receipt format requires exactly 120 days.
        # Required explicit input is compatibility validation, not policy approval.
        if environment!='dev' or type(receipt_retention_seconds) is not int or receipt_retention_seconds!=120*86400:
            raise AuthorityError('DELETION_CONFIGURATION_UNAVAILABLE')
        if not isinstance(keyring,dict) or not 1<=len(keyring)<=4 or any(not isinstance(k,str) or not re.fullmatch('[A-Za-z0-9]{1,8}',k) or not isinstance(v,bytes) or len(v)<32 for k,v in keyring.items()):
            raise AuthorityError('DELETION_KEY_INVENTORY_UNAVAILABLE')
        self.ddb,self.client=resource,resource.meta.client
        if self.client.meta.config.retries.get('total_max_attempts')!=1:
            raise AuthorityError('SDK_RETRY_CONFIGURATION_UNAVAILABLE')
        self.table,self.ledger,self.environment,self.keys,self.retention,self.now=authority_table,ledger_table,environment,dict(keyring),receipt_retention_seconds,now

    def _get(self,table,key):return self.ddb.Table(table).get_item(Key=key,ConsistentRead=True).get('Item')

    def _command(self,command):
        try:
            account=command.get('accountId');epoch=integral(command.get('occurredAtEpoch'))
            operation=uuid.UUID(command.get('operationId'))
            valid=(set(command)==COMMAND_FIELDS and isinstance(account,str) and SUBJECT_PATTERN.fullmatch(account)
                and command['PK']=='ACCOUNT#'+account and command['SK']=='ACCOUNT_DELETION'
                and integral(command['schemaVersion'])==1 and integral(command['recordVersion'])==1
                and command['environment']==self.environment and command['eventType']=='account.deletion.requested'
                and command['status']=='REQUESTED' and operation.version==4 and str(operation)==command['operationId']
                and epoch is not None and epoch>=0 and integral(command['deleteByEpoch'])==epoch+86400)
        except (AttributeError,TypeError,ValueError,KeyError):valid=False
        if not valid:raise AuthorityError('DELETION_COMMAND_INVALID')
        actual=self._get(self.ledger,{'PK':command['PK'],'SK':command['SK']})
        if actual!=command:raise AuthorityError('DELETION_COMMAND_UNVERIFIED')
        return account

    def _inventory(self):
        row=self._get(self.table,INVENTORY_KEY)
        fingerprints={k:hashlib.sha256(v).hexdigest() for k,v in self.keys.items()}
        if not row or set(row)!={'PK','SK','recordType','schemaVersion','revision','coverage','issuedKeys'} or row.get('recordType')!='V1_HMAC_KEY_INVENTORY' or integral(row.get('schemaVersion'))!=1 or integral(row.get('revision')) is None or row['revision']<1 or row.get('coverage')!='VERIFIED_COMPLETE' or row.get('issuedKeys')!=fingerprints:
            raise AuthorityError('DELETION_KEY_INVENTORY_UNAVAILABLE')
        return row

    def _partition(self,account,keyid):
        value=hmac.new(self.keys[keyid],('account\0'+account).encode(),hashlib.sha256).hexdigest()
        return 'V1#'+keyid+'#'+value

    def _guards(self,command,inventory):
        return [
            {'ConditionCheck':{'TableName':self.ledger,'Key':{'PK':command['PK'],'SK':'ACCOUNT_DELETION'},
                'ConditionExpression':'operationId = :operation AND occurredAtEpoch = :epoch AND #state = :requested AND eventType = :event AND accountId = :account AND environment = :environment AND schemaVersion = :schema AND recordVersion = :schema AND deleteByEpoch = :deadline',
                'ExpressionAttributeNames':{'#state':'status'},'ExpressionAttributeValues':{':operation':command['operationId'],':epoch':command['occurredAtEpoch'],':requested':'REQUESTED',':event':'account.deletion.requested',':account':command['accountId'],':environment':self.environment,':schema':1,':deadline':command['deleteByEpoch']}}},
            {'ConditionCheck':{'TableName':self.table,'Key':INVENTORY_KEY,
                'ConditionExpression':'revision = :revision AND coverage = :coverage AND issuedKeys = :keys AND recordType = :type AND schemaVersion = :schema',
                'ExpressionAttributeValues':{':revision':inventory['revision'],':coverage':'VERIFIED_COMPLETE',':keys':inventory['issuedKeys'],':type':'V1_HMAC_KEY_INVENTORY',':schema':1}}}]

    def _transact(self,items):
        try:self.client.transact_write_items(TransactItems=items)
        except Exception:raise AuthorityError('DELETION_TRANSACTION_UNCERTAIN') from None

    def _access_guard(self,partition,operation,exists):
        data={'TableName':self.table,'Key':{'PK':partition,'SK':'ACCESS'}}
        if exists:
            data.update(ConditionExpression='#state = :deleting AND deletionOperationId = :operation',ExpressionAttributeNames={'#state':'state'},ExpressionAttributeValues={':deleting':'DELETING',':operation':operation})
        else:data['ConditionExpression']='attribute_not_exists(PK)'
        return {'ConditionCheck':data}

    def _purchase_releases(self, partition, targets):
        """Release each admitted purchase reservation with its receipt deletion.

        Group by global key to avoid duplicate DynamoDB actions. Local PERIOD
        rows remain until their pending receipts have been visited in sort order.
        """
        from .purchase_usage import (period_for_receipt, global_for_period, counter_action,
                                     local_counter_action, exact_condition, validate_period, read, RETENTION_SECONDS)
        groups, conditions = {}, {}
        deleting = {row['SK'] for row in targets}
        for row in targets:
            if row.get('recordType') != 'V1_CHECK_RECEIPT' or row.get('basis') != 'paid' or row.get('state') != 'ADMITTED':
                continue
            period = period_for_receipt(self.ddb, self.table, partition, row)
            pointer = period['purchaseUsageKey']
            identity = (pointer['PK'], pointer['SK'])
            if identity not in groups:
                groups[identity] = {'period': period, 'count': 0,
                                    'global': global_for_period(self.ddb, self.table, period, now=self.now(), allow_expired=True)}
            if groups[identity]['period'] != period:
                raise AuthorityError('PURCHASE_USAGE_MISMATCH')
            groups[identity]['count'] += 1
            conditions[row['SK']] = exact_condition(row)
        actions, global_guards = [], {}
        for period in targets:
            if period.get('recordType') != 'V1_ALLOWANCE_PERIOD':
                continue
            if 'purchaseUsageKey' not in period and period.get('limit') != PAID_COMPLETED_CHECKS:
                continue
            pointer = validate_period(period)
            identity = (pointer['PK'], pointer['SK'])
            group = groups.get(identity)
            released = group['count'] if group and group['period'] == period else 0
            if released != period['reservedChecks']:
                # Every CHECK sorts before PERIOD. Missing receipt evidence may
                # not silently strand reservations and claim completion.
                raise AuthorityError('PURCHASE_USAGE_MISMATCH')
            conditions[period['SK']] = exact_condition(period)
            if group or self.now() >= period['endEpoch'] + RETENTION_SECONDS:
                continue
            observed = read(self.ddb, self.table, pointer, now=self.now())
            if (any(observed[k] != period[k] for k in ('startEpoch', 'endEpoch', 'limit'))
                    or observed['usedChecks'] < period['usedChecks']):
                raise AuthorityError('PURCHASE_USAGE_MISMATCH')
            # Ownership may already have been released after old reservations
            # reached zero. A newly restored account can legitimately advance
            # global counters while this old zero-reservation PERIOD is erased.
            # Guard immutable funding evidence and monotonic usage, not equality.
            previous = global_guards.get(identity)
            minimum = max(period['usedChecks'], previous[1] if previous else 0)
            global_guards[identity] = (observed, minimum)
        for observed, minimum in global_guards.values():
            immutable = {k: v for k, v in observed.items() if k not in ('usedChecks', 'reservedChecks')}
            guard = exact_condition(immutable)
            guard['ConditionExpression'] += ' AND usedChecks >= :minimumUsed'
            guard['ExpressionAttributeValues'][':minimumUsed'] = minimum
            actions.append({'ConditionCheck': {'TableName': self.table,
                            'Key': {k: observed[k] for k in ('PK', 'SK')}, **guard}})
        for group in groups.values():
            period, count, observed = group['period'], group['count'], group['global']
            if count > period['reservedChecks']:
                raise AuthorityError('PURCHASE_USAGE_MISMATCH')
            if period['SK'] not in deleting:
                actions.append(local_counter_action(self.table, period, -count, 0))
            if observed is not None:
                actions.append(counter_action(self.table, observed, -count, 0, now=self.now()))
        return actions, conditions

    def delete_batch(self,command,*,page_size=20,max_pages=2,can_continue=lambda:True):
        if type(page_size) is not int or not 1<=page_size<=20 or type(max_pages) is not int or not 1<=max_pages<=4:
            raise AuthorityError('DELETION_CONFIGURATION_UNAVAILABLE')
        if not can_continue():return {'deleted':0,'complete':False,'alreadyComplete':False}
        from shared_account_finalization.service import completed_fence
        if isinstance(command,dict) and isinstance(command.get('PK'),str) and command.get('SK')=='ACCOUNT_DELETION':
            current=self._get(self.ledger,{'PK':command['PK'],'SK':'ACCOUNT_DELETION'})
            original=None if current==command else command
            if completed_fence(current,self.environment,self.now(),original):
                return {'deleted':0,'complete':True,'alreadyComplete':True}
        account=self._command(command);inventory=self._inventory()
        partitions=[self._partition(account,k) for k in sorted(self.keys)]
        receipt_key={'PK':command['PK'],'SK':'ACCOUNT_DELETION#'+COMPONENT}
        receipt=self._get(self.ledger,receipt_key)
        if receipt:
            expected={'PK':command['PK'],'SK':receipt_key['SK'],'schemaVersion':1,'recordVersion':1,'environment':self.environment,'eventType':'account.deletion.component.completed','component':COMPONENT,'status':'COMPLETE','operationId':command['operationId'],'requestOccurredAtEpoch':command['occurredAtEpoch']}
            if integral(receipt.get('schemaVersion'))!=1 or integral(receipt.get('recordVersion'))!=1 or set(receipt)!=set(expected)|{'occurredAtEpoch','retainUntilEpoch'} or any(receipt.get(k)!=v for k,v in expected.items()) or integral(receipt.get('occurredAtEpoch')) is None or receipt['occurredAtEpoch']<command['occurredAtEpoch'] or integral(receipt.get('retainUntilEpoch'))!=receipt['occurredAtEpoch']+self.retention:
                raise AuthorityError('DELETION_RECEIPT_INVALID')
            return {'deleted':0,'complete':True,'alreadyComplete':True}
        progress_key={'PK':command['PK'],'SK':'ACCOUNT_DELETION#V1_AUTHORITY_PROGRESS'}
        expected_progress=progress_key|{'recordType':'V1_AUTHORITY_DELETION_PROGRESS','operationId':command['operationId'],'requestOccurredAtEpoch':command['occurredAtEpoch'],'inventoryRevision':inventory['revision'],'issuedKeys':inventory['issuedKeys']}
        progress=self._get(self.ledger,progress_key)
        if progress is not None and progress!=expected_progress:
            raise AuthorityError('DELETION_INVENTORY_CHANGED')
        fence=self._guards(command,inventory)
        if progress is None:fence.append({'Put':{'TableName':self.ledger,'Item':expected_progress,'ConditionExpression':'attribute_not_exists(PK)'}})
        for partition in partitions:
            access=self._get(self.table,{'PK':partition,'SK':'ACCESS'})
            if access is None:
                fence.append(self._access_guard(partition,command['operationId'],False));continue
            fence.append({'Update':{'TableName':self.table,'Key':{'PK':partition,'SK':'ACCESS'},
                'UpdateExpression':'SET #state = :deleting, deletionOperationId = :operation REMOVE expiresAt',
                'ConditionExpression':'recordType = :type AND (attribute_not_exists(deletionOperationId) OR deletionOperationId = :operation)',
                'ExpressionAttributeNames':{'#state':'state'},'ExpressionAttributeValues':{':deleting':'DELETING',':operation':command['operationId'],':type':'V1_ACCESS_AUTHORITY'}}})
        if not can_continue():return {'deleted':0,'complete':False,'alreadyComplete':False}
        self._transact(fence)
        deleted=pages=0
        for partition in partitions:
            while True:
                if not can_continue():return {'deleted':deleted,'complete':False,'alreadyComplete':False}
                page=self.ddb.Table(self.table).query(KeyConditionExpression='PK = :pk',ExpressionAttributeValues={':pk':partition},ConsistentRead=True,Limit=page_size+1)
                rows=page.get('Items',[])
                # ACCESS may sort after an unknown/future row family. A page is
                # not proof that the control row is absent.
                access=self._get(self.table,{'PK':partition,'SK':'ACCESS'})
                targets=[x for x in rows if x['SK']!='ACCESS'][:page_size]
                if targets:
                    items=self._guards(command,inventory)+[self._access_guard(partition,command['operationId'],access is not None)]
                    releases, conditions = self._purchase_releases(partition, targets)
                    items += releases
                    items += [{'Delete':{'TableName':self.table,'Key':{'PK':partition,'SK':row['SK']}, **conditions.get(row['SK'], {})}} for row in targets]
                    self._transact(items);deleted+=len(targets);pages+=1
                    if pages>=max_pages:return {'deleted':deleted,'complete':False,'alreadyComplete':False}
                    continue
                if page.get('LastEvaluatedKey'):raise AuthorityError('DELETION_PAGE_INVALID')
                if access is not None:
                    self._transact(self._guards(command,inventory)+[{'Delete':{'TableName':self.table,'Key':{'PK':partition,'SK':'ACCESS'},'ConditionExpression':'#state = :deleting AND deletionOperationId = :operation','ExpressionAttributeNames':{'#state':'state'},'ExpressionAttributeValues':{':deleting':'DELETING',':operation':command['operationId']}}}]);deleted+=1
                break
        # Once fenced, all known writers require global deletion absence or this
        # ACCESS fence. They cannot create a row between empty read and receipt.
        for partition in partitions:
            if not can_continue():return {'deleted':deleted,'complete':False,'alreadyComplete':False}
            empty=self.ddb.Table(self.table).query(KeyConditionExpression='PK = :pk',ExpressionAttributeValues={':pk':partition},ConsistentRead=True,Limit=1)
            if empty.get('Items'):raise AuthorityError('DELETION_NOT_EMPTY')
        epoch=self.now()
        if type(epoch) is not int or epoch<command['occurredAtEpoch']:
            raise AuthorityError('DELETION_CONFIGURATION_UNAVAILABLE')
        completed=receipt_key|{'schemaVersion':1,'recordVersion':1,'environment':self.environment,'eventType':'account.deletion.component.completed','component':COMPONENT,'status':'COMPLETE','operationId':command['operationId'],'occurredAtEpoch':epoch,'requestOccurredAtEpoch':command['occurredAtEpoch'],'retainUntilEpoch':epoch+self.retention}
        if not can_continue():return {'deleted':deleted,'complete':False,'alreadyComplete':False}
        self._transact(self._guards(command,inventory)+[self._access_guard(p,command['operationId'],False) for p in partitions]+[
            {'Put':{'TableName':self.ledger,'Item':completed,'ConditionExpression':'attribute_not_exists(PK)'}},
            {'Delete':{'TableName':self.ledger,'Key':progress_key,'ConditionExpression':'operationId = :operation','ExpressionAttributeValues':{':operation':command['operationId']}}}])
        return {'deleted':deleted,'complete':True,'alreadyComplete':False}
