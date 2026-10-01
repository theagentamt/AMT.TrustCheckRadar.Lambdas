"""Private, durable, conservative simulation reservations; never vendor billing."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat

from shared_message_contract.validation_v2 import AI_REASONS
from .corpus import unique_pairs
from .profile import (ATTEMPT_CAP, BUDGET_NANO, SPLIT_CAPS, MODELS, EvaluationError,
                      canonical, digest, require, reserve_cost)

OUTCOMES = {'not_dispatched', 'valid', 'schema_rejected', 'refused', 'truncated',
            'deadline', 'provider_failure'}
RESULT_KEYS = {'responseOutcome', 'assessment', 'reasonCodes', 'labelMatch', 'reviewStatus',
               'verdict', 'processingOutcome', 'coverage', 'independentThreatMatchPresent'}


def validate_result(value, dispatched):
    require(type(value) is dict and set(value) == RESULT_KEYS, 'LEDGER_INVALID')
    require(value['responseOutcome'] in OUTCOMES, 'LEDGER_INVALID')
    require((value['responseOutcome'] != 'not_dispatched') == dispatched, 'LEDGER_INVALID')
    require(value['assessment'] in (None, 'warning', 'no_warning', 'abstain'), 'LEDGER_INVALID')
    require((value['assessment'] is not None) == (value['responseOutcome'] == 'valid'), 'LEDGER_INVALID')
    require(type(value['reasonCodes']) is list and len(value['reasonCodes']) <= 5 and
            all(type(x) is str and x in AI_REASONS for x in value['reasonCodes']) and
            len(set(value['reasonCodes'])) == len(value['reasonCodes']), 'LEDGER_INVALID')
    require(bool(value['reasonCodes']) == (value['assessment'] == 'warning'), 'LEDGER_INVALID')
    require(value['labelMatch'] is None if value['assessment'] is None else type(value['labelMatch']) is bool, 'LEDGER_INVALID')
    require(value['reviewStatus'] in ('engineering_only', 'independently_adjudicated'), 'LEDGER_INVALID')
    require(value['verdict'] in ('unknown', 'suspicious', 'high_risk', 'no_known_threat_detected'), 'LEDGER_INVALID')
    require(value['processingOutcome'] in ('complete', 'partial', 'inconclusive', 'blocked', 'unavailable'), 'LEDGER_INVALID')
    require(value['coverage'] in ('supported_checks_complete', 'limited', 'not_assessed'), 'LEDGER_INVALID')
    require(type(value['independentThreatMatchPresent']) is bool, 'LEDGER_INVALID')


def private_directory(path):
    path = Path(path)
    require(not path.is_symlink(), 'PRIVATE_DIRECTORY_REQUIRED')
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and
            stat.S_IMODE(info.st_mode) == 0o700, 'PRIVATE_DIRECTORY_REQUIRED')
    return path


class Ledger:
    def __init__(self, directory, config):
        self.directory = private_directory(directory)
        self.config = config
        self.path = self.directory / 'journal.sqlite3'
        require(not self.path.is_symlink(), 'LEDGER_INVALID')
        created = False
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            created = True
        except FileExistsError:
            pass
        info = self.path.stat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and
                stat.S_IMODE(info.st_mode) == 0o600, 'PRIVATE_LEDGER_REQUIRED')
        self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute('PRAGMA journal_mode=DELETE')
            self.db.execute('PRAGMA synchronous=FULL')
            with self.transaction():
                if created:
                    self.db.execute('CREATE TABLE run (id INTEGER PRIMARY KEY CHECK(id=1), config TEXT NOT NULL, hash TEXT NOT NULL)')
                    self.db.execute('CREATE TABLE attempts (key TEXT PRIMARY KEY, model TEXT NOT NULL, language TEXT NOT NULL, split TEXT NOT NULL, dispatched INTEGER NOT NULL, reserved INTEGER NOT NULL, state TEXT NOT NULL, result TEXT, hash TEXT)')
                    self.db.execute('INSERT INTO run VALUES (1,?,?)', (canonical(config), digest(config)))
                self._validate()
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        try:
            self.db.execute('BEGIN IMMEDIATE')
            yield
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def _validate(self):
        require(self.db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'LEDGER_INVALID')
        rows = self.db.execute('SELECT * FROM run').fetchall()
        require(len(rows) == 1 and rows[0]['id'] == 1 and rows[0]['config'] == canonical(self.config)
                and rows[0]['hash'] == digest(self.config), 'RUN_IDENTITY_MISMATCH')
        rows = self.db.execute('SELECT * FROM attempts').fetchall()
        attempts, cost, buckets = 0, 0, {}
        for row in rows:
            require(type(row['key']) is str and len(row['key']) == 64 and
                    all(c in '0123456789abcdef' for c in row['key']), 'LEDGER_INVALID')
            require(row['model'] in MODELS and row['language'] in ('en', 'es') and
                    row['split'] in SPLIT_CAPS and row['dispatched'] in (0, 1), 'LEDGER_INVALID')
            require(row['reserved'] == (reserve_cost(row['model']) if row['dispatched'] else 0), 'LEDGER_INVALID')
            require(row['state'] in ('reserved', 'finished'), 'LEDGER_INVALID')
            if row['state'] == 'reserved':
                require(row['result'] is None and row['hash'] is None, 'LEDGER_INVALID')
            else:
                require(type(row['result']) is str, 'LEDGER_INVALID')
                value = json.loads(row['result'], object_pairs_hook=unique_pairs)
                require(row['hash'] == digest(value), 'LEDGER_INVALID')
                validate_result(value, bool(row['dispatched']))
            attempts += row['dispatched']; cost += row['reserved']
            bucket = (row['model'], row['split'])
            buckets[bucket] = buckets.get(bucket, 0) + row['dispatched']
        require(attempts <= ATTEMPT_CAP and cost <= BUDGET_NANO and
                all(count <= SPLIT_CAPS[split] for (_, split), count in buckets.items()), 'LEDGER_INVALID')

    def contains(self, key):
        with self.transaction():
            self._validate()
            return self.db.execute('SELECT 1 FROM attempts WHERE key=?', (key,)).fetchone() is not None

    def reserve(self, key, model, language, split, *, dispatched):
        require(model in MODELS and language in ('en', 'es') and split in SPLIT_CAPS, 'INVALID_RESERVATION')
        require(type(dispatched) is bool and type(key) is str and len(key) == 64 and
                all(c in '0123456789abcdef' for c in key), 'INVALID_RESERVATION')
        with self.transaction():
            self._validate()
            previous = self.db.execute('SELECT * FROM attempts WHERE key=?', (key,)).fetchone()
            if previous is not None:
                require((previous['model'], previous['language'], previous['split'], previous['dispatched']) ==
                        (model, language, split, int(dispatched)), 'RESERVATION_CONFLICT')
                return False
            count, cost = self.db.execute('SELECT COALESCE(SUM(dispatched),0),COALESCE(SUM(reserved),0) FROM attempts').fetchone()
            bucket = self.db.execute('SELECT COALESCE(SUM(dispatched),0) FROM attempts WHERE model=? AND split=?', (model, split)).fetchone()[0]
            amount = reserve_cost(model) if dispatched else 0
            require(count + int(dispatched) <= ATTEMPT_CAP and cost + amount <= BUDGET_NANO and
                    bucket + int(dispatched) <= SPLIT_CAPS[split], 'EXPERIMENT_CAP_REACHED')
            self.db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,?,NULL,NULL)',
                            (key, model, language, split, int(dispatched), amount, 'reserved'))
            return True

    def finish(self, key, value):
        with self.transaction():
            self._validate()
            row = self.db.execute('SELECT * FROM attempts WHERE key=?', (key,)).fetchone()
            require(row is not None, 'RESERVATION_MISSING')
            validate_result(value, bool(row['dispatched']))
            if row['state'] == 'finished':
                require(row['result'] == canonical(value), 'SETTLEMENT_CONFLICT')
                return
            # Conservative: never release reservations, even after a simulated
            # success. Unknown/crashed attempts therefore cannot reset budgets.
            self.db.execute('UPDATE attempts SET state=?,result=?,hash=? WHERE key=?',
                            ('finished', canonical(value), digest(value), key))

    def snapshot(self):
        with self.transaction():
            self._validate()
            return [dict(r) for r in self.db.execute('SELECT * FROM attempts ORDER BY key')]
