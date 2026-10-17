import base64
import hashlib
import importlib.util
import io
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('prepare_auth_overlay', ROOT / 'scripts/prepare_dev_prepare_auth_diagnostics.py')
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)


def baseline():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr(overlay.CORE, (ROOT / 'tests/shared_check_authority/fixtures/dev-live8-core.py').read_bytes())
        z.writestr(overlay.SECURITY, (ROOT / 'tests/shared_history/fixtures/dev-live8-security.py').read_bytes())
        z.writestr('shared_check_authority/runtime.py', b'# retain deployed inventory/history overlays\n')
        z.writestr('v1_play_handoff/app.py', b'# retain live8 verifier diagnostics\n')
        z.comment = b'preserve deployed metadata'
    return stream.getvalue()


def test_exact_live_authentication_policy_ast_equivalence():
    with zipfile.ZipFile(io.BytesIO(baseline())) as original:
        overlay.qualified_security(original.read(overlay.SECURITY), (ROOT / 'src' / overlay.SECURITY).read_bytes())
        generated = overlay.overlay_core(original.read(overlay.CORE), (ROOT / 'src' / overlay.CORE).read_bytes())
        assert generated != original.read(overlay.CORE)
        # Deployed core differs from repository core: whole-file replacement is forbidden.
        assert generated != (ROOT / 'src' / overlay.CORE).read_bytes()


def test_two_changed_one_added_and_unrelated_metadata_and_bytes_preserved(monkeypatch):
    data = baseline()
    monkeypatch.setattr(overlay, 'BASELINE_CODE', base64.b64encode(hashlib.sha256(data).digest()).decode())
    first = overlay.build(data)
    assert first == overlay.build(data)
    with zipfile.ZipFile(io.BytesIO(data)) as old, zipfile.ZipFile(io.BytesIO(first)) as new:
        assert set(new.namelist()) == set(old.namelist()) | {overlay.ADDED}
        assert new.comment == old.comment
        assert [name for name in old.namelist() if new.read(name) != old.read(name)] == [overlay.CORE, overlay.SECURITY]
        for name in old.namelist():
            for field in ['date_time', 'compress_type', 'external_attr', 'extra', 'comment', 'create_system']:
                assert getattr(old.getinfo(name), field) == getattr(new.getinfo(name), field)


def test_wrong_archive_refused():
    with pytest.raises(ValueError):
        overlay.build(baseline())


@pytest.mark.parametrize('replacement', [
    ('expiration <= now()', 'expiration < now()'),
    ('claims.get("token_use") != "access"', 'False'),
    ('error = HistoryError("UNAUTHORIZED"', 'error = HistoryError("FORBIDDEN"'),
    ('return error', 'return error\n    forbidden_extra_call()'),
    ('raise _jwt_rejection("SUBJECT_INVALID")', 'raise _jwt_rejection("SUBJECT_INVALID") from forbidden_extra_call()'),
])
def test_security_policy_or_error_helper_change_refused(replacement):
    source = (ROOT / 'src' / overlay.SECURITY).read_bytes()
    with zipfile.ZipFile(io.BytesIO(baseline())) as old, pytest.raises(ValueError):
        overlay.qualified_security(old.read(overlay.SECURITY), source.replace(replacement[0].encode(), replacement[1].encode()))


@pytest.mark.parametrize('replacement', [
    ("raise AuthorityError('AUTHENTICATION_REQUIRED')", "raise AuthorityError('ACCOUNT_UNAVAILABLE')"),
    ("getattr(error, '_jwt_auth_predicate', None)", 'str(error)'),
    ('self._assert_account(account)', 'pass'),
    ('            _record_prepare_auth_failure(event, error)', '            _record_prepare_auth_failure(event, forbidden_side_effect(error))'),
    ('            _record_prepare_auth_failure(event, error)', '            _record_prepare_auth_failure(event, error)\n            _record_prepare_auth_failure(event, error)'),
])
def test_account_policy_and_hook_leak_refused(replacement):
    source = (ROOT / 'src' / overlay.CORE).read_bytes()
    with zipfile.ZipFile(io.BytesIO(baseline())) as old, pytest.raises(ValueError):
        overlay.overlay_core(old.read(overlay.CORE), source.replace(replacement[0].encode(), replacement[1].encode()))
