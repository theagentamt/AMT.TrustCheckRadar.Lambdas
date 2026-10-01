"""Explicit Dev admission is independent of the immutable download envelope."""
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from account_export_api.runtime import parse_http_subjects,load
from account_export_api.cursor import ExportError

V7='01234567-89ab-7cde-8123-456789abcdef'


def test_canonical_subject_versions_are_not_identity_provenance():
    values=[V7,'11234567-89ab-4cde-8123-456789abcdef']
    assert parse_http_subjects(json.dumps(values))==tuple(values)


@pytest.mark.parametrize('raw',[None,'', 'null','{}','[]','[true]', '"'+V7+'"',
    json.dumps([V7,V7]),json.dumps([V7.upper()]),json.dumps([V7.replace('-','')]),
    json.dumps([' '+V7]),json.dumps([str(n).zfill(8)+'-89ab-4cde-8123-456789abcdef' for n in range(11)]),
    '[NaN]','x'*1025])
def test_invalid_scope_refuses_before_any_sdk(raw,monkeypatch):
    import boto3
    monkeypatch.setenv('STAGE','dev');monkeypatch.setenv('ACCOUNT_EXPORT_ENABLED','true')
    if raw is None:monkeypatch.delenv('ACCOUNT_EXPORT_HTTP_SUBJECTS_JSON',raising=False)
    else:monkeypatch.setenv('ACCOUNT_EXPORT_HTTP_SUBJECTS_JSON',raw)
    monkeypatch.setattr(boto3,'client',lambda *_a,**_k:pytest.fail('No SDK for invalid scope'))
    monkeypatch.setattr(boto3,'resource',lambda *_a,**_k:pytest.fail('No SDK for invalid scope'))
    with pytest.raises(ExportError) as caught:load()
    assert caught.value.code=='SERVICE_UNAVAILABLE' and caught.value.status==503


def test_disabled_precedence_does_not_require_scope(monkeypatch):
    monkeypatch.setenv('STAGE','dev');monkeypatch.setenv('ACCOUNT_EXPORT_ENABLED','false')
    monkeypatch.delenv('ACCOUNT_EXPORT_HTTP_SUBJECTS_JSON',raising=False)
    with pytest.raises(ExportError,match='SERVICE_NOT_ENABLED'):load()
