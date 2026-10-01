"""Actual consent producer and consumer composition on isolated SDK tables."""
import importlib.util
import os
from pathlib import Path
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION') != '1':
    pytest.skip('Isolated SDK only', allow_module_level=True)
from tests.campaign_deletion_bridge.test_qualification_dynamodb import runner, Q

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('withdrawal_acceptance',
    ROOT / 'scripts/qualification/withdrawal_acceptance.py')
W = importlib.util.module_from_spec(spec)
spec.loader.exec_module(W)

@pytest.mark.parametrize('case', W.CASES)
def test_actual_producer_durable_cleanup_and_replay(runner, case, monkeypatch):
    monkeypatch.setenv('CAMPAIGN_PERIOD_ADMISSION_ENABLED', 'true')
    monkeypatch.setenv('CAMPAIGN_PERIOD_ADMISSION_GENERATION', Q.OP)
    monkeypatch.setenv('CAMPAIGN_PERIOD_ADMISSION_ACCOUNT_ID', Q.ACCOUNT)
    monkeypatch.setenv('AWS_REGION', Q.REGION)
    monkeypatch.setenv('AWS_DEFAULT_REGION', Q.REGION)
    runner.preflight()
    runner.seed()
    W.run(runner, case, Q.require)


def test_acceptance_rejects_producer_without_durable_recovery(runner, monkeypatch):
    from contextlib import contextmanager
    actual = W.participation
    @contextmanager
    def without_enqueue(value):
        with actual(value) as producer:
            producer.config.CAMPAIGN_RECOVERY_WRITES_ENABLED = False
            yield producer
    monkeypatch.setattr(W, 'participation', without_enqueue)
    monkeypatch.setenv('CAMPAIGN_PERIOD_ADMISSION_ENABLED', 'true')
    monkeypatch.setenv('CAMPAIGN_PERIOD_ADMISSION_GENERATION', Q.OP)
    monkeypatch.setenv('CAMPAIGN_PERIOD_ADMISSION_ACCOUNT_ID', Q.ACCOUNT)
    monkeypatch.setenv('AWS_REGION', Q.REGION)
    monkeypatch.setenv('AWS_DEFAULT_REGION', Q.REGION)
    runner.preflight()
    runner.seed()
    with pytest.raises(Q.QualificationFailure):
        W.run(runner, W.CASES[0], Q.require)
    command = runner.get('ledger', runner.cmd['PK'], 'CAMPAIGN_WITHDRAWAL#' + Q.OP)
    assert command['status'] == 'PENDING'
    assert runner.get('ledger', runner.cmd['PK'], Q.R.JOB_PREFIX + Q.OP) is None
    assert runner.get('users', 'USER#' + runner.subject, 'CAMPAIGN_PARTICIPATION')['state'] == 'withdrawal_pending'
