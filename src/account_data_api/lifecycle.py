"""Guarded purchase cleanup and final identity completion; no activation defaults."""
from shared_account_finalization.service import (
    FinalizationError, validate_inventory, inventory_condition, serialize_operation,
)
from service import validate_command, _valid_component_receipt, _component_receipt


class Lifecycle:
    def __init__(self, *, ledger, ledger_name, client, ownership, finalizer,
                 environment, manifest_sha256, inventory_revision, now):
        self.ledger, self.ledger_name, self.client = ledger, ledger_name, client
        self.ownership, self.finalizer = ownership, finalizer
        self.environment, self.manifest, self.revision, self.now = environment, manifest_sha256, inventory_revision, now

    def verify(self, command):
        command = validate_command(command, self.environment)
        current = self.ledger.get_item(Key={'PK':command['PK'],'SK':command['SK']},ConsistentRead=True).get('Item')
        if self.finalizer._completed(current,command,self.now()):
            return None
        inventory = self.ledger.get_item(Key={'PK':'INVENTORY#'+self.environment,'SK':'ACCOUNT_DATA_INVENTORY'},ConsistentRead=True).get('Item')
        validate_inventory(inventory,self.environment,self.manifest,
                           expected_revision=self.revision,now_epoch=self.now())
        if command['occurredAtEpoch'] <= inventory['approvedAtEpoch']:
            raise FinalizationError('FINALIZER_INVENTORY_UNVERIFIED')
        current = self.ledger.get_item(Key={'PK':command['PK'],'SK':command['SK']},ConsistentRead=True).get('Item')
        if current != command:
            raise FinalizationError('FINALIZER_COMMAND_UNVERIFIED')
        return inventory

    def entitlements(self, command):
        inventory = self.verify(command)
        if inventory is None:
            return {'complete':True,'deleted':0}
        key = {'PK':command['PK'],'SK':'ACCOUNT_DELETION#ENTITLEMENTS'}
        old = self.ledger.get_item(Key=key,ConsistentRead=True).get('Item')
        if _valid_component_receipt(old,command,'ENTITLEMENTS'):
            return {'complete':True,'deleted':0}
        ownership_inventory = self.ownership.inventory()
        result = self.ownership.delete_owned_batch(command,limit=20)
        if not result['complete']:
            return result
        # Writers share the fixed deletion fence. Empty owned partition plus exact
        # command and both inventory proofs permits a bounded completion receipt.
        operations = self.ownership._deletion_guards(command,ownership_inventory)
        operations.append(inventory_condition(self.ledger_name,inventory))
        receipt = _component_receipt(command,'ENTITLEMENTS',self.now())
        put = {'TableName':self.ledger_name,'Item':receipt,'ConditionExpression':'attribute_not_exists(PK)'}
        operations.append({'Put':put})
        try:
            self.client.transact_write_items(TransactItems=[serialize_operation(op) for op in operations])
        except Exception:
            current = self.ledger.get_item(Key=key,ConsistentRead=True).get('Item')
            if not _valid_component_receipt(current,command,'ENTITLEMENTS'):
                raise RuntimeError('Account entitlement cleanup receipt unconfirmed') from None
        return result

    def finalize(self, command):
        if not self.finalizer.enabled:
            return {'complete':False,'policyBlocked':True}
        try:
            return self.finalizer.finalize(command)
        except FinalizationError as error:
            if str(error) == 'FINALIZER_COMPONENT_UNVERIFIED':
                return {'complete':False,'policyBlocked':False}
            raise
