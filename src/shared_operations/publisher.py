"""Transactional operations facts using the existing authority ownership fence.

No table, permissions, schedule or recipient is provisioned here. A separate
qualified operations table is required before this permanent publisher can run.
"""
from copy import deepcopy
from dataclasses import dataclass, field
import re
import secrets

from .contract import RETENTION_SECONDS, canonical, empty_event, plain, require, validate

CONTROL = {'PK': 'OPERATIONS_CONTROL', 'SK': 'GENERATION'}
PROOF = re.compile(r'v1_[A-Za-z0-9]{1,8}_[0-9a-f]{8}_[0-9a-f]{16}_[0-9a-f]{24}')
PARTITION = re.compile(r'V1#[A-Za-z0-9]{1,8}#[0-9a-f]{64}')


def lookup_key(partition, proof):
    require(type(partition) is str and PARTITION.fullmatch(partition)
            and type(proof) is str and PROOF.fullmatch(proof), 'OPERATIONS_SOURCE_INVALID')
    return {'PK': partition, 'SK': 'OPS#' + proof}


def _put(table, event, deadline, generation):
    event = validate(event)
    return {'Put': {'TableName': table, 'Item': {
        'PK': event['logicalCheckRef'], 'SK': event['eventId'], 'recordType': 'V1_OPERATIONS_FACT',
        'generation': generation, 'expiresAt': deadline, 'event': event},
        'ConditionExpression': 'attribute_not_exists(PK)'}}


@dataclass(frozen=True)
class AttemptPermit:
    """Internal dispatch ownership, never serialized to a public request/report."""
    account: str = field(repr=False)
    proof: str = field(repr=False)
    lookup: dict = field(repr=False)
    started: dict = field(repr=False)
    finish_id: str = field(repr=False)
    execution_token: str = field(repr=False)


