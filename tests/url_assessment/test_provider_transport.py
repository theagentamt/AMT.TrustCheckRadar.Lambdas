from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import lookup_provider as module
from assessment_service import Budget, Unavailable


class FakeSocket:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.data = None
        self.address = None
        self.closed = False
    def settimeout(self, value): assert 0 < value <= 2
    def connect(self, address): self.address = address
    def sendall(self, data): self.data = data
    def recv(self, amount): return next(self.chunks, b'')
    def close(self): self.closed = True


def transport(monkeypatch, chunks, answers=('8.8.8.8',)):
    import dns.resolver
    resolver = Mock()
    resolver.resolve.return_value = answers
    monkeypatch.setattr(dns.resolver, 'Resolver', Mock(return_value=resolver))
    sock = FakeSocket(chunks)
    monkeypatch.setattr(module.socket, 'socket', Mock(return_value=sock))
    context = Mock()
    context.wrap_socket.return_value = sock
    monkeypatch.setattr(module.ssl, 'create_default_context', Mock(return_value=context))
    return sock, context, resolver


def test_fixed_provider_endpoint_tls_host_and_encoded_parameters(monkeypatch):
    sock, context, resolver = transport(monkeypatch, [b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}'])
    key = 'synthetic_provider_key_123456789'
    assert module.lookup('https://example.com/?a=1&b=2', key, Budget(32)) == []
    assert sock.address == ('8.8.8.8', 443)
    context.wrap_socket.assert_called_once_with(sock, server_hostname='webrisk.googleapis.com')
    assert sock.data.startswith(b'GET /v1/uris:search?uri=https%3A%2F%2Fexample.com%2F%3Fa%3D1%26b%3D2&threatTypes=MALWARE&')
    assert b'Host: webrisk.googleapis.com\r\n' in sock.data
    assert key.encode() not in sock.data.split(b'\r\n')[0]
    assert b'allowScan' not in sock.data
    assert sock.closed
    assert resolver.resolve.call_args.kwargs['lifetime'] <= 2


def test_provider_redirect_is_not_followed(monkeypatch):
    sock, _, _ = transport(monkeypatch, [b'HTTP/1.1 302 Found\r\nLocation: http://169.254.169.254/\r\nContent-Length: 0\r\n\r\n'])
    with pytest.raises(Unavailable, match='PROVIDER_UNAVAILABLE'):
        module.lookup('https://example.com/', 'synthetic', Budget(32))
    assert sock.address == ('8.8.8.8', 443)
    assert sock.closed


def test_provider_dns_private_answer_never_connects(monkeypatch):
    sock, _, _ = transport(monkeypatch, [], ('8.8.8.8', '127.0.0.1'))
    with pytest.raises(Unavailable): module.lookup('https://example.com/', 'synthetic', Budget(32))
    assert sock.address is None


def test_provider_wire_response_cap(monkeypatch):
    sock, _, _ = transport(monkeypatch, [b'x' * 4096] * 9)
    with pytest.raises(Unavailable, match='PROVIDER_RESPONSE_INVALID'):
        module.lookup('https://example.com/', 'synthetic', Budget(32))
    assert sock.closed


@pytest.mark.parametrize('raw', [
    b'HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\n{}',
    b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}',
    b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nTransfer-Encoding: chunked\r\n\r\n{}',
    b'HTTP/1.1 200 OK\r\nContent-Length: 9000\r\n\r\n{}',
    b'HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\n\r\n{}',
    b'HTTP/1.1 200 OK\r\nX: ' + b'a' * 16384 + b'\r\n\r\n{}',
])
def test_malformed_provider_framing_not_no_match(raw):
    with pytest.raises(Unavailable): module.decode_http(raw)
