"""Isolated smoke-runner regressions; these never count as live acceptance."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

spec = importlib.util.spec_from_file_location(
    'assessment_smoke', Path(__file__).parents[2] / 'scripts/url_assessment_dev_smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def run(monkeypatch, capsys, patch=None):
    requests = []

    def invoke(event):
        requests.append(event)
        public = event['url'] == 'https://example.com/'
        matched = event['url'] == 'http://testsafebrowsing.appspot.com/s/malware.html'
        eligible = public or matched
        result = {
            'schemaVersion': 2, 'checkId': event['checkId'] if eligible else None,
            'consumerAccessEnabled': False,
            'verdict': 'high_risk' if matched else 'no_known_threat_detected' if public else 'unknown',
            'processingOutcome': 'partial' if matched else 'complete' if public else 'blocked',
            'coverage': 'limited' if matched else 'supported_checks_complete' if public else 'not_assessed',
            'providerCallCount': int(eligible), 'lookupCount': int(eligible),
            'observedHopCount': int(eligible), 'threatTypes': ['MALWARE'] if matched else [],
            'lookupObservedAt': '2026-10-25T00:00:00Z' if eligible else None,
            'lookupValidUntil': '2099-01-01T00:00:00Z' if matched else None,
        }
        return result | (patch or {})

    monkeypatch.setattr(smoke, 'invoke', invoke)
    monkeypatch.setattr(sys, 'argv', ['smoke', '--function', smoke.ALIAS, '--public-https', '--google-test'])
    with pytest.raises(SystemExit) as stopped:
        smoke.main()
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    return stopped.value.code, lines, requests


def test_current_response_schema_keeps_private_request_schema_one(monkeypatch, capsys):
    code, lines, requests = run(monkeypatch, capsys)
    assert code == 0 and len(lines) == len(requests) == 8
    assert all(line['passed'] for line in lines)
    assert all(event['schemaVersion'] == 1 for event in requests)
    assert all(event['scope'] == 'full_url' for event in requests)


@pytest.mark.parametrize('patch', [
    {'schemaVersion': 1},
    {'checkId': 'another-check'},
    {'lookupObservedAt': None},
    {'processingOutcome': 'unavailable'},
    {'coverage': 'not_assessed'},
])
def test_incompatible_or_incomplete_response_fails(monkeypatch, capsys, patch):
    code, lines, _ = run(monkeypatch, capsys, patch)
    assert code == 1
    assert any(not line['passed'] for line in lines)