class Publisher:
    def __init__(self, authority, table, generation, environment, *, nonce=lambda: secrets.token_hex(16)):
        require(environment in ('dev', 'uat', 'prod') and type(table) is str
                and re.fullmatch(r'[A-Za-z0-9_.-]{3,255}', table)
                and table not in {authority.s.authority_table, authority.s.users_table, authority.s.devices_table, authority.s.deletion_table}
                and type(generation) is str and re.fullmatch('[0-9a-f]{32}', generation),
                'OPERATIONS_CONFIGURATION_INVALID')
        self.a, self.table, self.generation, self.environment, self.nonce = authority, table, generation, environment, nonce

    def _id(self, prefix):
        value = self.nonce()
        require(type(value) is str and re.fullmatch('[0-9a-f]{32}', value), 'OPERATIONS_CONFIGURATION_INVALID')
        return prefix + value

    def generation_guard(self):
        return self.a._check(self.table, CONTROL,
            'recordType = :type AND generation = :generation AND environment = :environment AND #state = :ready AND retentionSeconds = :retention AND backupsDisabled = :yes',
            {':type': 'V1_OPERATIONS_STORE', ':generation': self.generation, ':ready': 'QUALIFIED',
             ':environment': self.environment, ':retention': RETENTION_SECONDS, ':yes': True}, {'#state': 'state'})

    def _meta(self, partition, proof, *, allow_expired=False):
        key = lookup_key(partition, proof)
        row = plain(self.a._get(self.a.s.authority_table, key))
        fields = {'PK', 'SK', 'recordType', 'revision', 'generation', 'environment', 'acceptedAtEpoch',
                  'operationsExpiresAtEpoch', 'logicalCheckRef', 'acceptedEventId', 'terminalEventId',
                  'service', 'inputKind', 'accessClass', 'attempts', 'GSI1PK', 'GSI1SK', 'lookupState'}
        require(type(row) is dict and set(row) == fields and all(row[k] == v for k, v in key.items())
                and row.get('recordType') == 'V1_OPERATIONS_LOOKUP'
                and row.get('generation') == self.generation and row.get('environment') == self.environment
                and type(row.get('acceptedAtEpoch')) is int and row['acceptedAtEpoch'] >= 0
                and row['acceptedAtEpoch'] <= self.a.now()
                and row.get('operationsExpiresAtEpoch') == row['acceptedAtEpoch'] + RETENTION_SECONDS
                and type(row.get('revision')) is int and row['revision'] >= 1
                and type(row.get('attempts')) is dict and len(row['attempts']) <= 10
                and row.get('lookupState') in (('OPEN', 'DRAINING') if allow_expired else ('OPEN',))
                and (allow_expired or row['operationsExpiresAtEpoch'] > self.a.now()),
                'OPERATIONS_SOURCE_UNAVAILABLE')
        require(row['GSI1PK'] == 'V1_OPERATIONS_EXPIRING' and row['GSI1SK'] ==
                f'{row["operationsExpiresAtEpoch"]:012d}#{partition}#{key["SK"]}', 'OPERATIONS_SOURCE_INVALID')
        empty_event(eventType='check_accepted', eventId=row['acceptedEventId'], eventTimeEpoch=row['acceptedAtEpoch'],
            environment=self.environment, service=row['service'], inputKind=row['inputKind'],
            logicalCheckRef=row['logicalCheckRef'], lifecycleRevision=1, accountingState='pending', accessClass=row['accessClass'])
        require(type(row['terminalEventId']) is str and re.fullmatch('op1_[0-9a-f]{32}', row['terminalEventId']), 'OPERATIONS_SOURCE_INVALID')
        for slot, value in row['attempts'].items():
            require(type(value) is dict and set(value) == {'start', 'finishEventId'}, 'OPERATIONS_SOURCE_INVALID')
            begun = validate(value['start'])
            require(begun['eventType'] == 'attempt_started' and begun['logicalCheckRef'] == row['logicalCheckRef']
                    and slot == f'{begun["component"]}:{begun["attemptOrdinal"]}:{begun["providerFamily"] or "internal"}'
                    and type(value['finishEventId']) is str and re.fullmatch('op1_[0-9a-f]{32}', value['finishEventId']),
                    'OPERATIONS_SOURCE_INVALID')
        return row

    def meta_guard(self, row, *, live=True):
        operation = self.a._check(self.a.s.authority_table, {k: row[k] for k in ('PK', 'SK')},
            'recordType = :type AND revision = :revision AND generation = :generation AND operationsExpiresAtEpoch = :deadline AND logicalCheckRef = :reference AND lookupState = :lookup',
            {':type': 'V1_OPERATIONS_LOOKUP', ':revision': row['revision'], ':generation': self.generation,
             ':deadline': row['operationsExpiresAtEpoch'], ':reference': row['logicalCheckRef'], ':lookup': row['lookupState']})
        if live:
            epoch = self.a.now()
            require(row['acceptedAtEpoch'] <= epoch < row['operationsExpiresAtEpoch'], 'OPERATIONS_SOURCE_UNAVAILABLE')
            operation['ConditionCheck']['ConditionExpression'] += ' AND operationsExpiresAtEpoch > :liveNow'
            operation['ConditionCheck']['ExpressionAttributeValues'][':liveNow'] = epoch
        return operation

    def admission_actions(self, partition, proof, payload, basis, accepted_at):
        source = payload.get('target', {}).get('sourceType') if payload.get('entryPoint') == 'message' else payload.get('entryPoint')
        service = 'message_check' if payload.get('entryPoint') == 'message' else (
            'recovery_clarification' if payload.get('entryPoint') == 'recovery' else 'url_check')
        if service == 'recovery_clarification': source = 'recovery_clarification'
        key = lookup_key(partition, proof)
        deadline = accepted_at + RETENTION_SECONDS
        meta = key | {'recordType': 'V1_OPERATIONS_LOOKUP', 'revision': 1,
            'generation': self.generation, 'environment': self.environment,
            'acceptedAtEpoch': accepted_at, 'operationsExpiresAtEpoch': deadline,
            'logicalCheckRef': self._id('lc1_'), 'acceptedEventId': self._id('op1_'),
            'terminalEventId': self._id('op1_'), 'service': service, 'inputKind': source,
            'accessClass': basis, 'attempts': {}, 'lookupState': 'OPEN', 'GSI1PK': 'V1_OPERATIONS_EXPIRING',
            'GSI1SK': f'{deadline:012d}#{partition}#{key["SK"]}'}
        event = empty_event(eventType='check_accepted', eventId=meta['acceptedEventId'],
            eventTimeEpoch=accepted_at, environment=self.environment, service=service, inputKind=source,
            logicalCheckRef=meta['logicalCheckRef'], lifecycleRevision=1, accountingState='pending', accessClass=basis)
        return [self.generation_guard(), {'Put': {'TableName': self.a.s.authority_table,
            'Item': meta, 'ConditionExpression': 'attribute_not_exists(PK)'}},
            _put(self.table, event, deadline, self.generation)]

    def terminal_actions(self, partition, proof, outcome, charge, settled_at, *, source_accepted_at, expired=False):
        meta = self._meta(partition, proof)
        require(meta['acceptedAtEpoch'] == source_accepted_at, 'OPERATIONS_SOURCE_INVALID')
        reasons = {'complete': 'NONE', 'partial': 'COVERAGE_PARTIAL', 'inconclusive': 'RESULT_INCONCLUSIVE',
                   'failed': 'OPERATION_EXPIRED' if expired else 'SETTLED_FAILED', 'blocked': 'RESULT_BLOCKED',
                   'invalid_input': 'RESULT_INVALID', 'unsupported': 'RESULT_UNSUPPORTED', 'unavailable': 'SERVICE_UNAVAILABLE'}
        event = empty_event(eventType='check_terminal', eventId=meta['terminalEventId'], eventTimeEpoch=settled_at,
            environment=self.environment, service=meta['service'], inputKind=meta['inputKind'],
            logicalCheckRef=meta['logicalCheckRef'], lifecycleRevision=2, processingOutcome=outcome,
            accountingState='charged' if charge else 'not_charged', chargedChecks=charge,
            accessClass=meta['accessClass'], reasonCode=reasons[outcome])
        return [self.generation_guard(), self.meta_guard(meta),
                _put(self.table, event, meta['operationsExpiresAtEpoch'], self.generation)]

    def start_attempt(self, account, proof, execution_token, *, component, ordinal, provider=None, input_kind=None):
        """Only the winning conditional start obtains a permit; uncertainty never does."""
        key_id = self.a._token_parts(proof)[0]
        partition = self.a._partition(account, key_id)
        require(type(execution_token) is str and re.fullmatch('[0-9a-f]{32}', execution_token), 'OPERATIONS_SOURCE_INVALID')
        meta = self._meta(partition, proof)
        slot = f'{component}:{ordinal}:{provider or "internal"}'
        require(slot not in meta['attempts'] and len(meta['attempts']) < 10, 'OPERATIONS_ATTEMPT_ALREADY_CLAIMED')
        source = input_kind or meta['inputKind']
        service = 'url_check' if source == 'message_url' else meta['service']
        begun = empty_event(eventType='attempt_started', eventId=self._id('op1_'), eventTimeEpoch=self.a.now(),
            environment=self.environment, service=service, inputKind=source, logicalCheckRef=meta['logicalCheckRef'],
            attemptRef=self._id('at1_'), attemptKind='provider_call' if provider else 'internal_component',
            component=component, attemptOrdinal=ordinal, providerFamily=provider)
        finish_id = self._id('op1_')
        attempts = deepcopy(meta['attempts'])
        attempts[slot] = {'start': begun, 'finishEventId': finish_id}
        account_guards = self.a._account_conditions(account)
        update = self.meta_guard(meta)['ConditionCheck']
        update = dict(update, UpdateExpression='SET attempts = :attempts, revision = :next')
        update['ExpressionAttributeValues'] = update['ExpressionAttributeValues'] | {':attempts': attempts, ':next': meta['revision'] + 1}
        receipt = self.a._check(self.a.s.authority_table, {'PK': partition, 'SK': 'CHECK#' + proof},
            'recordType = :type AND #state = :admitted AND executionToken = :token AND settleByEpoch > :now AND retentionDeadlineEpoch > :now AND operationsGeneration = :generation AND operationsAcceptedAtEpoch = :accepted',
            {':type': 'V1_CHECK_RECEIPT', ':admitted': 'ADMITTED', ':token': execution_token, ':now': self.a.now(),
             ':generation': self.generation, ':accepted': meta['acceptedAtEpoch']}, {'#state': 'state'})
        self.a._transact(account_guards + [self.generation_guard(), receipt,
            {'Update': update}, _put(self.table, begun, meta['operationsExpiresAtEpoch'], self.generation)])
        # A commit followed by lost acknowledgement raises, so no permit is returned.
        require(self.a.now() < meta['operationsExpiresAtEpoch'], 'OPERATIONS_SOURCE_UNAVAILABLE')
        return AttemptPermit(account, proof, meta, begun, finish_id, execution_token)

    def finish_attempt(self, permit, *, outcome, reason, duration, request_units=None, cost_microusd=None):
        require(type(permit) is AttemptPermit, 'OPERATIONS_SOURCE_INVALID')
        meta = self._meta(permit.lookup['PK'], permit.proof)
        require(meta['logicalCheckRef'] == permit.lookup['logicalCheckRef'], 'OPERATIONS_SOURCE_INVALID')
        slot = f'{permit.started["component"]}:{permit.started["attemptOrdinal"]}:{permit.started["providerFamily"] or "internal"}'
        require(meta['attempts'].get(slot) == {'start': permit.started, 'finishEventId': permit.finish_id},
                'OPERATIONS_SOURCE_INVALID')
        event = dict(permit.started, eventType='attempt_finished', eventId=permit.finish_id,
            eventTimeEpoch=self.a.now(), attemptOutcome=outcome, reasonCode=reason,
            durationBucket=duration, providerRequestUnits=request_units, estimatedCostMicrousd=cost_microusd)
        event = validate(event)
        receipt = self.a._check(self.a.s.authority_table, {'PK': meta['PK'], 'SK': 'CHECK#' + permit.proof},
            'recordType = :type AND #state IN (:admitted, :settled) AND executionToken = :token AND retentionDeadlineEpoch > :now AND operationsGeneration = :generation AND operationsAcceptedAtEpoch = :accepted',
            {':type': 'V1_CHECK_RECEIPT', ':admitted': 'ADMITTED', ':settled': 'SETTLED', ':token': permit.execution_token, ':now': self.a.now(),
             ':generation': self.generation, ':accepted': meta['acceptedAtEpoch']}, {'#state': 'state'})
        account_guards = self.a._account_conditions(permit.account)
        update = self.meta_guard(meta)['ConditionCheck']
        update = dict(update, UpdateExpression='SET revision = :next')
        update['ExpressionAttributeValues'] = update['ExpressionAttributeValues'] | {':next': meta['revision'] + 1}
        self.a._transact(account_guards + [self.generation_guard(), receipt,
            {'Update': update}, _put(self.table, event, meta['operationsExpiresAtEpoch'], self.generation)])
        return event

    def events_for(self, account, proof):
        """Bounded source adapter; deletion/expiry/generation are rechecked after reading."""
        partition = self.a._partition(account, self.a._token_parts(proof)[0])
        meta = self._meta(partition, proof)
        receipt = self.a._get(self.a.s.authority_table, {'PK': partition, 'SK': 'CHECK#' + proof})
        require(receipt is not None, 'OPERATIONS_SOURCE_UNAVAILABLE')
        self.a._verify_token(account, proof, receipt['payloadHmac'], allow_expired=True)
        source = self.a._check(self.a.s.authority_table, {'PK': partition, 'SK': 'CHECK#' + proof},
            'recordType = :type AND #state = :currentState AND operationsGeneration = :generation AND operationsAcceptedAtEpoch = :accepted',
            {':type': 'V1_CHECK_RECEIPT', ':currentState': receipt['state'],
             ':generation': self.generation, ':accepted': meta['acceptedAtEpoch']}, {'#state': 'state'})
        require(receipt['state'] in ('ADMITTED','SETTLED'), 'OPERATIONS_SOURCE_UNAVAILABLE')
        def guards():
            return self.a._account_conditions(account) + [self.generation_guard(), source, self.meta_guard(meta)]
        self.a._transact(guards())
        page = self.a.ddb.Table(self.table).query(KeyConditionExpression='PK = :pk',
            ExpressionAttributeValues={':pk': meta['logicalCheckRef']}, ConsistentRead=True, Limit=24)
        require(not page.get('LastEvaluatedKey'), 'OPERATIONS_BATCH_BOUND')
        events = []
        for raw in page.get('Items', []):
            row = plain(raw)
            require(set(row) == {'PK', 'SK', 'recordType', 'generation', 'expiresAt', 'event'}
                    and row['recordType'] == 'V1_OPERATIONS_FACT' and row['generation'] == self.generation
                    and row['expiresAt'] == meta['operationsExpiresAtEpoch'], 'OPERATIONS_FACT_INVALID')
            event = validate(row['event'])
            require(row['PK'] == event['logicalCheckRef'] == meta['logicalCheckRef'] and row['SK'] == event['eventId']
                    and meta['acceptedAtEpoch'] <= event['eventTimeEpoch'] < meta['operationsExpiresAtEpoch'], 'OPERATIONS_FACT_INVALID')
            events.append(event)
        require(self.a.now() < meta['operationsExpiresAtEpoch'], 'OPERATIONS_SOURCE_UNAVAILABLE')
        self.a._transact(guards())
        return events

    def drain(self, partition, proof, guards, *, mode='expiry', max_pages=2, can_continue=lambda: True):
        """Erase children before the sole lookup; caller supplies existing deletion/expiry fences."""
        require(type(max_pages) is int and 1 <= max_pages <= 2 and type(guards) is list and guards
                and mode in ('expiry', 'source_expiry', 'account_deletion'), 'OPERATIONS_BATCH_BOUND')
        key = lookup_key(partition, proof)
        if self.a._get(self.a.s.authority_table, key) is None:
            source = plain(self.a._get(self.a.s.authority_table, {'PK': partition, 'SK': 'CHECK#' + proof}))
            require(type(source) is dict and source.get('recordType') == 'V1_CHECK_RECEIPT'
                    and source.get('policyVersion') == self.a.s.policy_version
                    and source.get('state') in ('ADMITTED','SETTLED'), 'OPERATIONS_CLEANUP_UNQUALIFIED')
            names = {'operationsGeneration','operationsAcceptedAtEpoch','operationsErasedGeneration'}
            if not names.intersection(source): return True  # Positively identified legacy receipt.
            require(names <= set(source) and source['operationsGeneration'] == self.generation
                    and source['operationsErasedGeneration'] == self.generation
                    and type(source['operationsAcceptedAtEpoch']) is int
                    and 0 <= source['operationsAcceptedAtEpoch'] <= self.a.now(), 'OPERATIONS_CLEANUP_UNQUALIFIED')
            return True
        meta = self._meta(partition, proof, allow_expired=True)
        require(mode in ('account_deletion', 'source_expiry') or meta['operationsExpiresAtEpoch'] <= self.a.now(), 'OPERATIONS_NOT_EXPIRED')
        if meta['lookupState'] == 'OPEN':
            closing = self.meta_guard(meta, live=False)['ConditionCheck']
            closing = dict(closing, UpdateExpression='SET lookupState = :closing, revision = :next')
            closing['ExpressionAttributeValues'] = closing['ExpressionAttributeValues'] | {':closing': 'DRAINING', ':next': meta['revision'] + 1}
            self.a._transact(guards + [self.generation_guard(), {'Update': closing}])
            meta = dict(meta, lookupState='DRAINING', revision=meta['revision'] + 1)
        for _ in range(max_pages):
            if not can_continue(): return False
            page = self.a.ddb.Table(self.table).query(KeyConditionExpression='PK = :pk',
                ExpressionAttributeValues={':pk': meta['logicalCheckRef']}, ConsistentRead=True, Limit=24)
            rows = page.get('Items', [])
            if rows:
                require(len(rows) <= 24 and len(guards) + len(rows) + 2 <= 100, 'OPERATIONS_BATCH_BOUND')
                self.a._transact(guards + [self.generation_guard(), self.meta_guard(meta, live=False)] + [
                    {'Delete': {'TableName': self.table, 'Key': {'PK': item['PK'], 'SK': item['SK']}}} for item in rows])
                continue
            # Strong empty read plus unchanged source fence; writers cannot pass
            # deletion or the original deadline while cleanup is in progress.
            deletion = self.meta_guard(meta, live=False)['ConditionCheck']
            source_key = {'PK': partition, 'SK': 'CHECK#' + proof}
            receipt = self.a._get(self.a.s.authority_table, source_key)
            remaining_guards = []
            source_conditions = []
            for guard in guards:
                check = guard.get('ConditionCheck', {})
                if check.get('TableName') == self.a.s.authority_table and check.get('Key') == source_key:
                    source_conditions.append(check)
                else: remaining_guards.append(guard)
            if receipt is None:
                require(not source_conditions, 'OPERATIONS_SOURCE_UNAVAILABLE')
                source_action = self.a._check(self.a.s.authority_table, source_key, 'attribute_not_exists(PK)')
            else:
                require(receipt.get('operationsGeneration') == self.generation
                        and receipt.get('operationsAcceptedAtEpoch') == meta['acceptedAtEpoch'], 'OPERATIONS_SOURCE_INVALID')
                update = {'TableName': self.a.s.authority_table, 'Key': source_key,
                    'UpdateExpression': 'SET operationsErasedGeneration = :opsGen',
                    'ConditionExpression': 'operationsGeneration = :opsGen AND operationsAcceptedAtEpoch = :opsAccepted AND recordType = :opsReceipt AND policyVersion = :opsPolicy AND #opsState IN (:opsAdmitted, :opsSettled)',
                    'ExpressionAttributeNames': {'#opsState': 'state'},
                    'ExpressionAttributeValues': {':opsGen': self.generation, ':opsAccepted': meta['acceptedAtEpoch'],
                        ':opsReceipt': 'V1_CHECK_RECEIPT', ':opsPolicy': self.a.s.policy_version,
                        ':opsAdmitted': 'ADMITTED', ':opsSettled': 'SETTLED'}}
                for check in source_conditions:
                    update['ConditionExpression'] += ' AND (' + check['ConditionExpression'] + ')'
                    update['ExpressionAttributeValues'].update(check.get('ExpressionAttributeValues', {}))
                    update.setdefault('ExpressionAttributeNames', {}).update(check.get('ExpressionAttributeNames', {}))
                source_action = {'Update': update}
            self.a._transact(remaining_guards + [self.generation_guard(), source_action, {'Delete': deletion}])
            return True
        return False
