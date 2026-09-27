"""Verify-only support admission into the unchanged twelve-component deletion pipeline.

The signed record is call-local. This module cannot sign evidence, reset passwords,
delete Cognito identities, or issue component receipts. AWS IAM transport authenticity
is an independently qualified deployment prerequisite, not a property of event JSON.
"""
from copy import deepcopy
import os
import time

from .contract import ALGORITHM, clocks, envelope, integer, need, profile_hash, settings, Unavailable


class Guard:
    def __init__(self, record, config, now, remaining):
        self.record, self.config, self.now, self.remaining = record, config, now, remaining
        self.previous = 0

    def check(self):
        value = integer(self.now())
        need(value >= self.previous and self.remaining() >= 6000)
        clocks(self.record, self.config, value)
        self.previous = value
        return value


class Client:
    def __init__(self, raw, guard, profile=None):
        self.raw, self.guard, self.profile = raw, guard, profile
        self.writes = 0

    def get_item(self, **kwargs):
        self.guard.check()
        return self.raw.get_item(**kwargs)

    def query(self, **kwargs):
        self.guard.check()
        return self.raw.query(**kwargs)

    def transact_write_items(self, **kwargs):
        from boto3.dynamodb.types import TypeSerializer
        wire = TypeSerializer().serialize
        self.guard.check()
        need(self.profile is not None and self.writes == 0)
        actions = deepcopy(kwargs['TransactItems'])
        c = self.guard.config
        pk = 'USER#' + self.guard.record['subject']
        matches = [a['Update'] for a in actions if 'Update' in a
                   and a['Update']['TableName'] == c['usersTable']
                   and a['Update']['Key'] == {'PK': {'S': pk}, 'SK': {'S': 'PROFILE'}}]
        need(len(matches) == 1 and len(actions) <= 100)
        update = matches[0]
        terms = []
        # Equality of every observed profile field, plus absence of all supported
        # optional fields, closes identity/status races without a duplicate item action.
        names = update.setdefault('ExpressionAttributeNames', {})
        values = update.setdefault('ExpressionAttributeValues', {})
        fields = PROFILE_FIELDS | set(self.profile)
        for index, name in enumerate(sorted(fields)):
            alias, value_alias = '#support' + str(index), ':support' + str(index)
            need(alias not in names and value_alias not in values)
            names[alias] = name
            if name in self.profile:
                values[value_alias] = wire(self.profile[name])
                terms.append(alias + ' = ' + value_alias)
            else:
                terms.append('attribute_not_exists(' + alias + ')')
        update['ConditionExpression'] = '(' + update['ConditionExpression'] + ') AND ' + ' AND '.join(terms)
        self.guard.check()
        self.writes += 1
        return self.raw.transact_write_items(TransactItems=actions)


PROFILE_FIELDS = {
    'PK', 'SK', 'sub', 'email', 'given_name', 'family_name', 'phone_number', 'over_18',
    'status', 'ageVerified', 'ageVerifiedAt', 'agePolicyVersion', 'createdAt', 'updatedAt',
    'updatedAtEpoch', 'deletionOperationId', 'deletionRequestedAtEpoch',
}


class Table:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def get_item(self, Key, **kwargs):
        from boto3.dynamodb.types import TypeSerializer, TypeDeserializer
        result = self.client.get_item(TableName=self.name,
                    Key={k: TypeSerializer().serialize(v) for k, v in Key.items()}, **kwargs)
        return {'Item': {k: TypeDeserializer().deserialize(v) for k, v in result['Item'].items()}} if result.get('Item') else {}


