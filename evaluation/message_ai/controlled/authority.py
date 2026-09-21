"""Experiment-wide local authorization and conservative durable reservations.

Ordinary execution never initializes/resets this database. A trusted operator can
still rewrite local files/code; this is not a hostile-administrator security bound.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sqlite3
import stat
import time

from .. import corpus
from ..profile import (ATTEMPT_CAP, BUDGET_NANO, MODELS, SPLIT_CAPS, canonical,
                       digest, require, reserve_cost, EvaluationError)
from . import protocol

EVIDENCE = {'operatorApproval', 'corpusPermission', 'providerAccess', 'providerRetention',
            'generationPricing', 'countPricing', 'countCompatibility'}


def bounded_json(path):
    try:
        with Path(path).open('rb') as handle:
            raw = handle.read(262145)
        require(len(raw) <= 262144, 'AUTHORIZATION_INVALID')
        return json.loads(raw, object_pairs_hook=corpus.unique_pairs)
    except (OSError, ValueError, TypeError, RecursionError, UnicodeError):
        raise EvaluationError('AUTHORIZATION_INVALID') from None


def private(path, directory=False):
    path = Path(path)
    require(not path.is_symlink(), 'AUTHORITY_PRIVATE_STATE_REQUIRED')
    info = path.stat()
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
            and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)), 'AUTHORITY_PRIVATE_STATE_REQUIRED')


def approved_registry():
    return bounded_json(Path(__file__).with_name('approved_experiments.json'))


def validate_authorization(value, manifest, directory, *, now=None, execution=False, audit=False):
    keys = {'schemaVersion', 'experimentId', 'executionMode', 'corpusSha256', 'profiles',
            'authorityPathSha256', 'hostSha256', 'expiresAt', 'generationAttemptCap',
            'countAttemptCap', 'budgetNano', 'countMaxChargeNano', 'evidence', 'credentialSha256'}
    require(type(value) is dict and set(value) == keys and type(value['schemaVersion']) is int
            and value['schemaVersion'] == 1, 'AUTHORIZATION_INVALID')
    require(type(value['experimentId']) is str and re.fullmatch(r'[a-z0-9-]{1,64}', value['experimentId'])
            and value['executionMode'] in ('controlled_live', 'fault_test'), 'AUTHORIZATION_INVALID')
    if not audit:
        registry = approved_registry()
        require(type(registry) is dict and registry.get(value['experimentId']) == digest(value), 'EXPERIMENT_NOT_APPROVED')
    corpus.validate(manifest)
    require(value['corpusSha256'] == digest(manifest), 'CORPUS_IDENTITY_MISMATCH')
    require(type(value['profiles']) is dict and value['profiles'] and set(value['profiles']) <= set(MODELS)
            and all(v == digest(protocol.profile(m)) for m, v in value['profiles'].items()), 'PROFILE_IDENTITY_MISMATCH')
    require(value['authorityPathSha256'] == digest(str(Path(directory).resolve())) and
            value['hostSha256'] == digest(platform.node()), 'AUTHORITY_LOCATION_MISMATCH')
    require(type(value['credentialSha256']) is str and re.fullmatch('[0-9a-f]{64}', value['credentialSha256']), 'AUTHORIZATION_INVALID')
    for field, maximum in (('generationAttemptCap', ATTEMPT_CAP), ('countAttemptCap', 660), ('budgetNano', BUDGET_NANO)):
        require(type(value[field]) is int and 0 < value[field] <= maximum, 'AUTHORIZATION_INVALID')
    require(type(value['countMaxChargeNano']) is int and 0 <= value['countMaxChargeNano'] <= value['budgetNano'], 'COUNT_PRICING_UNRESOLVED')
    require(type(value['expiresAt']) is int and 0 < value['expiresAt'] < 10**11, 'AUTHORIZATION_INVALID')
    if execution:
        require((time.time() if now is None else now) < value['expiresAt'], 'AUTHORIZATION_EXPIRED')
    require(type(value['evidence']) is dict and set(value['evidence']) == EVIDENCE, 'EVIDENCE_UNRESOLVED')
    for name, expected in value['evidence'].items():
        require(type(expected) is str and re.fullmatch('[0-9a-f]{64}', expected), 'EVIDENCE_UNRESOLVED')
        path = Path(directory) / 'evidence' / (name + '.json')
        private(path)
        with path.open('rb') as handle:
            raw = handle.read(262145)
        require(len(raw) <= 262144 and hashlib.sha256(raw).hexdigest() == expected, 'EVIDENCE_UNRESOLVED')
    return value


def provision_authority(directory, authorization, manifest):
    """Trusted one-time administrative API behind the explicit approved init CLI.

    Requires a reviewed registry pin and private preexisting evidence/directory.
    Reprovisioning/deleting authoritative files is not an ordinary recovery path.
    """
    directory = Path(directory)
    private(directory, True)
    validate_authorization(authorization, manifest, directory, execution=True)
    # This marker survives a lost database; init cannot silently reset its budget.
    marker = directory / 'provisioned'
    fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(digest(authorization)); handle.flush(); os.fsync(handle.fileno())
    path = directory / 'authority.sqlite3'
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    connection = sqlite3.connect(path)
    try:
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('CREATE TABLE authority (id INTEGER PRIMARY KEY CHECK(id=1), authorization TEXT NOT NULL, hash TEXT NOT NULL, halted INTEGER NOT NULL)')
        connection.execute('CREATE TABLE calls (key TEXT PRIMARY KEY, casekey TEXT NOT NULL, phase TEXT NOT NULL, model TEXT NOT NULL, language TEXT NOT NULL, split TEXT NOT NULL, binding TEXT NOT NULL, reserved INTEGER NOT NULL, state TEXT NOT NULL, result TEXT, hash TEXT)')
        connection.execute('INSERT INTO authority VALUES (1,?,?,0)', (canonical(authorization), digest(authorization)))
        connection.commit()
    finally:
        connection.close()


def validate_result(phase, value):
    require(type(value) is dict and set(value) == {'outcome', 'inputTokens', 'usage', 'assessment', 'reasonCodes', 'responseReceived'}, 'AUTHORITY_CORRUPT')
    outcomes = {'count': {'counted', 'input_limit', 'count_invalid', 'transport_failure'},
                'generation': {'valid', 'assessment_rejected', 'transport_failure', 'usage_mismatch'}}
    require(value['outcome'] in outcomes[phase] and type(value['responseReceived']) is bool, 'AUTHORITY_CORRUPT')
    count = value['inputTokens']
    require((type(count) is int and 0 < count <= 2_000_000) if phase == 'count' and value['outcome'] in ('counted', 'input_limit') else count is None, 'AUTHORITY_CORRUPT')
    if value['usage'] is not None:
        data = value['usage']
        require(phase == 'generation' and type(data) is dict and set(data) == {'inputTokens', 'outputTokens', 'cachedTokens', 'reasoningTokens', 'tokenCostUpperNano'}
                and all(type(n) is int and 0 <= n <= 10**12 for n in data.values()), 'AUTHORITY_CORRUPT')
    require(value['assessment'] in (None, 'warning', 'no_warning', 'abstain'), 'AUTHORITY_CORRUPT')
    require((value['assessment'] is not None) == (phase == 'generation' and value['outcome'] == 'valid'), 'AUTHORITY_CORRUPT')
    require(type(value['reasonCodes']) is list and len(value['reasonCodes']) <= 5 and all(type(x) is str and x in corpus.AI_REASONS for x in value['reasonCodes'])
            and len(set(value['reasonCodes'])) == len(value['reasonCodes']) and bool(value['reasonCodes']) == (value['assessment'] == 'warning'), 'AUTHORITY_CORRUPT')


class Authority:
    def __init__(self, directory, authorization, manifest, *, clock=time.time, audit=False):
        self.directory = Path(directory)
        self.authorization, self.manifest, self.clock, self.audit = authorization, manifest, clock, audit
        private(self.directory, True)
        validate_authorization(authorization, manifest, directory, audit=audit)
        private(self.directory / 'provisioned')
        with (self.directory / 'provisioned').open('rb') as marker:
            require(marker.read(65) == digest(authorization).encode(), 'AUTHORITY_CORRUPT')
        path = self.directory / 'authority.sqlite3'
        private(path)  # Missing/corrupt authority never causes implicit creation.
        self.db = sqlite3.connect(path.resolve().as_uri() + ('?mode=ro' if audit else '?mode=rw'), uri=True, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA synchronous=FULL')
        if not audit:
            self.db.execute('PRAGMA journal_mode=DELETE')
        try:
            with self.transaction():
                self._validate()
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        try:
            self.db.execute('BEGIN' if self.audit else 'BEGIN IMMEDIATE')
            yield
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def _validate(self):
        require(self.db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'AUTHORITY_CORRUPT')
        rows = self.db.execute('SELECT * FROM authority').fetchall()
        require(len(rows) == 1 and rows[0]['authorization'] == canonical(self.authorization)
                and rows[0]['hash'] == digest(self.authorization) and rows[0]['halted'] in (0, 1), 'AUTHORITY_CORRUPT')
        calls = self.db.execute('SELECT * FROM calls').fetchall()
        cost, counts, buckets = 0, {'count': 0, 'generation': 0}, {}
        for row in calls:
            require(row['phase'] in counts and row['model'] in self.authorization['profiles'] and row['language'] in ('en', 'es')
                    and row['split'] in SPLIT_CAPS and row['state'] in ('reserved', 'finished'), 'AUTHORITY_CORRUPT')
            for field in ('key', 'casekey', 'binding'):
                require(type(row[field]) is str and re.fullmatch('[0-9a-f]{64}', row[field]), 'AUTHORITY_CORRUPT')
            require(row['key'] == digest([row['casekey'], row['phase']]), 'AUTHORITY_CORRUPT')
            amount = self.authorization['countMaxChargeNano'] if row['phase'] == 'count' else reserve_cost(row['model'])
            require(row['reserved'] == amount, 'AUTHORITY_CORRUPT')
            if row['state'] == 'reserved':
                require(row['result'] is row['hash'] is None, 'AUTHORITY_CORRUPT')
            else:
                result = json.loads(row['result'], object_pairs_hook=corpus.unique_pairs)
                require(digest(result) == row['hash'], 'AUTHORITY_CORRUPT')
                validate_result(row['phase'], result)
            counts[row['phase']] += 1; cost += amount
            bucket = (row['model'], row['split'], row['phase'])
            buckets[bucket] = buckets.get(bucket, 0) + 1
        require(cost <= self.authorization['budgetNano'] and counts['count'] <= self.authorization['countAttemptCap']
                and counts['generation'] <= self.authorization['generationAttemptCap']
                and all(n <= SPLIT_CAPS[split] for (_, split, _), n in buckets.items()), 'AUTHORITY_CORRUPT')
        return bool(rows[0]['halted'])

    def get(self, casekey, phase):
        with self.transaction():
            self._validate()
            row = self.db.execute('SELECT * FROM calls WHERE key=?', (digest([casekey, phase]),)).fetchone()
            return dict(row) if row else None

    def reserve(self, casekey, phase, model, language, split, binding):
        require(not self.audit, 'READ_ONLY_AUTHORITY')
        validate_authorization(self.authorization, self.manifest, self.directory, now=self.clock(), execution=True)
        require(phase in ('count', 'generation') and model in self.authorization['profiles'] and language in ('en', 'es') and split in SPLIT_CAPS, 'INVALID_RESERVATION')
        require(all(type(x) is str and re.fullmatch('[0-9a-f]{64}', x) for x in (casekey, binding)), 'INVALID_RESERVATION')
        key = digest([casekey, phase])
        with self.transaction():
            require(not self._validate(), 'EXPERIMENT_HALTED')
            row = self.db.execute('SELECT * FROM calls WHERE key=?', (key,)).fetchone()
            if row:
                require((row['binding'], row['model'], row['language'], row['split']) == (binding, model, language, split), 'ATTEMPT_IDENTITY_MISMATCH')
                return False
            if phase == 'generation':
                count = self.db.execute('SELECT * FROM calls WHERE key=?', (digest([casekey, 'count']),)).fetchone()
                require(count is not None and count['binding'] == binding and count['state'] == 'finished', 'COUNT_NOT_ADMITTED')
                result = json.loads(count['result'])
                require(result['outcome'] == 'counted' and result['inputTokens'] <= 8192, 'COUNT_NOT_ADMITTED')
            cap = self.authorization['countAttemptCap' if phase == 'count' else 'generationAttemptCap']
            used = self.db.execute('SELECT COUNT(*) FROM calls WHERE phase=?', (phase,)).fetchone()[0]
            bucket = self.db.execute('SELECT COUNT(*) FROM calls WHERE model=? AND split=? AND phase=?', (model, split, phase)).fetchone()[0]
            reserved = self.db.execute('SELECT COALESCE(SUM(reserved),0) FROM calls').fetchone()[0]
            amount = self.authorization['countMaxChargeNano'] if phase == 'count' else reserve_cost(model)
            require(used < cap and bucket < SPLIT_CAPS[split] and reserved + amount <= self.authorization['budgetNano'], 'EXPERIMENT_CAP_REACHED')
            self.db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL)',
                            (key, casekey, phase, model, language, split, binding, amount, 'reserved'))
            return True

    def finish(self, casekey, phase, result, *, halt=False):
        require(not self.audit, 'READ_ONLY_AUTHORITY')
        validate_result(phase, result)
        with self.transaction():
            self._validate()
            key = digest([casekey, phase])
            row = self.db.execute('SELECT * FROM calls WHERE key=?', (key,)).fetchone()
            require(row is not None, 'RESERVATION_MISSING')
            if row['state'] == 'finished':
                require(row['result'] == canonical(result), 'SETTLEMENT_CONFLICT')
            else:
                self.db.execute('UPDATE calls SET state=?,result=?,hash=? WHERE key=?', ('finished', canonical(result), digest(result), key))
            if halt:
                self.db.execute('UPDATE authority SET halted=1')

    def snapshot(self):
        with self.transaction():
            halted = self._validate()
            return halted, [dict(row) for row in self.db.execute('SELECT * FROM calls ORDER BY key')]

    def dispatch_gate(self):
        require(not self.audit, 'READ_ONLY_AUTHORITY')
        with self.transaction():
            require(not self._validate(), 'EXPERIMENT_HALTED')
            # The lock itself can wait. Refresh expiry/revocation after admission
            # to the transaction, not using a decision made before that wait.
            validate_authorization(self.authorization, self.manifest, self.directory, now=self.clock(), execution=True)

    def approval_status(self):
        try:
            registry = approved_registry()
            if type(registry) is not dict or registry.get(self.authorization['experimentId']) != digest(self.authorization):
                return 'revoked_or_unapproved'
        except Exception:
            return 'approval_registry_unavailable'
        return 'expired' if self.clock() >= self.authorization['expiresAt'] else 'currently_approved'
