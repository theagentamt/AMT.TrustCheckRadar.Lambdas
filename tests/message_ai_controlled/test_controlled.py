from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
from email.message import Message
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import sqlite3
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'src'))
from evaluation.message_ai import corpus
from evaluation.message_ai.profile import canonical, digest, EvaluationError, reserve_cost
from evaluation.message_ai.controlled import authority as auth, protocol, engine, cli, transport
from message_evaluator import proposer

MODEL = 'gpt-4.1-mini-2025-04-14'


def packet():
    data = corpus.load(ROOT / 'evaluation/message_ai/fixtures/engineering.json')
    data['cases'] = data['cases'][:2]
    return data


def write_private(path, data):
    path.write_bytes(data); path.chmod(0o600)


def setup(tmp_path, monkeypatch, **changes):
    directory = tmp_path / 'authority'; directory.mkdir(mode=0o700)
    evidence_dir = directory / 'evidence'; evidence_dir.mkdir(mode=0o700)
    evidence = {}
    for name in auth.EVIDENCE:
        content = canonical({'unitTestOnly': name}).encode()
        write_private(evidence_dir / (name + '.json'), content)
        evidence[name] = hashlib.sha256(content).hexdigest()
    data = packet()
    key = b'unit-test-credential-not-real'
    grant = {'schemaVersion': 1, 'experimentId': 'unit-test-only', 'executionMode': 'fault_test',
        'corpusSha256': digest(data), 'profiles': {MODEL: digest(protocol.profile(MODEL))},
        'authorityPathSha256': digest(str(directory.resolve())), 'hostSha256': digest(platform.node()),
        'expiresAt': int(time.time()) + 3600, 'generationAttemptCap': 660, 'countAttemptCap': 660,
        'budgetNano': 5_000_000_000, 'countMaxChargeNano': 1000, 'evidence': evidence,
        'credentialSha256': hashlib.sha256(key).hexdigest()}
    grant.update(changes)
    approved = {grant['experimentId']: digest(grant)}
    monkeypatch.setattr(auth, 'approved_registry', lambda: approved.copy())
    auth.provision_authority(directory, grant, data)
    return auth.Authority(directory, grant, data), data, grant, directory


def response(model=MODEL, input_tokens=1000, output_tokens=100, *, tier='default', assessment='warning'):
    value = {'model': model, 'service_tier': tier, 'status': 'completed', 'usage': {
        'input_tokens': input_tokens, 'input_tokens_details': {'cached_tokens': 0},
        'output_tokens': output_tokens, 'output_tokens_details': {'reasoning_tokens': 0},
        'total_tokens': input_tokens + output_tokens}, 'output': [{
        'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': [{
            'type': 'output_text', 'text': canonical({'assessment': assessment, 'context': 'clear',
                'reasons': [{'code': 'AI_PRETEXT', 'spans': [{'start': 0, 'end': 4}]}] if assessment == 'warning' else []})}]}]}
    return canonical(value).encode()


