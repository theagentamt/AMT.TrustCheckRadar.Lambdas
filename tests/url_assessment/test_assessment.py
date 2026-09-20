import importlib
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'src/url_assessment'))
from assessment_service import Budget, Unavailable, assess, validate_resolution
from lookup_provider import decode_http, parse_lookup


def event(url='https://example.com/', **extra):
    return {'schemaVersion': 1, 'checkId': 'synthetic-check', 'url': url, 'scope': 'full_url'} | extra


def resolution(urls=('https://example.com/',), complete=True):
    return {'schemaVersion': 1, 'checkId': 'synthetic-check',
            'resolutionStatus': 'http_chain_complete' if complete else 'partial',
            'hops': [{'url': url, 'httpStatus': 302 if i < len(urls) - 1 else 200} for i, url in enumerate(urls)],
            'lastObservedUrl': urls[-1] if urls else None, 'httpChainComplete': complete,
            'warnings': [], 'reasonCodes': ['HTTP_TERMINAL_OBSERVED'], 'requestCount': len(urls), 'scope': 'HTTP_REDIRECTS_ONLY'}


class Dependencies:
    def __init__(self, response=None, results=None):
        self.response = resolution() if response is None else response
        self.results = results or [[]] * 6
        self.urls = []
        self.resolve_count = 0
        self.provider_call_count = 0
    def resolve(self, check_id, url, budget):
        self.resolve_count += 1
        return self.response
    def lookup(self, url, budget):
        self.urls.append(url)
        self.provider_call_count += 1
        result = self.results[len(self.urls) - 1]
        if isinstance(result, Exception): raise result
        return result


def test_complete_no_match_is_limited_to_supported_checks():
    result = assess(event(), Dependencies(), Budget(32))
    assert result['verdict'] == 'no_known_threat_detected'
    assert result['processingOutcome'] == 'complete'
    assert result['reasonCodes'] == ['NO_LIST_MATCH', 'BROWSER_NAVIGATION_NOT_EVALUATED']
    assert result['consumerAccessEnabled'] is False
    assert 'example.com' not in json.dumps(result)


@pytest.mark.parametrize('url', ['http://169.254.169.254/latest/meta-data/', 'http://127.0.0.1/', 'http://2130706433/', 'https://example.com/?token=secret', 'https://example.com/reset', 'file:///etc/passwd', 'https://user:pass@example.com/', 'https://example.com/#secret', 'https://example.com/%0d%0aevil', 'https://example.com:444/', 'http://service.internal/'])
def test_rejected_url_never_invokes_aws_or_provider(url):
    deps = Dependencies()
    result = assess(event(url), deps, Budget(32))
    assert result['verdict'] == 'unknown'
    assert deps.resolve_count == 0
    assert deps.urls == []


@pytest.mark.parametrize('extra', [{'schemaVersion': True}, {'paid': True}, {'accountId': 'x'}, {'scope': 'unrestricted'}, {'checkId': '../x'}])
def test_untrusted_event_flags_fail_closed(extra):
    deps = Dependencies()
    assess(event(**extra), deps, Budget(32))
    assert deps.resolve_count == 0


def test_gateway_envelope_is_not_a_private_request():
    deps = Dependencies()
    assess({'body': json.dumps(event()), 'requestContext': {}}, deps, Budget(32))
    assert deps.resolve_count == 0


def test_redirect_hop_match_survives_earlier_no_match_and_stops_further_calls():
    deps = Dependencies(resolution(('https://example.com/', 'https://other.example/', 'https://last.example/')), [[], ['MALWARE'], Unavailable('PROVIDER_UNAVAILABLE')])
    result = assess(event(), deps, Budget(32))
    assert result['verdict'] == 'high_risk'
    assert result['processingOutcome'] == 'partial'
    assert result['lookupCount'] == 2
    assert len(deps.urls) == 2


def test_incomplete_chain_no_match_is_not_clearance():
    result = assess(event(), Dependencies(resolution(complete=False)), Budget(32))
    assert result['verdict'] == 'unknown'
    assert result['processingOutcome'] == 'partial'


def test_origin_projection_no_match_is_not_full_link_clearance():
    result = assess(event(scope='origin_only'), Dependencies(), Budget(32))
    assert result['verdict'] == 'unknown'
    assert result['reasonCodes'] == ['ORIGIN_ONLY_NOT_FULL_LINK']
    for url in ('https://example.com/path', 'https://example.com/?x=y', 'https://example.com/?'):
        deps = Dependencies()
        assess(event(url, scope='origin_only'), deps, Budget(32))
        assert deps.resolve_count == 0


@pytest.mark.parametrize('reason', ['PROVIDER_AUTHORIZATION_FAILED', 'PROVIDER_RATE_LIMITED', 'PROVIDER_RESPONSE_INVALID', 'SECRET_UNAVAILABLE'])
def test_provider_failure_never_becomes_no_match(reason):
    result = assess(event(), Dependencies(results=[Unavailable(reason)]), Budget(32))
    assert result['verdict'] == 'unknown'
    assert result['reasonCodes'] == [reason]