def execute(event, context, *, config=None, clients=None, now=lambda: int(time.time())):
    # Dependency injection is local SDK testing only; deployed handler never accepts it.
    c = settings(os.environ) if config is None else settings({
        'SUPPORT_ACCOUNT_DELETION_ENABLED': os.environ.get('SUPPORT_ACCOUNT_DELETION_ENABLED', ''),
        'SUPPORT_ACCOUNT_DELETION_CONFIG_JSON': __import__('json').dumps(config)})
    record, signature, encoded = envelope(event, context, c)
    guard = Guard(record, c, now, context.get_remaining_time_in_millis)
    guard.check()
    if clients is None:
        import boto3
        from botocore.config import Config
        # No hidden SDK write retries after an uncertain admission transaction.
        sdk = Config(retries={'total_max_attempts': 1}, connect_timeout=2, read_timeout=3)
        clients = {name: boto3.client(name, region_name=c['region'], config=sdk)
                   for name in ('kms', 'dynamodb', 'cognito-idp')}
    guard.check()
    verification = clients['kms'].verify(KeyId=c['kmsKeyArn'], Message=encoded,
        MessageType='RAW', Signature=signature, SigningAlgorithm=ALGORITHM)
    need(verification.get('SignatureValid') is True and verification.get('KeyId') == c['kmsKeyArn']
         and verification.get('SigningAlgorithm') == ALGORITHM)
    guard.check()
    raw = clients['dynamodb']
    for name in ('usersTable', 'ledgerTable'):
        guard.check()
        table = raw.describe_table(TableName=c[name])['Table']
        need(table['TableId'] == c[name + 'Id'] and table['TableStatus'] == 'ACTIVE'
             and table['TableArn'] == 'arn:aws:dynamodb:' + c['region'] + ':' + c['accountId'] + ':table/' + c[name])
    client = Client(raw, guard)
    ledger = Table(client, c['ledgerTable'])
    from account_data_api.service import AccountDeletionService, validate_command
    from shared_account_finalization.service import (REQUIRED_COMPONENTS, validate_inventory, completed_fence)
    inventory = ledger.get_item(Key={'PK': 'INVENTORY#dev', 'SK': 'ACCOUNT_DATA_INVENTORY'}, ConsistentRead=True).get('Item')
    validate_inventory(inventory, 'dev', c['inventoryManifestSha256'], REQUIRED_COMPONENTS,
                       expected_revision=c['inventoryRevision'], now_epoch=guard.check())
    command_key = {'PK': 'ACCOUNT#' + record['subject'], 'SK': 'ACCOUNT_DELETION'}

    def acknowledge(command):
        current = guard.check()
        if command.get('status') == 'COMPLETE':
            need(completed_fence(command, 'dev', current))
        else:
            validate_command(command, 'dev')
        need(command['accountId'] == record['subject'] and command['operationId'] == record['operationId']
             and record['issuedAtEpoch'] <= command['occurredAtEpoch'] <= current
             and command['occurredAtEpoch'] < record['expiresAtEpoch']
             and inventory['approvedAtEpoch'] < command['occurredAtEpoch'])
        # Terminal recognition is not a new all-components erasure observation.
        return {'schemaVersion': 1, 'operationId': record['operationId'],
                'admission': 'ACCEPTED', 'completionVerified': False}

    existing = ledger.get_item(Key=command_key, ConsistentRead=True).get('Item')
    if existing is not None:
        return acknowledge(existing)
    profile = Table(client, c['usersTable']).get_item(
        Key={'PK': 'USER#' + record['subject'], 'SK': 'PROFILE'}, ConsistentRead=True).get('Item')
    need(type(profile) is dict and set(profile) <= PROFILE_FIELDS and profile.get('sub') == record['subject']
         and profile.get('status') in ('ACTIVE', 'PENDING_AGE_GATE')
         and profile_hash(profile) == record['profileSha256'])
    guard.check()
    user = clients['cognito-idp'].admin_get_user(UserPoolId=c['cognitoPoolId'], Username=record['subject'])
    attrs = user.get('UserAttributes', [])
    need(type(attrs) is list and len({a['Name'] for a in attrs}) == len(attrs))
    attributes = {a['Name']: a['Value'] for a in attrs}
    need(user.get('Username') == record['subject'] and attributes.get('sub') == record['subject'])
    for name in ('email', 'given_name', 'family_name', 'phone_number'):
        need(attributes.get(name) == profile.get(name))
    # Cognito attributes are not ownership proof. The independent signer supplies
    # that proof; this read only detects current account/snapshot mismatch.
    admitted_at = guard.check()
    client.profile = profile
    service = AccountDeletionService(environment='dev', ledger_table=ledger,
        users_table_name=c['usersTable'], ledger_table_name=c['ledgerTable'], dynamodb_client=client,
        required_components=REQUIRED_COMPONENTS, inventory_manifest_sha256=c['inventoryManifestSha256'],
        inventory_revision=c['inventoryRevision'], campaign_recovery_writes_enabled=True, now=lambda: admitted_at)
    try:
        service.request(record['subject'], record['operationId'])
    except Exception:
        # No retry: only reconcile an exact durable command after possible commit.
        command = ledger.get_item(Key=command_key, ConsistentRead=True).get('Item')
        need(command is not None)
        return acknowledge(command)
    return acknowledge(ledger.get_item(Key=command_key, ConsistentRead=True).get('Item'))
