"""Independent offline scope boundaries; no live configuration qualification."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_store import client, TABLE, DAY, FIRST, summary, rows
from test_scoped_store import scope, scoped, store
from operations_reporting.revisions import (
    REVISION_V2, SUMMARY_V2, RevisionFault, plan_revision, validate_revision,
    validate_summary, _sha,
)
from operations_reporting.scope import ScopeFault, validate_scope
from operations_reporting.store import AggregateStore, AggregateStoreFault


@pytest.mark.parametrize('field,value', [
    ('schemaVersion', 1.0), ('schemaVersion', True),
    ('generation', '11111111-1111-4111-8111-111111111111\n'),
    ('generation', '11111111-1111-4111-C111-111111111111'),
    ('population', ['environment_customer']), ('sourceContract', None),
    ('coverage', ['environment_completeness_unqualified']),
])
def test_scope_noncanonical_values_fail_finite(field, value):
    descriptor = scope() | {field: value}
    with pytest.raises(ScopeFault, match='^INVALID_REPORT_SCOPE$'):
        validate_scope(descriptor, environment='dev')
    report = summary() | {'schemaVersion': SUMMARY_V2, 'sourceScope': descriptor}
    with pytest.raises(RevisionFault, match='^INVALID_SUMMARY$'):
        validate_summary(report)


@pytest.mark.parametrize('record_version,report_version,retain_scope', [
    (REVISION_V2, '1.0.0-operations-summary-candidate.1', False),
    ('1.0.0-operations-revision-candidate.1', SUMMARY_V2, True),
    (REVISION_V2, SUMMARY_V2, False),
    ('1.0.0-operations-revision-candidate.1', '1.0.0-operations-summary-candidate.1', True),
])
def test_rehashed_mixed_version_records_cannot_be_adopted(record_version, report_version, retain_scope):
    record = plan_revision(None, scoped(), generated_at_epoch=FIRST)['record']
    record['schemaVersion'] = record_version
    record['report']['schemaVersion'] = report_version
    if not retain_scope:
        record['report'].pop('sourceScope')
    record['reportSha256'] = _sha(record['report'])
    record['recordSha256'] = _sha({k: v for k, v in record.items() if k != 'recordSha256'})
    with pytest.raises(RevisionFault, match='^INVALID_PREVIOUS_REVISION$'):
        validate_revision(record)


def test_selected_refusal_precedes_clock_and_every_storage_operation(client, monkeypatch):
    descriptor = scope(population='selected_dev')
    def forbidden(*args, **kwargs):
        pytest.fail('selected scope crossed noncommitting boundary')
    selected = AggregateStore(client, TABLE, environment='dev', now=forbidden, source_scope=descriptor)
    monkeypatch.setattr(client, 'get_item', forbidden)
    monkeypatch.setattr(client, 'transact_write_items', forbidden)
    for call in (lambda: selected.read(DAY), lambda: selected.commit(scoped(descriptor=descriptor))):
        with pytest.raises(AggregateStoreFault, match='^SCOPE_UNQUALIFIED$'):
            call()


def test_valid_legacy_record_cannot_be_rehomed_under_scoped_key(client):
    legacy = AggregateStore(client, TABLE, environment='dev', now=lambda: FIRST)
    original = legacy.commit(summary())['record']
    original_rows = deepcopy(rows(client))
    target_pk = store(client)._scope(DAY)[0]
    for row in original_rows:
        row['PK'] = {'S': target_pk}
        client.put_item(TableName=TABLE, Item=row)
    with pytest.raises(AggregateStoreFault, match='^INVALID_STORED_RECORD$'):
        store(client).read(DAY)
    assert legacy.read(DAY)['record'] == original


def test_same_generation_is_still_partitioned_by_environment(client):
    dev = store(client).commit(scoped())['record']
    uat_report = scoped() | {'environment': 'uat'}
    uat = AggregateStore(client, TABLE, environment='uat', now=lambda: FIRST, source_scope=scope())
    other = uat.commit(uat_report)['record']
    assert dev['revision'] == other['revision'] == 1
    assert len({row['PK']['S'] for row in rows(client)}) == 2
    assert uat.read(DAY)['record']['report']['environment'] == 'uat'
    assert store(client).read(DAY)['record']['report']['environment'] == 'dev'


def test_legacy_summary_and_revision_wire_match_exact_git_base(tmp_path):
    """Execute retained base source in an isolated local package, without services."""
    root = Path(__file__).resolve().parents[2]
    package = tmp_path / 'operations_reporting'
    package.mkdir()
    for member in ('__init__.py', 'reducer.py', 'revisions.py', 'event.schema.json', 'rejection.schema.json'):
        data = subprocess.check_output(['git', 'show', f'762184b5a32847cfae0a9798a1b35f0a6ed24c35:src/operations_reporting/{member}'], cwd=root)
        (package / member).write_bytes(data)
    fixture = Path(__file__).with_name('fixtures.json')
    program = '''
import json,sys
from operations_reporting.reducer import aggregate_day
from operations_reporting.revisions import plan_revision
f=json.load(open(sys.argv[1])); t=int(sys.argv[2])
r=aggregate_day([x['event'] for x in f['events']],[x['aggregate'] for x in f['rejectionAggregates']],local_day='2026-10-03',environment='dev',as_of_epoch=t)
print(json.dumps({'report':r,'plan':plan_revision(None,r,generated_at_epoch=t)},sort_keys=True,separators=(',',':')))
'''
    env = dict(os.environ, PYTHONPATH=str(tmp_path), AWS_EC2_METADATA_DISABLED='true')
    previous = json.loads(subprocess.check_output([sys.executable, '-B', '-c', program, str(fixture), str(FIRST)], cwd=tmp_path, env=env))
    report = summary()
    assert previous == {'report': report, 'plan': plan_revision(None, report, generated_at_epoch=FIRST)}
