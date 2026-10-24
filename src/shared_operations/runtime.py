"""Default-off trusted constructors; never load a keyring in the evaluator.

Configuration, table names and provider selection come from server settings.
An invocation capability can identify only its original account/check and is
checked against the winning admission commitment in every provider transaction.
"""
import os
import re
import time
from contextlib import contextmanager
from types import SimpleNamespace

from shared_check_authority.core import Authority, OWNER_POLICY, integral
from shared_check_authority.inventory import INVENTORY_FIELDS, INVENTORY_KEY, inventory_condition
from .contract import plain, require
from .publisher import Publisher, lookup_key


def enabled():
    value = os.environ.get('OPERATIONS_ENABLED', 'false')
    require(value in ('true', 'false'), 'OPERATIONS_CONFIGURATION_INVALID')
    return value == 'true'


class WorkerView:
    _get = Authority._get
    _check = Authority._check
    _transact = Authority._transact

    def __init__(self, resource, table, now):
        require(os.environ.get('STAGE') == 'dev', 'OPERATIONS_CONFIGURATION_INVALID')
        values = {name: os.environ[env] for name, env in (
            ('users_table', 'USERS_TABLE_NAME'), ('devices_table', 'DEVICE_BINDINGS_TABLE_NAME'),
            ('deletion_table', 'DELETION_LEDGER_TABLE_NAME'))}
        values.update(authority_table=table, policy_version=OWNER_POLICY)
        require(all(type(v) is str and re.fullmatch('[A-Za-z0-9_.-]{3,255}', v)
                    for k, v in values.items() if k != 'policy_version'), 'OPERATIONS_CONFIGURATION_INVALID')
        require(os.environ.get('AUTHORITY_POLICY_VERSION') == OWNER_POLICY, 'OPERATIONS_CONFIGURATION_INVALID')
        self.s = SimpleNamespace(**values)
        self.ddb, self.client, self.now = resource, resource.meta.client, now
        require(self.client.meta.config.retries.get('total_max_attempts') == 1, 'OPERATIONS_CONFIGURATION_INVALID')


def worker_publisher(resource, table, *, now):
    if not enabled(): return None
    view = WorkerView(resource, table, now)
    return Publisher(view, os.environ['OPERATIONS_TABLE_NAME'], os.environ['OPERATIONS_STORE_GENERATION'], os.environ['STAGE'])


