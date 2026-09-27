"""Isolated actual consent producer/cleanup composition; transport delivery is injected.

Never imported by an application handler. The outer runner verifies dedicated
resources and package hashes before this module can mutate synthetic fixtures.
"""
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
import importlib
import json
import sys
from types import SimpleNamespace

from shared_campaign_recovery import records as R
from shared_research_consent import CURRENT_NOTICE, CURRENT_POLICY

CASES = ('actual_withdrawal_complete_replay', 'actual_withdrawal_lost_ack',
         'actual_withdrawal_delayed_replay')
JOIN = '11111111-1111-4111-8111-111111111111'
EVENT = '22222222-2222-4222-8222-222222222222'
DELAYED = '33333333-3333-4333-8333-333333333333'


@contextmanager
def participation(runner):
    names = ('config', 'errors')
    previous = {name: sys.modules.get(name) for name in names}
    try:
        config = runner.worker_module('campaign_participation', 'config')
        for name, value in {
            'USERS_TABLE_NAME': runner.tables['users'],
            'DELETION_LEDGER_TABLE_NAME': runner.tables['ledger'],
            'ENVIRONMENT': 'dev', 'NOTICE_VERSION': CURRENT_NOTICE,
            'POLICY_VERSION': CURRENT_POLICY, 'CONSENT_INDEPENDENCE_ENABLED': True,
            'CAMPAIGN_RECOVERY_WRITES_ENABLED': True,
        }.items():
            setattr(config, name, value)
        sys.modules['config'] = config
        sys.modules['errors'] = runner.worker_module('campaign_participation', 'errors')
        service = runner.worker_module('campaign_participation', 'service')
        # The service keeps its explicit config reference; do not leak its
        # import aliases into subsequently imported cleanup handlers.
        for name, value in previous.items():
            if value is None: sys.modules.pop(name, None)
            else: sys.modules[name] = value
        original = service.dynamodb
        service.dynamodb = runner.d

        class Table:
            def __init__(self, kind): self.kind = kind
            def get_item(self, **kwargs):
                key = kwargs['Key']
                row = runner.get(self.kind, key['PK'], key['SK'])
                return {'Item': row} if row else {}

        service.users_table = Table('users')
        service.deletion_ledger_table = Table('ledger')
        try:
            yield service
        finally:
            original.close()
            service.resource.meta.client.close()
    finally:
        for name, value in previous.items():
            if value is None: sys.modules.pop(name, None)
            else: sys.modules[name] = value


@contextmanager
def cleanup_handler(runner):
    app = importlib.import_module('app')
    settings = {
        'APP_ENVIRONMENT': 'dev', 'CAMPAIGN_SCHEMA_VERSION': 1,
        'PIPELINE_TABLE_NAME': runner.tables['pipeline'],
        'INTELLIGENCE_TABLE_NAME': runner.tables['pipeline'],
        'USERS_TABLE_NAME': runner.tables['users'],
        'DELETION_LEDGER_TABLE_NAME': runner.tables['ledger'],
        'PARTICIPATION_ITEM_SK': 'CAMPAIGN_PARTICIPATION',
        'PARTICIPATION_AUDIT_DAYS': 400, 'CONTRIBUTOR_RECOVERY_DAYS': 7,
        'TRANSIENT_RETENTION_DAYS': 21,
        'CAMPAIGN_DELETION_STREAM_ENABLED': True, 'CAMPAIGN_COMPLETION_ENABLED': True,
        'CAMPAIGN_RECOVERY_ENABLED': True,
        'CAMPAIGN_COMPLETION_MANIFEST_SHA256': 'c' * 64,
        'CAMPAIGN_COMPLETION_INVENTORY_REVISION': 1,
        'CAMPAIGN_RECOVERY_MANIFEST_SHA256': 'b' * 64,
        'CAMPAIGN_RECOVERY_INVENTORY_REVISION': 1,
        'CAMPAIGN_LOCATOR_MANIFEST_SHA256': 'a' * 64,
        'CAMPAIGN_LOCATOR_INVENTORY_REVISION': 1,
        'CAMPAIGN_RECOVERY_INDEX_NAME': R.INDEX,
    }
    old = {name: getattr(app.config, name) for name in settings}
    clients = app.dynamodb, app.kms
    try:
        for name, value in settings.items(): setattr(app.config, name, value)
        app.dynamodb, app.kms = runner.d, runner.kms
        yield app
    finally:
        for name, value in old.items(): setattr(app.config, name, value)
        app.dynamodb, app.kms = clients


