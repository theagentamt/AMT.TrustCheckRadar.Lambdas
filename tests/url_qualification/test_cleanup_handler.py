"""Cleanup retention consent is checked before any SDK client or sweep."""
import sys
from pathlib import Path
from unittest.mock import Mock

import boto3
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from shared_url_qualification import cleanup


@pytest.mark.parametrize('approval',[None,'false','TRUE','1','', ' true', 'true '])
def test_cleanup_retention_absent_false_or_malformed_never_creates_sdk_client(monkeypatch,approval):
    monkeypatch.setenv('STAGE','dev')
    monkeypatch.setenv('URL_CLEANUP_ENABLED','true')
    if approval is None:monkeypatch.delenv('URL_RETENTION_APPROVED',raising=False)
    else:monkeypatch.setenv('URL_RETENTION_APPROVED',approval)
    denied=Mock(side_effect=AssertionError('SDK work before retention consent'))
    monkeypatch.setattr(boto3,'resource',denied)
    monkeypatch.setattr(boto3,'client',denied)
    monkeypatch.setattr(cleanup,'sweep',Mock(side_effect=AssertionError('sweep before consent')))
    assert cleanup.lambda_handler({},None)=={'enabled':False}
    denied.assert_not_called()


def test_approved_cleanup_handler_preserves_bounded_sweep_without_runtime_mode(monkeypatch):
    for key,value in {'STAGE':'dev','URL_CLEANUP_ENABLED':'true','URL_RETENTION_APPROVED':'true',
        'URL_QUALIFICATION_ENABLED':'false','URL_PROVIDER_ENABLED':'false','URL_NEW_ADMISSION_ENABLED':'false',
        'URL_QUALIFICATION_TABLE_NAME':'synthetic-table'}.items():monkeypatch.setenv(key,value)
    resource=object()
    factory=Mock(return_value=resource)
    monkeypatch.setattr(boto3,'resource',factory)
    monkeypatch.setattr(cleanup.time,'time',lambda:1800000000)
    sweep=Mock(return_value={'deleted':2,'complete':True})
    monkeypatch.setattr(cleanup,'sweep',sweep)
    assert cleanup.lambda_handler({'ignored':'synthetic'},None)=={'deleted':2,'complete':True}
    sweep.assert_called_once_with(resource,'synthetic-table',1800000000)
    assert factory.call_args.kwargs['config'].retries['total_max_attempts']==1