class MessageView(WorkerView):
    def __init__(self, resource, context, digest, *, now):
        super().__init__(resource, os.environ['AUTHORITY_TABLE_NAME'], now)
        self.context, self.digest = context, digest

    def _partition(self, account, key_id):
        require(account == self.context['account'] and key_id == self.context['proof'].split('_')[1],
                'OPERATIONS_CONTEXT_INVALID')
        return self.context['partition']

    def _token_parts(self, proof):
        require(proof == self.context['proof'], 'OPERATIONS_CONTEXT_INVALID')
        _, key_id, expiry, nonce, tag = proof.split('_')
        return key_id, int(expiry, 16), nonce, tag

    def _get(self, table, key):
        require(table == self.s.authority_table and key == lookup_key(self.context['partition'], self.context['proof']),
                'OPERATIONS_CONTEXT_INVALID')
        return super()._get(table, key)

    def _check(self, table, key, expression, values=None, names=None):
        if table == self.s.authority_table and key.get('SK', '').startswith('CHECK#'):
            require(key == {'PK': self.context['partition'], 'SK': 'CHECK#' + self.context['proof']},
                    'OPERATIONS_CONTEXT_INVALID')
            expression += ' AND operationsContextDigest = :contextDigest'
            values = dict(values or {}, **{':contextDigest': self.digest})
        return super()._check(table, key, expression, values, names)

    def _account_conditions(self, account):
        require(account == self.context['account'], 'OPERATIONS_CONTEXT_INVALID')
        names = {f'#f{i}': field for i, field in enumerate(INVENTORY_FIELDS)}
        row = plain(self.ddb.Table(self.s.authority_table).get_item(Key=INVENTORY_KEY, ConsistentRead=True,
            ProjectionExpression=', '.join(names), ExpressionAttributeNames=names).get('Item'))
        key_id = self.context['proof'].split('_')[1]
        require(type(row) is dict and set(row) == set(INVENTORY_FIELDS)
                and row['PK'] == INVENTORY_KEY['PK'] and row['SK'] == INVENTORY_KEY['SK']
                and row['recordType'] == 'V1_HMAC_KEY_INVENTORY' and integral(row['schemaVersion']) == 1
                and integral(row['revision']) is not None and row['revision'] >= 1
                and row['coverage'] == 'VERIFIED_COMPLETE' and type(row['issuedKeys']) is dict
                and 1 <= len(row['issuedKeys']) <= 4
                and all(type(k) is str and re.fullmatch('[A-Za-z0-9]{1,8}', k)
                    and type(v) is str and re.fullmatch('[0-9a-f]{64}', v) for k, v in row['issuedKeys'].items())
                and row['issuedKeys'].get(key_id) == self.context['keyFingerprint'], 'OPERATIONS_CONTEXT_INVALID')
        return [inventory_condition(self.s.authority_table, row),
            self._check(self.s.users_table, {'PK': f'USER#{account}', 'SK': 'PROFILE'},
                '#s = :active AND ageVerified = :yes AND #sub = :account',
                {':active': 'ACTIVE', ':yes': True, ':account': account}, {'#s': 'status', '#sub': 'sub'}),
            self._check(self.s.deletion_table, {'PK': f'ACCOUNT#{account}', 'SK': 'ACCOUNT_DELETION'}, 'attribute_not_exists(PK)')]


def message_observer(event, *, resource=None, now=lambda: int(time.time())):
    from .message_context import commitment, validate
    from .dispatch import JournalObserver
    from shared_message_contract import validation_v2 as v2, validation_v3 as v3
    require(enabled(), 'OPERATIONS_CONFIGURATION_INVALID')
    context = validate(event['operationsContext'])
    version = {2: v2.VERSION, 3: v3.VERSION}.get(event['schemaVersion'])
    require(version is not None and event['policyVersion'] == v2.POLICY, 'OPERATIONS_CONTEXT_INVALID')
    mode = event.get('runtimeMode', 'ai')
    require(mode == context['executionMode'] and context['acceptedAtEpoch'] <= now(), 'OPERATIONS_CONTEXT_INVALID')
    payload = event['intent'] | {'messageTransportVersion': version}
    if mode == 'ai_primary_v1': payload['messageExecutionProfile'] = mode
    digest = commitment(context, payload, event['checkId'])
    if resource is None:
        import boto3
        from botocore.config import Config
        resource = boto3.resource('dynamodb', region_name='us-east-1',
            config=Config(connect_timeout=.2, read_timeout=.3, retries={'total_max_attempts': 1}))
    view = MessageView(resource, context, digest, now=now)
    publisher = Publisher(view, os.environ['OPERATIONS_TABLE_NAME'], os.environ['OPERATIONS_STORE_GENERATION'], os.environ['STAGE'])
    require(context['generation'] == publisher.generation, 'OPERATIONS_CONTEXT_INVALID')
    return JournalObserver(publisher, context['account'], context['proof'], context['executionToken'],
        component='message_evaluator', ordinal=1, provider='openai')


@contextmanager
def message_execution(event):
    """Claim the private invocation before policy may perform any linked lookup."""
    from .dispatch import JournalObserver, finished
    observer = message_observer(event)
    internal = JournalObserver(observer.publisher, observer.account, observer.proof, observer.token,
        component='message_evaluator', ordinal=1, provider=None)
    internal.start()
    try:
        yield observer
    except Exception:
        finished(internal, 'failed', 'SERVICE_UNAVAILABLE', None)
        raise
    else:
        finished(internal, 'succeeded', 'NONE', None)