def run(runner, case, require):
    require(case in CASES)
    r = runner
    # Retain qualified synthetic inventory/keys, discard the old account fixture.
    for row in r.rows('ledger'):
        if row['PK'] == r.cmd['PK']: r.delete('ledger', row['PK'], row['SK'])
    for period in (r.period - 1, r.period):
        r.delete('pipeline', r.partition(period), 'TOMBSTONE')
    profile = {'PK': 'USER#' + r.subject, 'SK': 'PROFILE', 'sub': r.subject,
               'status': 'ACTIVE', 'ageVerified': True}
    r.put('users', profile)
    preserved = {'PK': 'UNRELATED#fixture', 'SK': 'KEEP', 'value': 'untouched'}
    r.put('pipeline', preserved)
    publisher = r.worker_module('campaign_observation_publisher', 'service')
    cluster = r.worker_module('campaign_cluster_aggregator', 'service')
    cluster._load_candidates = lambda *_: []  # No real candidate-index discovery claim.
    sent = []
    queue = SimpleNamespace(send_message=lambda **kwargs: sent.append(kwargs))
    with participation(r) as producer:
        joined = producer.update_participation(r.subject, {
            'schemaVersion': 2, 'action': 'join', 'operationId': JOIN,
            'noticeVersion': CURRENT_NOTICE, 'expectedStateVersion': 0,
        }, now_epoch=r.when - 30)
        require(joined['state'] == 'enrolled' and joined['stateVersion'] == 1)
        item = {
            'schemaVersion': 1, 'recordVersion': 1, 'environment': 'dev',
            'statisticsEventId': EVENT, 'accountId': r.subject,
            'campaignConsentGranted': True, 'consentEpochId': joined['consentEpochId'],
            'noticeVersion': CURRENT_NOTICE, 'observedAtEpoch': r.when - 10,
            'expiresAt': r.now + 1000,
            'appFeatures': {'schemaVersion': 1, 'extractorVersion': 'android-1.0.0',
                'languageId': 'en', 'taxonomyBucket': 'advance_fee', 'vector': [1.0, 0.0],
                'lexicalFingerprint': ['0123456789abcdef'], 'signalIds': ['payment_request'],
                'indicatorIds': ['payment.crypto'], 'confidence': 0.9},
        }
        def publish(value):
            return publisher.publish_observation(value,
                pipeline_table_name=r.tables['pipeline'], users_table_name=r.tables['users'],
                deletion_ledger_table_name=r.tables['ledger'], cluster_queue_url='synthetic',
                hmac_key_id=r.config['key'], transient_retention_days=21,
                dynamodb_client=r.d, kms_client=r.kms, sqs_client=queue, now_epoch=r.now,
                locator_manifest_sha256='a' * 64, locator_inventory_revision=1)
        def aggregate(body):
            return cluster.process_message(body, environment='dev', schema_version=1,
                table_name=r.tables['pipeline'], retention_days=21, max_submissions=3,
                dynamodb=r.d, now_epoch=r.now, locator_manifest_sha256='a' * 64,
                locator_inventory_revision=1, users_table_name=r.tables['users'],
                deletion_ledger_table_name=r.tables['ledger'], outbox_table_name=r.tables['users'])
        for event in (EVENT, DELAYED):
            row = item | {'statisticsEventId': event}
            r.put('users', json.loads(json.dumps(row | {'PK': 'EVENT#' + event,
                'SK': 'OBSERVATION_READY', 'eventType': 'campaign.observation.ready'}), parse_float=Decimal))
            require(publish(row) == 'published')
        require(len(sent) == 2)
        body = sent[0]['MessageBody']
        aggregate(body)
        clustered = r.get('pipeline', 'EVENT#' + EVENT, 'CLUSTERED')
        require(clustered is not None)
        candidate = 'CANDIDATE#' + clustered['candidateId']
        require(r.get('pipeline', candidate, 'CONTRIB#' + r.token) is not None)
        payload = {'schemaVersion': 2, 'action': 'withdraw',
            'operationId': r.cmd['operationId'], 'noticeVersion': CURRENT_NOTICE,
            'expectedStateVersion': 1}
        # Inject a lost response only after the real atomic producer transaction.
        if case == 'actual_withdrawal_lost_ack':
            class Lost:
                def __getattr__(self, name): return getattr(r.d, name)
                def transact_write_items(self, **kwargs):
                    r.d.transact_write_items(**kwargs)
                    raise RuntimeError('SYNTHETIC_LOST_ACK')
            producer.dynamodb = Lost()
            try:
                producer.update_participation(r.subject, payload, now_epoch=r.when)
            except RuntimeError as exc: require(str(exc) == 'SYNTHETIC_LOST_ACK')
            else: require(False)
            producer.dynamodb = r.d
            committed = r.snapshot()
            result = producer.update_participation(r.subject, payload, now_epoch=r.when)
            require(r.snapshot() == committed)
        else:
            result = producer.update_participation(r.subject, payload, now_epoch=r.when)
        require(result['state'] == 'withdrawal_pending' and result['stateVersion'] == 2)
        command = r.get('ledger', r.cmd['PK'], 'CAMPAIGN_WITHDRAWAL#' + payload['operationId'])
        require(command is not None and command['status'] == 'PENDING')
        require(r.get('ledger', command['PK'], R.JOB_PREFIX + command['operationId']) is not None)
        require(r.get('ledger', command['PK'], R.CONTROL_SK)['pendingJobs'] == 1)
        r.cmd = deepcopy(command)
        pending = r.snapshot()
        require(publish(item) == 'participation-suppressed')
        require(aggregate(sent[1]['MessageBody']) == 'participation-suppressed')
        require(r.snapshot() == pending and len(sent) == 2)
        stream = {'Records': [{'eventName': 'INSERT', 'eventSource': 'aws:dynamodb',
            'eventSourceARN': f"arn:aws:dynamodb:us-east-1:107827791950:table/{r.tables['ledger']}/stream/2026-09-24T00:00:00.000",
            'dynamodb': {'SequenceNumber': '1', 'NewImage': R.wire(command)}}]}
        with cleanup_handler(r) as app:
            for _ in range(8):
                try:
                    outcome = app.lambda_handler(stream, r.context)
                    require(outcome['completed'] == 1)
                    break
                except RuntimeError as exc:
                    require(str(exc) == 'CAMPAIGN_DELETION_RECONCILIATION_REQUIRED')
                    require(r.get('ledger', command['PK'], command['SK'])['status'] == 'PENDING')
            else: require(False)
            final = r.snapshot()
            require(app.lambda_handler(stream, r.context)['completed'] == 1)
            require(r.snapshot() == final)
        terminal = r.get('ledger', command['PK'], command['SK'])
        require(terminal['status'] == 'COMPLETE')
        require(r.get('ledger', command['PK'], R.JOB_PREFIX + command['operationId']) is None)
        require(r.get('ledger', command['PK'], R.CONTROL_SK) is None)
        state = r.get('users', profile['PK'], 'CAMPAIGN_PARTICIPATION')
        require(state['state'] == 'withdrawn' and state['stateVersion'] == 3)
        audits = [row for row in r.rows('users') if row.get('eventType') == 'campaign.participation.withdrawal_completed']
        require(len(audits) == 1 and audits[0]['operationId'] == command['operationId'])
        require(audits[0]['expiresAt'] == terminal['completedAtEpoch'] + 400 * 86400)
        require(r.get('users', profile['PK'], 'PROFILE') == profile)
        require(r.get('pipeline', preserved['PK'], preserved['SK']) == preserved)
        require(r.get('pipeline', candidate, 'SUMMARY') is None)
        require(r.get('pipeline', candidate, 'CONTRIB#' + r.token) is None)
        require(not any(row['PK'] in {r.partition(r.period - 1), r.partition(r.period)}
            and row['SK'].startswith('LOCATOR#') for row in r.rows('pipeline')))
        for event in (EVENT, DELAYED):
            for sk in ('FEATURE', 'DEDUPE', 'CLUSTERED'):
                require(r.get('pipeline', 'EVENT#' + event, sk) is None)
        # Replaying the captured outbox and cluster/DLQ-shaped bodies invokes real
        # consumers, but does not claim SQS retention/redrive/delivery validation.
        for _ in range(3 if case == 'actual_withdrawal_delayed_replay' else 1):
            require(publish(item) == 'participation-suppressed')
            require(publish(item | {'statisticsEventId': DELAYED}) == 'participation-suppressed')
            for message in sent: require(aggregate(message['MessageBody']) == 'missing')
            replay = producer.update_participation(r.subject, payload, now_epoch=r.now)
            require(replay['state'] == 'withdrawn' and not replay['contributionEligible'])
            require(r.snapshot() == final and len(sent) == 2)
