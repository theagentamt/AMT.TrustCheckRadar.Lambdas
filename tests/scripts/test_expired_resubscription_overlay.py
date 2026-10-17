"""Offline archive and retained-writer qualification; no SDK or runtime calls."""
import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('expired_overlay', ROOT/'scripts/prepare_dev_expired_resubscription.py')
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)


def fixture():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in overlay.SOURCE_PINS:
            data = ((ROOT/'tests/shared_check_authority/fixtures/dev-live9-retained-writer.py').read_bytes()
                    if name == overlay.WRITER else subprocess.check_output(
                        ['git', '-C', str(ROOT), 'show', overlay.BASE_REVISION+':src/'+name]))
            assert hashlib.sha256(data).hexdigest() == overlay.BASE_PINS[name]
            archive.writestr(name, data)
        archive.writestr('dependency/unchanged.bin', b'unchanged existing dependency\x00'*50)
        archive.comment = b'preserve exact comment'
    return output.getvalue()


def test_only_six_entries_and_all_other_compressed_bytes_and_metadata_stay_exact(monkeypatch):
    data = fixture()
    monkeypatch.setattr(overlay, 'BASE_SHA', overlay.sha(data))
    first, report = overlay.build(data)
    assert first == overlay.build(data)[0]
    assert report['changedEntries'] == sorted(overlay.SOURCE_PINS)
    assert report['addedOrRemovedEntries'] == 0
    assert report['unrelatedEntriesByteAndCompressedPayloadExact'] == 1
    with zipfile.ZipFile(io.BytesIO(first)) as z:
        merged = z.read(overlay.WRITER)
    assert merged != (ROOT/'src'/overlay.WRITER).read_bytes()
    for name in ('_operation_sk', '_expire_audit', 'set_complimentary', '__init__', '_validate_sources'):
        assert overlay.shape(overlay.definition(merged, name)[0]) == overlay.shape(overlay.definition(
            (ROOT/'tests/shared_check_authority/fixtures/dev-live9-retained-writer.py').read_bytes(), name)[0])


def test_wrong_baseline_rejected():
    with pytest.raises(ValueError):
        overlay.build(fixture())


@pytest.mark.parametrize('entry', sorted(overlay.SOURCE_PINS))
def test_any_reviewed_source_drift_rejected(entry, monkeypatch):
    data = fixture()
    monkeypatch.setattr(overlay, 'BASE_SHA', overlay.sha(data))
    sources = {n: (ROOT/'src'/n).read_bytes() for n in overlay.SOURCE_PINS}
    sources[entry] += b'\n# unreviewed drift\n'
    with pytest.raises(ValueError):
        overlay.build(data, sources)


def test_missing_or_added_source_entry_rejected(monkeypatch):
    data = fixture()
    monkeypatch.setattr(overlay, 'BASE_SHA', overlay.sha(data))
    sources = {n: (ROOT/'src'/n).read_bytes() for n in overlay.SOURCE_PINS}
    sources['unrelated.py'] = b''
    with pytest.raises(ValueError):
        overlay.build(data, sources)


def test_optimized_cli_refuses_before_output(tmp_path):
    output, manifest = tmp_path/'out.zip', tmp_path/'out.json'
    result = subprocess.run([sys.executable, '-O', '-B', str(ROOT/'scripts/prepare_dev_expired_resubscription.py'),
        '--baseline', str(tmp_path/'missing.zip'), '--output', str(output), '--manifest', str(manifest)],
        capture_output=True, text=True)
    assert result.returncode != 0 and 'Optimized Python' in result.stderr
    assert not output.exists() and not manifest.exists()