class FakeTransport:
    mode = 'fault_test'

    def __init__(self, count=1000, generation=None):
        self.calls = []
        self.count = canonical({'object': 'response.input_tokens', 'input_tokens': count}).encode()
        self.generation = response() if generation is None else generation

    def post(self, phase, body, deadline):
        self.calls.append((phase, body, deadline))
        value = self.count if phase == 'count' else self.generation
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture(autouse=True)
def never_network_or_cloud_secrets(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail('Unmocked network/credential boundary')
    monkeypatch.setattr(socket, 'socket', fail)
    monkeypatch.setattr(socket, 'create_connection', fail)
    monkeypatch.setattr(socket, 'getaddrinfo', fail)
    monkeypatch.setattr(proposer, 'secret', fail)
    monkeypatch.setattr(proposer, 'propose', fail)


def test_count_projection_includes_prompt_schema_reasoning_and_rejects_unknown_fields():
    item = packet()['cases'][0]['intent']
    for model in protocol.base.MODELS:
        generation, count, binding = protocol.request_binding(item, model)
        assert count['input'] == generation['input']
        assert count['text'] == generation['text']
        assert count.get('reasoning') == generation.get('reasoning')
        assert len(count['input'][0]['content'][0]['text']) > 2000
        assert 'schema' in count['text']['format'] and 'max_output_tokens' not in count
        mutated = copy.deepcopy(item); mutated['target']['sanitizedText'] += ' extra'
        assert protocol.request_binding(mutated, model)[2] != binding
        for field in ('conversation', 'instructions', 'previous_response_id', 'unknown'):
            bad = generation | {field: 'sentinel'}
            with pytest.raises(EvaluationError, match='UNSUPPORTED_COUNT_PROJECTION'):
                protocol.count_body(bad)


@pytest.mark.parametrize('value', [True, -1, 0, 1.5, '12', 2_000_001])
def test_count_rejects_non_authoritative_numeric_shape(value):
    with pytest.raises(EvaluationError):
        protocol.count_result(canonical({'object': 'response.input_tokens', 'input_tokens': value}).encode())


def test_usage_independent_of_refusal_and_malformed_assessment():
    raw = json.loads(response()); raw['output'] = [{'type': 'refusal'}]
    data = protocol.usage(canonical(raw).encode(), MODEL)
    assert data['inputTokens'] == 1000 and data['tokenCostUpperNano'] == 560000
    for field, value in [('total_tokens', 9999), ('input_tokens', True), ('output_tokens_details', {'reasoning_tokens': 101})]:
        bad = copy.deepcopy(raw); bad['usage'][field] = value
        assert protocol.usage(canonical(bad).encode(), MODEL) is None
    assert protocol.usage(response(tier='priority'), MODEL) is None
    assert protocol.usage(b'not JSON', MODEL) is None


def test_end_to_end_fault_adapter_and_resume_do_not_redispatch(tmp_path, monkeypatch):
    store, data, grant, directory = setup(tmp_path, monkeypatch)
    fake = FakeTransport()
    assert engine.execute_case(store, data['cases'][0], MODEL, fake) == 'valid'
    assert [c[0] for c in fake.calls] == ['count', 'generation']
    assert engine.execute_case(store, data['cases'][0], MODEL, fake) == 'already_reserved'
    report = engine.report(store)
    assert report['simulatedAttemptReservations'] == 2 and report['actualProviderAttemptReservations'] == 0
    assert report['reservedCostNano'] == 1000 + reserve_cost(MODEL)
    assert report['generationKnownUsageTotals']['tokenCostUpperNano'] == 560000
    assert report['actualProviderCostNano'] == 0
    store.close()
    store = auth.Authority(directory, grant, data)
    assert engine.execute_case(store, data['cases'][0], MODEL, fake) == 'already_reserved'
    assert len(fake.calls) == 2 and engine.report(store) == report
    store.close()


@pytest.mark.parametrize('count,expected', [(8193, 'input_limit'), (True, 'count_invalid')])
def test_unadmitted_count_never_generates(tmp_path, monkeypatch, count, expected):
    store, data, _, _ = setup(tmp_path, monkeypatch)
    fake = FakeTransport(count=count)
    assert engine.execute_case(store, data['cases'][0], MODEL, fake) == expected
    assert len(fake.calls) == 1
    assert engine.report(store)['attemptReservationsByPhase'] == {'count': 1}
    store.close()


def test_timeout_unknown_cost_remains_reserved_and_no_retry(tmp_path, monkeypatch):
    store, data, _, _ = setup(tmp_path, monkeypatch)
    fake = FakeTransport(generation=TimeoutError('PRIVATE-SECRET-SENTINEL'))
    assert engine.execute_case(store, data['cases'][0], MODEL, fake) == 'generation_transport_failure'
    assert engine.execute_case(store, data['cases'][0], MODEL, fake) == 'already_reserved'
    report = engine.report(store)
    assert report['generationAttemptsWithUnknownUsage'] == 1
    assert report['reservedCostNano'] == reserve_cost(MODEL) + 1000
    assert 'PRIVATE' not in canonical(report)
    store.close()


@pytest.mark.parametrize('raw', [response(input_tokens=1001), response(output_tokens=513), response(tier='priority')])
def test_usage_disagreement_halts_global_dispatch(tmp_path, monkeypatch, raw):
    store, data, _, _ = setup(tmp_path, monkeypatch)
    fake = FakeTransport(generation=raw)
    assert engine.execute_case(store, data['cases'][0], MODEL, fake) == 'experiment_halted'
    assert engine.report(store)['halted'] is True
    with pytest.raises(EvaluationError, match='EXPERIMENT_HALTED'):
        engine.execute_case(store, data['cases'][1], MODEL, fake)
    assert len(fake.calls) == 2
    store.close()


def test_invalid_assessment_still_records_valid_usage(tmp_path, monkeypatch):
    store, data, _, _ = setup(tmp_path, monkeypatch)
    raw = json.loads(response()); raw['output'][0]['content'] = [{'type': 'refusal', 'refusal': 'not retained'}]
    fake = FakeTransport(generation=canonical(raw).encode())
    assert engine.execute_case(store, data['cases'][0], MODEL, fake) == 'assessment_rejected'
    report = engine.report(store)
    assert report['generationKnownUsageTotals']['inputTokens'] == 1000
    assert report['validAiAssessments'] == {}
    store.close()


def test_reservations_survive_crash_and_new_report_directories(tmp_path, monkeypatch):
    store, data, grant, directory = setup(tmp_path, monkeypatch)
    item = data['cases'][0]
    binding = protocol.request_binding(item['intent'], MODEL)[2]
    key = digest({'case': item['caseId'], 'model': MODEL})
    store.reserve(key, 'count', MODEL, 'en', 'smoke', binding)
    store.close()
    store = auth.Authority(directory, grant, data)
    fake = FakeTransport()
    assert engine.execute_case(store, item, MODEL, fake) == 'count_pending_unknown'
    first = engine.export_report(store, tmp_path / 'report-a')
    assert first == engine.export_report(store, tmp_path / 'report-b')
    assert fake.calls == [] and first['reservedCostNano'] == 1000
    store.close()


def test_init_cannot_reset_missing_ledger_or_copy_to_new_location(tmp_path, monkeypatch):
    store, data, grant, directory = setup(tmp_path, monkeypatch); store.close()
    (directory / 'authority.sqlite3').unlink()
    with pytest.raises(FileNotFoundError):
        auth.Authority(directory, grant, data)
    with pytest.raises(FileExistsError):
        auth.provision_authority(directory, grant, data)
    with pytest.raises(EvaluationError, match='AUTHORITY_LOCATION_MISMATCH'):
        auth.validate_authorization(grant, data, tmp_path / 'new')


def test_expired_or_revoked_grant_cannot_dispatch(tmp_path, monkeypatch):
    store, data, grant, _ = setup(tmp_path, monkeypatch)
    store.clock = lambda: grant['expiresAt']
    fake = FakeTransport()
    with pytest.raises(EvaluationError, match='AUTHORIZATION_EXPIRED'):
        engine.execute_case(store, data['cases'][0], MODEL, fake)
    store.clock = time.time
    monkeypatch.setattr(auth, 'approved_registry', lambda: {})
    with pytest.raises(EvaluationError, match='EXPERIMENT_NOT_APPROVED'):
        engine.execute_case(store, data['cases'][0], MODEL, fake)
    assert fake.calls == []
    store.close()


def test_expiry_after_reservation_still_prevents_transport(tmp_path, monkeypatch):
    store, data, grant, _ = setup(tmp_path, monkeypatch)
    real_reserve = store.reserve
    def reserve_then_expire(*args):
        result = real_reserve(*args)
        store.clock = lambda: grant['expiresAt']
        return result
    monkeypatch.setattr(store, 'reserve', reserve_then_expire)
    fake = FakeTransport()
    assert engine.execute_case(store, data['cases'][0], MODEL, fake) == 'count_transport_failure'
    assert fake.calls == [] and engine.report(store)['reservedCostNano'] == 1000
    store.close()


def test_two_connections_last_count_slot(tmp_path, monkeypatch):
    store, data, grant, directory = setup(tmp_path, monkeypatch, countAttemptCap=1)
    store.close(); barrier = threading.Barrier(2)
    def reserve(index):
        connection = auth.Authority(directory, grant, data)
        try:
            barrier.wait(timeout=5)
            return connection.reserve(digest(index), 'count', MODEL, 'en', 'smoke', digest('binding'))
        except EvaluationError as exc:
            return str(exc)
        finally:
            connection.close()
    with ThreadPoolExecutor(2) as pool:
        values = list(pool.map(reserve, [1, 2]))
    assert values.count(True) == 1 and values.count('EXPERIMENT_CAP_REACHED') == 1


def test_cost_cap_includes_count_cost_before_generation(tmp_path, monkeypatch):
    store, data, _, _ = setup(tmp_path, monkeypatch, budgetNano=1000)
    fake = FakeTransport()
    with pytest.raises(EvaluationError, match='EXPERIMENT_CAP_REACHED'):
        engine.execute_case(store, data['cases'][0], MODEL, fake)
    assert len(fake.calls) == 1 and engine.report(store)['reservedCostNano'] == 1000
    store.close()


def test_tampered_journal_finish_and_binding_fail_closed(tmp_path, monkeypatch):
    store, _, _, _ = setup(tmp_path, monkeypatch)
    key = digest('key')
    store.reserve(key, 'count', MODEL, 'en', 'smoke', digest('binding'))
    with pytest.raises(EvaluationError, match='ATTEMPT_IDENTITY_MISMATCH'):
        store.reserve(key, 'count', MODEL, 'en', 'smoke', digest('changed'))
    result = engine._result('counted', count=1000, received=True)
    store.finish(key, 'count', result); store.finish(key, 'count', result)
    with pytest.raises(EvaluationError, match='SETTLEMENT_CONFLICT'):
        store.finish(key, 'count', engine._result('counted', count=2000, received=True))
    store.db.execute('UPDATE calls SET reserved=0')
    with pytest.raises(EvaluationError, match='AUTHORITY_CORRUPT'):
        store.snapshot()
    store.close()


def test_authorization_requires_real_pins_and_no_shipped_approval(tmp_path, capsys):
    assert json.loads((ROOT / 'evaluation/message_ai/controlled/approved_experiments.json').read_text()) == {}
    manifest = tmp_path / 'auth.json'
    write_private(manifest, canonical({'approved': True}).encode())
    result = cli.main(['run', '--corpus', 'PRIVATE-SENTINEL', '--authorization', str(manifest),
                       '--authority-dir', str(tmp_path / 'missing'), '--report-dir', str(tmp_path / 'report')])
    assert result == 2 and not (tmp_path / 'missing').exists()
    assert 'PRIVATE' not in capsys.readouterr().err


def test_preflight_report_and_init_never_require_key(tmp_path, monkeypatch, capsys):
    store, data, grant, directory = setup(tmp_path, monkeypatch); store.close()
    manifest = tmp_path / 'corpus.json'; manifest.write_text(canonical(data))
    authorization = tmp_path / 'authorization.json'; write_private(authorization, canonical(grant).encode())
    def fail(*args): pytest.fail('credential loaded for read-only action')
    monkeypatch.setattr(engine, 'credential_loader', fail)
    common = ['--corpus', str(manifest), '--authorization', str(authorization), '--authority-dir', str(directory)]
    assert cli.main(['preflight'] + common) == 0
    assert cli.main(['report'] + common + ['--report-dir', str(tmp_path / 'report')]) == 0
    assert cli.main(['init'] + common) == 2
    assert 'unit-test-only' not in capsys.readouterr().out


def test_bound_credential_only_loaded_after_approval(tmp_path, monkeypatch):
    store, _, _, directory = setup(tmp_path, monkeypatch)
    write_private(directory / 'provider-key.txt', b'unit-test-credential-not-real')
    load = engine.credential_loader(store)
    assert load() == 'unit-test-credential-not-real'
    monkeypatch.setattr(auth, 'approved_registry', lambda: {})
    with pytest.raises(EvaluationError, match='EXPERIMENT_NOT_APPROVED'):
        load()
    store.close()


def test_revoked_and_expired_readonly_report_keeps_reconciliation(tmp_path, monkeypatch):
    store, data, grant, directory = setup(tmp_path, monkeypatch)
    engine.execute_case(store, data['cases'][0], MODEL, FakeTransport())
    before = engine.report(store); store.close()
    monkeypatch.setattr(auth, 'approved_registry', lambda: {})
    audit = auth.Authority(directory, grant, data, audit=True, clock=lambda: grant['expiresAt'] + 1)
    after = engine.export_report(audit, tmp_path / 'revoked-report')
    assert after['authorizationStatus'] == 'revoked_or_unapproved'
    assert after['reservedCostNano'] == before['reservedCostNano']
    with pytest.raises(EvaluationError, match='READ_ONLY_AUTHORITY'):
        audit.reserve(digest('case'), 'count', MODEL, 'en', 'smoke', digest('binding'))
    with pytest.raises(EvaluationError, match='READ_ONLY_AUTHORITY'):
        engine.credential_loader(audit)()
    monkeypatch.setattr(auth, 'approved_registry', lambda: {grant['experimentId']: digest(grant)})
    assert engine.report(audit)['authorizationStatus'] == 'expired'
    audit.close()


def test_halt_between_reserve_and_dispatch_blocks_transport(tmp_path, monkeypatch):
    store, data, grant, directory = setup(tmp_path, monkeypatch)
    other = data['cases'][1]
    key = digest({'case': other['caseId'], 'model': MODEL})
    binding = protocol.request_binding(other['intent'], MODEL)[2]
    store.reserve(key, 'count', MODEL, other['intent']['language'], 'smoke', binding)
    store.finish(key, 'count', engine._result('counted', count=1000, received=True))
    store.reserve(key, 'generation', MODEL, other['intent']['language'], 'smoke', binding)
    reserved = threading.Event(); halted = threading.Event()
    def peer_halts():
        peer = auth.Authority(directory, grant, data)
        try:
            assert reserved.wait(5)
            peer.finish(key, 'generation', engine._result('usage_mismatch', received=True), halt=True)
            halted.set()
        finally:
            peer.close()
    thread = threading.Thread(target=peer_halts)
    thread.start()
    original = store.reserve
    def reserve_then_wait(*args):
        result = original(*args); reserved.set(); assert halted.wait(5); return result
    monkeypatch.setattr(store, 'reserve', reserve_then_wait)
    fake = FakeTransport()
    assert engine.execute_case(store, data['cases'][0], MODEL, fake) == 'count_transport_failure'
    thread.join(5)
    assert not thread.is_alive() and fake.calls == []
    assert engine.report(store)['halted'] is True
    store.close()


def test_cli_finalizer_cannot_expose_exception_text(tmp_path, monkeypatch, capsys):
    store, data, grant, directory = setup(tmp_path, monkeypatch); store.close()
    corpus_path = tmp_path / 'corpus.json'; corpus_path.write_text(canonical(data))
    grant_path = tmp_path / 'grant.json'; write_private(grant_path, canonical(grant).encode())
    original = auth.Authority.close
    def close_with_private_error(self):
        original(self); raise RuntimeError('SENSITIVE-CLOSE-SENTINEL')
    monkeypatch.setattr(auth.Authority, 'close', close_with_private_error)
    assert cli.main(['preflight', '--corpus', str(corpus_path), '--authorization', str(grant_path),
                     '--authority-dir', str(directory)]) == 0
    output = capsys.readouterr()
    assert 'SENSITIVE' not in output.out + output.err


def install_connection(monkeypatch, *, body=b'{}', status=200, encoding='identity', length=None, close_error=False):
    records = []
    class Response:
        def __init__(self):
            self.status = status
            self.headers = Message()
            self.headers['Content-Length'] = str(len(body) if length is None else length)
            self.position = 0
        def getheader(self, name, default):
            return encoding
        def read1(self, size):
            part = body[self.position:self.position + size]
            self.position += len(part)
            return part
        def close(self):
            if close_error: raise RuntimeError('PRIVATE-CLOSE')
    class Connection:
        def __init__(self, host, **kwargs):
            records.append(('host', host, kwargs)); self.sock = object()
        def connect(self):
            records.append(('connect',))
        def request(self, method, path, *, body, headers):
            records.append(('request', method, path, body, headers))
        def getresponse(self):
            return Response()
        def close(self):
            if close_error: raise RuntimeError('PRIVATE-CONNECTION')
    monkeypatch.setattr(proposer, 'FixedConnection', Connection)
    return records


def test_fixed_transport_paths_host_and_finalizer_redaction(monkeypatch):
    records = install_connection(monkeypatch, close_error=True)
    client = transport.OfficialTransport(lambda: 'unit-test-credential-not-real')
    for phase, route in [('count', protocol.COUNT_PATH), ('generation', protocol.GENERATION_PATH)]:
        assert client.post(phase, b'{}', time.monotonic() + 8) == b'{}'
        assert records[-1][1:3] == ('POST', route)
        assert records[-1][4]['Accept-Encoding'] == 'identity'
    assert all(r[1] == 'api.openai.com' and r[2]['port'] == 443 for r in records if r[0] == 'host')
    with pytest.raises(EvaluationError, match='INVALID_TRANSPORT_REQUEST'):
        client.post('https://untrusted.invalid/', b'{}', time.monotonic() + 8)


@pytest.mark.parametrize('options', [{'status': 302}, {'status': 429}, {'body': b'x' * 4097},
                                    {'encoding': 'gzip'}, {'length': 100}, {'length': -1}])
def test_transport_no_redirect_retry_oversize_or_bad_framing(monkeypatch, options):
    records = install_connection(monkeypatch, **options)
    client = transport.OfficialTransport(lambda: 'unit-test-credential-not-real')
    with pytest.raises(EvaluationError, match='^PROVIDER_TRANSPORT_FAILURE$'):
        client.post('count', b'{}', time.monotonic() + 8)
    assert sum(r[0] == 'connect' for r in records) == 1


def test_deadline_covers_credential_phase_and_external_error_is_redacted(monkeypatch):
    records = install_connection(monkeypatch)
    now = [0.0]
    def slow_key():
        now[0] = 9.0
        return 'unit-test-credential-not-real'
    client = transport.OfficialTransport(slow_key, clock=lambda: now[0])
    with pytest.raises(EvaluationError, match='^PROVIDER_TRANSPORT_FAILURE$'):
        client.post('count', b'{}', 8)
    assert records == []
    def bad_key():
        raise EvaluationError('PRIVATE-KEY-ERROR')
    client = transport.OfficialTransport(bad_key)
    with pytest.raises(EvaluationError, match='^PROVIDER_TRANSPORT_FAILURE$'):
        client.post('count', b'{}', time.monotonic() + 8)


def test_count_and_generation_share_total_deadline(tmp_path, monkeypatch):
    store, data, _, _ = setup(tmp_path, monkeypatch)
    fake = FakeTransport(); now = [0.0]
    original = fake.post
    def measured(phase, body, deadline):
        value = original(phase, body, deadline)
        now[0] += 7
        return value
    fake.post = measured
    assert engine.execute_case(store, data['cases'][0], MODEL, fake, clock=lambda: now[0]) == 'valid'
    assert fake.calls[0][2] == 8 and fake.calls[1][2] == 15
    assert all(call[2] <= 18 for call in fake.calls)
    store.close()


def test_dispatch_gate_rechecks_expiry_after_its_own_lock_wait(tmp_path, monkeypatch):
    store, data, grant, directory = setup(tmp_path, monkeypatch)
    now = [grant['expiresAt'] - 1]; store.clock = lambda: now[0]
    peer_locked = threading.Event(); gate_entering = threading.Event()
    original = store.transaction
    @contextmanager
    def signaled_transaction():
        gate_entering.set()
        with original():
            yield
    monkeypatch.setattr(store, 'transaction', signaled_transaction)
    def lock_then_expire():
        peer = auth.Authority(directory, grant, data)
        try:
            with peer.transaction():
                peer_locked.set()
                assert gate_entering.wait(5)
                now[0] = grant['expiresAt']
        finally:
            peer.close()
    thread = threading.Thread(target=lock_then_expire); thread.start()
    assert peer_locked.wait(5)
    with pytest.raises(EvaluationError, match='AUTHORIZATION_EXPIRED'):
        store.dispatch_gate()
    thread.join(5)
    assert not thread.is_alive()
    store.close()


def test_authority_path_escapes_sqlite_uri_metacharacters(tmp_path, monkeypatch):
    special = tmp_path / 'safe?name#fragment'; special.mkdir()
    store, _, _, directory = setup(special, monkeypatch)
    assert (directory / 'authority.sqlite3').exists()
    assert engine.report(store)['reservedCostNano'] == 0
    store.close()
