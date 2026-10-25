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


PUBLIC_URL = 'https://example.com/'
MALWARE_URL = 'http://testsafebrowsing.appspot.com/s/malware.html'
NOW = '2026-10-25T12:00:00Z'


def run(monkeypatch, capsys, patch=None, *, patch_url=None, exception_url=None, now=NOW):
    requests = []

    def invoke(event):
        requests.append(event)
        if event['url'] == exception_url:
            raise RuntimeError('SYNTHETIC_INVOKE_FAILURE')
        public = event['url'] == PUBLIC_URL
        matched = event['url'] == MALWARE_URL
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
        return result | ((patch or {}) if patch_url is None or event['url'] == patch_url else {})

    monkeypatch.setattr(smoke, 'invoke', invoke)
    monkeypatch.setattr(smoke, 'wall_clock', lambda: smoke.epoch(now))
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


@pytest.mark.parametrize('observed', [
    None, True, 'not-a-timestamp', '2026-02-30T00:00:00Z',
    '2026-10-25T00:00:00+00:00', '2026-10-25T12:00:00.000000001Z',
])
@pytest.mark.parametrize('url', [PUBLIC_URL, MALWARE_URL])
def test_malformed_or_future_observation_never_passes(monkeypatch, capsys, observed, url):
    code, lines, requests = run(monkeypatch, capsys, {'lookupObservedAt': observed}, patch_url=url)
    assert code == 1 and lines[-1]['passed'] is False
    assert len(requests) == (7 if url == PUBLIC_URL else 8)
    assert lines[-1]['case'] == ('public-https' if url == PUBLIC_URL else 'google-documented-malware-test')


@pytest.mark.parametrize('expiry', [
    None, True, 'not-a-timestamp', '2026-02-30T00:00:00Z',
    '2026-10-26T00:00:00+00:00', '2026-10-24T23:59:59Z',
    '2026-10-25T00:00:00Z', '2026-10-25T11:59:59.999999999Z', NOW,
])
def test_malformed_unordered_or_expired_match_never_passes(monkeypatch, capsys, expiry):
    code, lines, requests = run(monkeypatch, capsys, {'lookupValidUntil': expiry}, patch_url=MALWARE_URL)
    assert code == 1 and len(requests) == 8
    assert lines[-1] == {'case': 'google-documented-malware-test', 'passed': False}


def test_nanosecond_expiry_is_current_only_before_its_exact_deadline(monkeypatch, capsys):
    expiry = '2026-10-25T12:00:00.000000001Z'
    code, lines, _ = run(monkeypatch, capsys, {'lookupValidUntil': expiry}, patch_url=MALWARE_URL)
    assert code == 0 and lines[-1]['passed'] is True
    code, lines, _ = run(monkeypatch, capsys, {'lookupValidUntil': expiry}, patch_url=MALWARE_URL, now=expiry)
    assert code == 1 and lines[-1]['passed'] is False


def test_first_control_failure_stops_before_next_invocation(monkeypatch, capsys):
    code, lines, requests = run(monkeypatch, capsys, {'providerCallCount': 1})
    assert code == 1 and len(requests) == 1
    assert lines == [{'case': 'metadata', 'passed': False}]


@pytest.mark.parametrize('url,count', [('http://10.0.0.1/', 2), (PUBLIC_URL, 7)])
def test_invocation_failure_stops_without_retry_or_later_cases(monkeypatch, capsys, url, count):
    code, lines, requests = run(monkeypatch, capsys, exception_url=url)
    assert code == 1 and len(requests) == len(lines) == count
    assert lines[-1]['passed'] is False
    assert all(line['passed'] for line in lines[:-1])
