import ast
import base64
import copy
import hashlib
import importlib.util
import io
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('dev_failure_overlay', ROOT / 'scripts/prepare_dev_failure_diagnostics.py')
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)


def baseline(component):
    if component == 'admission':
        with zipfile.ZipFile(ROOT / 'tests/shared_check_authority/fixtures/dev-access-reader-8ecc.zip') as archive:
            source = archive.read(overlay.COMPONENTS[component]['changed'])
    else:
        source = (ROOT / 'tests/v1_play_handoff/fixtures/dev-live7-app.py').read_bytes()
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as archive:
        archive.writestr(overlay.COMPONENTS[component]['changed'], source)
        archive.writestr('unrelated/source.py', b'unchanged_policy = True\n')
        archive.writestr('app.py', b'# original root wrapper\n')
        archive.comment = b'preserved metadata'
    return result.getvalue()


@pytest.mark.parametrize('component', sorted(overlay.COMPONENTS))
def test_policy_ast_preserved_against_exact_deployed_source(component):
    item = overlay.COMPONENTS[component]
    with zipfile.ZipFile(io.BytesIO(baseline(component))) as archive:
        original = archive.read(item['changed'])
    overlay.qualified_source(original, (ROOT / 'src' / item['changed']).read_bytes(), item)


@pytest.mark.parametrize('component', sorted(overlay.COMPONENTS))
def test_overlay_deterministic_and_every_unrelated_entry_byte_preserved(component, monkeypatch):
    data = baseline(component)
    item = {**overlay.COMPONENTS[component],
            'baselineCodeSha256': base64.b64encode(hashlib.sha256(data).digest()).decode()}
    monkeypatch.setitem(overlay.COMPONENTS, component, item)
    first = overlay.build(data, component)
    assert first == overlay.build(data, component)
    with zipfile.ZipFile(io.BytesIO(data)) as original, zipfile.ZipFile(io.BytesIO(first)) as candidate:
        assert candidate.comment == original.comment
        assert set(candidate.namelist()) == set(original.namelist()) | {item['added']}
        for name in original.namelist():
            if name != item['changed']:
                assert candidate.read(name) == original.read(name)
            for field in ('date_time', 'compress_type', 'external_attr', 'extra', 'comment', 'create_system'):
                assert getattr(candidate.getinfo(name), field) == getattr(original.getinfo(name), field)


@pytest.mark.parametrize('component', sorted(overlay.COMPONENTS))
def test_unpinned_archive_rejected_before_overlay(component):
    with pytest.raises(ValueError):
        overlay.build(baseline(component), component)


@pytest.mark.parametrize('component', sorted(overlay.COMPONENTS))
def test_policy_change_cannot_hide_in_diagnostic_overlay(component):
    item = overlay.COMPONENTS[component]
    with zipfile.ZipFile(io.BytesIO(baseline(component))) as archive:
        original = archive.read(item['changed'])
    candidate = (ROOT / 'src' / item['changed']).read_bytes() + b'\nPOLICY_CHANGED = True\n'
    with pytest.raises(ValueError):
        overlay.qualified_source(original, candidate, item)


def test_qualification_rejects_even_under_optimized_python():
    # The builder uses explicit require(), never assertions that -O can remove.
    tree = ast.parse((ROOT / 'scripts/prepare_dev_failure_diagnostics.py').read_text())
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))