def test_no_observed_eligible_hop_does_not_load_secret_or_call_provider():
    response = resolution((), False)
    response.update(resolutionStatus='blocked', reasonCodes=['NON_PUBLIC_DESTINATION'])
    deps = Dependencies(response)
    result = assess(event(), deps, Budget(32))
    assert result['processingOutcome'] == 'blocked'
    assert not deps.urls


@pytest.mark.parametrize('patch', [{'checkId': 'other'}, {'hops': [{'url': 'http://127.0.0.1/', 'httpStatus': 200}]}, {'hops': [{'url': 'https://example.com/?token=x', 'httpStatus': 200}]}, {'hops': [{'url': 'https://other.example/', 'httpStatus': 200}]}, {'httpChainComplete': False}, {'requestCount': True}, {'warnings': ['INJECT_RAW_TEXT']}, {'reasonCodes': ['https://private.example/']}, {'lastObservedUrl': 'https://other.example/'}])
def test_malformed_resolver_response_never_reaches_google(patch):
    deps = Dependencies(resolution() | patch)
    result = assess(event(), deps, Budget(32))
    assert result['reasonCodes'] == ['RESOLVER_RESPONSE_INVALID']
    assert not deps.urls


def test_maximum_chain_is_bounded_to_six_checks():
    urls = tuple(['https://example.com/'] + [f'https://example.com/{i}' for i in range(1, 6)])
    deps = Dependencies(resolution(urls))
    result = assess(event(), deps, Budget(32))
    assert result['lookupCount'] == 6
    assert len(deps.urls) == 6


def test_expired_budget_does_not_start_resolver():
    deps = Dependencies()
    result = assess(event(), deps, Budget(0))
    assert result['reasonCodes'] == ['TIME_BUDGET_EXCEEDED']
    assert not deps.resolve_count


def test_http_warning_is_not_threat_evidence():
    response = resolution(('http://example.com/',))
    response['warnings'] = ['UNENCRYPTED_CONNECTION']
    result = assess(event('http://example.com/'), Dependencies(response), Budget(32))
    assert result['verdict'] == 'no_known_threat_detected'
    assert result['transportWarnings'] == ['UNENCRYPTED_CONNECTION']


@pytest.mark.parametrize('payload', [None, [], {'threat': {}}, {'threats': []}, {'error': {}}, {'threat': {'threatTypes': ['NEW_UNKNOWN'], 'expireTime': '2030-01-01T00:00:00Z'}}, {'threat': {'threatTypes': ['MALWARE'], 'expireTime': 'invalid'}}])
def test_provider_parser_rejects_unknown_or_evaluate_shapes(payload):
    with pytest.raises(Unavailable): parse_lookup(payload)


def test_lookup_shapes_match_official_examples():
    assert parse_lookup({}) == []
    assert parse_lookup({'threat': {'threatTypes': ['MALWARE'], 'expireTime': '2030-01-01T00:00:00.123456789Z'}}) == ['MALWARE']


@pytest.mark.parametrize('code,reason', [(301, 'PROVIDER_UNAVAILABLE'), (401, 'PROVIDER_AUTHORIZATION_FAILED'), (403, 'PROVIDER_AUTHORIZATION_FAILED'), (429, 'PROVIDER_RATE_LIMITED'), (500, 'PROVIDER_UNAVAILABLE')])
def test_http_failures_or_redirects_cannot_parse_as_empty_success(code, reason):
    with pytest.raises(Unavailable, match=reason): decode_http(f'HTTP/1.1 {code} X\r\nContent-Length: 2\r\n\r\n{{}}'.encode())


def test_http_body_caps_and_duplicate_keys():
    for body in [b' ' * 4097, b'{"threat":{},"threat":{}}', b'not json', b'']:
        with pytest.raises(Unavailable): decode_http(b'HTTP/1.1 200 OK\r\n\r\n' + body)
    assert decode_http(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}') == []


def test_private_handler_logs_allowlist_only(monkeypatch, capsys):
    # Load under unique name to avoid legacy app.py test imports.
    import importlib.util
    spec = importlib.util.spec_from_file_location('private_assessment_app', ROOT / 'src/url_assessment/app.py')
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    monkeypatch.setenv('STAGE', 'dev')
    monkeypatch.setattr(app, 'Dependencies', Dependencies)
    context = Mock()
    context.get_remaining_time_in_millis.return_value = 35000
    result = app.lambda_handler(event(), context)
    log = json.loads(capsys.readouterr().out)
    assert set(log) == {'event', 'status', 'verdict', 'reason', 'lookupCount', 'providerCallCount', 'observedHopCount', 'elapsedMs'}
    assert 'example.com' not in json.dumps(log)
    assert 'synthetic-check' not in json.dumps(log)
    monkeypatch.setenv('STAGE', 'prod')
    assert app.lambda_handler(event(), context)['reasonCodes'] == ['DEV_ONLY_DISABLED']
