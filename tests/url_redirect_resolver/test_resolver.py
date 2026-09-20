import importlib.util
import io
from pathlib import Path
import socket
import ssl
import sys
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "url_redirect_resolver"


def load(name, dependencies=None):
    spec = importlib.util.spec_from_file_location("url_resolver_test_" + name, SOURCE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {spec.name: module, **(dependencies or {})}):
        spec.loader.exec_module(module)
    return module


resolver = load("resolver")
transport = load("transport", {"resolver": resolver})
app = load("app", {"resolver": resolver, "transport": transport})


class FakeTransport:
    def __init__(self, responses=(), addresses=None):
        self.responses = iter(responses)
        self.answer = addresses or ["93.184.215.14"]
        self.calls = []

    def addresses(self, host, deadline):
        return self.answer

    def fetch(self, target, address, deadline):
        self.calls.append((target, address))
        return next(self.responses)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://example.com/file", "https://u:p@example.com",
    "http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/", "http://100.64.0.1/", "http://[::1]/",
    "http://[::ffff:127.0.0.1]/", "http://[fe80::1%25eth0]/",
    "http://2130706433/", "http://0177.0.0.1/", "http://0x7f000001/",
    "http://127.1/", "http://example.com:22/", "https://example.com:0/",
    "https://example.com:/", "http://example.com\\@127.0.0.1/",
    "https://example.com/%0d%0aInjected:yes", "https://example.com/\r\nx",
    " https://example.com", "https://example.com/%zz", "https://éxample.com/",
    "https://foo.local/", "https://example.com/?access_token=secret",
    "https://example.com/reset/abc", "https://example.com/?X-Amz-Signature=secret",
])
def test_rejects_before_dns_or_fetch(url):
    client = FakeTransport()
    with mock.patch.object(client, "addresses", wraps=client.addresses) as dns:
        result = resolver.resolve(url, client)
    assert result["resolutionStatus"] in ("blocked", "invalid_input")
    assert not client.calls
    dns.assert_not_called()


def test_preserves_path_query_and_does_not_visit_embedded_url():
    url = "https://example.com/a%2Fb?x=1&x=&next=http%3A%2F%2F127.0.0.1&x=2#fragment"
    client = FakeTransport([(200, None)])
    result = resolver.resolve(url, client)
    assert result["httpChainComplete"]
    assert client.calls[0][0].url == url.split("#")[0]
    assert len(client.calls) == 1
    assert "BROWSER_NAVIGATION_NOT_EVALUATED" in result["reasonCodes"]


def test_relative_nested_shorteners_and_transport_warnings():
    client = FakeTransport([(302, "/next"), (307, "http://other.example/end"), (301, "https://other.example/end"), (200, None)])
    result = resolver.resolve("https://short.example/start", client)
    assert [hop["httpStatus"] for hop in result["hops"]] == [302, 307, 301, 200]
    assert result["lastObservedUrl"] == "https://other.example/end"
    assert set(result["warnings"]) == {"UNENCRYPTED_CONNECTION", "HTTPS_TO_HTTP_REDIRECT"}
    assert result["requestCount"] == 4
    assert "riskLevel" not in result


@pytest.mark.parametrize("destination", ["http://10.1.1.1/", "//169.254.169.254/", "file:///etc/passwd", "https://user:password@example.com", "https://example.com/?token=abc"])
def test_redirect_to_disallowed_destination_never_connects(destination):
    client = FakeTransport([(302, destination)])
    result = resolver.resolve("https://short.example/abc", client)
    assert not result["httpChainComplete"]
    assert len(client.calls) == 1
    assert len(result["hops"]) == 1


@pytest.mark.parametrize("addresses", [["93.184.215.14", "10.1.1.1"], ["93.184.215.14", "fd00::1"], ["93.184.215.14", "::ffff:8.8.8.8"], ["93.184.215.14", "64:ff9b::808:808"]])
def test_mixed_dns_answers_fail_closed(addresses):
    client = FakeTransport(addresses=addresses)
    result = resolver.resolve("https://example.com/", client)
    assert result["resolutionStatus"] == "blocked"
    assert not client.calls


def test_ipv6_only_is_explicit_not_a_clean_result():
    client = FakeTransport(addresses=["2606:4700:4700::1111"])
    result = resolver.resolve("https://example.com/", client)
    assert result["reasonCodes"] == ["IPV6_ONLY_UNSUPPORTED"]
    assert result["resolutionStatus"] == "partial"
    assert not client.calls


def test_revalidates_dns_even_when_redirect_stays_on_same_host():
    client = FakeTransport([(302, "/next")])
    with mock.patch.object(client, "addresses", side_effect=[["93.184.215.14"], ["127.0.0.1"]]):
        result = resolver.resolve("https://example.com/", client)
    assert result["resolutionStatus"] == "blocked"
    assert len(client.calls) == 1


@pytest.mark.parametrize("responses,reason,count", [
    ([(302, "/")], "REDIRECT_LOOP", 1),
    ([(302, None)], "MISSING_REDIRECT_LOCATION", 1),
    ([(302, "\r\nhttp://example.org/")], "INVALID_REDIRECT_LOCATION", 1),
    ([(404, None)], "HTTP_RESPONSE_UNSUPPORTED", 1),
    ([(302, "/" + str(i)) for i in range(6)], "REDIRECT_LIMIT_EXCEEDED", 6),
])
def test_bounded_failures(responses, reason, count):
    client = FakeTransport(responses)
    result = resolver.resolve("https://example.com/", client)
    assert result["reasonCodes"] == [reason]
    assert not result["httpChainComplete"]
    assert len(client.calls) == count


def test_total_deadline_stops_before_network():
    deadline = resolver.Deadline(0)
    client = FakeTransport()
    result = resolver.resolve("https://example.com/", client, deadline)
    assert result["reasonCodes"] == ["TIME_BUDGET_EXCEEDED"]
    assert not client.calls


class FakeSocket:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)

    def recv(self, limit):
        chunk = next(self.chunks, b"")
        assert len(chunk) <= limit
        return chunk


def test_reads_headers_only_and_ignores_small_prefetched_body():
    sock = FakeSocket([b"HTTP/1.1 200 OK\r\nContent-Length: 999999999\r\n\r\n<script>"])
    status, location = transport.read_headers(sock, resolver.Deadline())
    assert (status, location) == (200, None)


@pytest.mark.parametrize("header,reason", [
    (b"HTTP/1.1 302 Found\r\nLocation: /a\r\nLocation: /b\r\n\r\n", "AMBIGUOUS_REDIRECT_LOCATION"),
    (b"HTTP/1.1 200 OK\r\nRefresh: 0;url=https://example.com\r\n\r\n", "BROWSER_REDIRECT_UNSUPPORTED"),
    (b"HTTP/1.1 302 Found\r\nLocation: /a\r\n /b\r\n\r\n", "MALFORMED_HTTP_RESPONSE"),
    (b"HTTP/1.1 200 OK\r\nBad header\r\n\r\n", "MALFORMED_HTTP_RESPONSE"),
    (b"HTTP/1.1 200 OK\r\n", "INCOMPLETE_HTTP_HEADERS"),
])
def test_hostile_headers(header, reason):
    with pytest.raises(resolver.ResolutionStop) as error:
        transport.read_headers(FakeSocket([header]), resolver.Deadline())
    assert error.value.reason == reason


def test_header_size_is_bounded():
    with pytest.raises(resolver.ResolutionStop) as error:
        transport.read_headers(FakeSocket([b"x" * 1024] * 16), resolver.Deadline())
    assert error.value.reason == "RESPONSE_HEADERS_TOO_LARGE"


def test_slow_drip_cannot_reset_total_deadline():
    now = [0]
    deadline = resolver.Deadline(clock=lambda: now[0])
    sock = FakeSocket([b"x"] * 20)
    original = sock.recv
    def slow_recv(limit):
        now[0] += 1
        return original(limit)
    sock.recv = slow_recv
    with pytest.raises(resolver.ResolutionStop) as error:
        transport.read_headers(sock, deadline)
    assert error.value.reason == "TIME_BUDGET_EXCEEDED"


def test_connects_to_pinned_ip_but_uses_hostname_for_tls_and_host_header():
    sock = mock.MagicMock()
    sock.recv.return_value = b"HTTP/1.1 200 OK\r\n\r\n"
    tls = mock.MagicMock()
    tls.wrap_socket.return_value = sock
    target = resolver.parse_target("https://example.com/path?x=1")
    with mock.patch.object(socket, "socket", return_value=sock), mock.patch.object(socket, "getaddrinfo", side_effect=AssertionError("second DNS lookup")), mock.patch.object(ssl, "create_default_context", return_value=tls):
        assert transport.Transport().fetch(target, "93.184.215.14", resolver.Deadline()) == (200, None)
    sock.connect.assert_called_once_with(("93.184.215.14", 443))
    tls.wrap_socket.assert_called_once_with(sock, server_hostname="example.com")
    request = sock.sendall.call_args.args[0]
    assert b"Host: example.com\r\n" in request
    assert b"GET /path?x=1 HTTP/1.1" in request
    assert b"Authorization:" not in request and b"Cookie:" not in request
    sock.close.assert_called()


def test_tls_failure_does_not_fallback_to_http():
    sock = mock.MagicMock()
    tls = mock.MagicMock()
    tls.wrap_socket.side_effect = ssl.SSLError("private-url-must-not-leak")
    with mock.patch.object(socket, "socket", return_value=sock), mock.patch.object(ssl, "create_default_context", return_value=tls):
        with pytest.raises(resolver.ResolutionStop) as error:
            transport.Transport().fetch(resolver.parse_target("https://example.com/"), "93.184.215.14", resolver.Deadline())
    assert str(error.value) == "TLS_FAILED"
    sock.connect.assert_called_once()
    sock.close.assert_called()


def test_handler_logs_no_input_even_when_dependency_raises(capsys):
    context = mock.Mock()
    context.get_remaining_time_in_millis.return_value = 12000
    with mock.patch.object(app, "resolve", side_effect=RuntimeError("sensitive-url")):
        result = app.lambda_handler({"schemaVersion": 1, "checkId": "private-id", "url": "https://example.com/?private=secret"}, context)
    output = capsys.readouterr().out
    assert result["reasonCodes"] == ["INTERNAL_ERROR"]
    assert "secret" not in output and "example.com" not in output and "private-id" not in output


@pytest.mark.parametrize("extra", [{"headers": {"Authorization": "secret"}}, {"maxRedirects": 100}, {"accountId": "admin"}])
def test_event_cannot_override_policy(extra):
    with mock.patch.object(app, "resolve") as resolve:
        result = app.lambda_handler({"schemaVersion": 1, "checkId": "abc", "url": "https://example.com", **extra}, mock.Mock())
    assert result["resolutionStatus"] == "invalid_input"
    resolve.assert_not_called()


def test_dns_queries_both_families_without_search_suffixes():
    import dns.resolver
    client = mock.Mock()
    client.resolve.side_effect = [["93.184.215.14"], ["2606:4700:4700::1111"]]
    with mock.patch.object(dns.resolver, "Resolver", return_value=client):
        assert transport.Transport().addresses("example.com", resolver.Deadline()) == ["93.184.215.14", "2606:4700:4700::1111"]
    assert [call.args for call in client.resolve.call_args_list] == [("example.com.", "A"), ("example.com.", "AAAA")]
    assert all(call.kwargs["search"] is False and call.kwargs["lifetime"] <= 2 for call in client.resolve.call_args_list)


def test_dns_timeout_does_not_use_partial_unvalidated_answers():
    import dns.exception
    import dns.resolver
    client = mock.Mock()
    client.resolve.side_effect = [["93.184.215.14"], dns.exception.Timeout()]
    with mock.patch.object(dns.resolver, "Resolver", return_value=client):
        result = resolver.resolve("https://example.com", transport.Transport())
    assert result["requestCount"] == 0
    assert result["reasonCodes"] == ["DNS_TIMEOUT"]


def test_only_a_record_can_work_when_aaaa_has_no_answer():
    import dns.resolver
    client = mock.Mock()
    client.resolve.side_effect = [["93.184.215.14"], dns.resolver.NoAnswer()]
    with mock.patch.object(dns.resolver, "Resolver", return_value=client):
        assert transport.Transport().addresses("example.com", resolver.Deadline()) == ["93.184.215.14"]


@pytest.mark.parametrize("url", [
    "https://example.com/%2572eset/abc",
    "https://example.com/reset;tracking=x",
    "https://example.com/?x=1;token=abc",
    "https://example.com/?%2574oken=abc",
    "https://example.com/%2525252572eset/abc",
])
def test_sensitive_encodings_never_connect(url):
    client = FakeTransport()
    with mock.patch.object(client, "addresses", wraps=client.addresses) as dns:
        result = resolver.resolve(url, client)
    assert result["resolutionStatus"] == "blocked"
    dns.assert_not_called()
    assert not client.calls


@pytest.mark.parametrize("url", ["https://example.com//path", "https://example.com/\ud800"])
def test_ambiguous_or_invalid_unicode_never_connects(url):
    client = FakeTransport()
    result = resolver.resolve(url, client)
    assert result["resolutionStatus"] == "invalid_input"
    assert not client.calls


def test_preserves_empty_query_on_wire():
    target = resolver.parse_target("https://example.com/path?#fragment")
    assert target.url == "https://example.com/path?"
    assert target.request_target == "/path?"


@pytest.mark.parametrize("location,expected", [
    ("?", "https://example.com/path?"),
    ("?#f", "https://example.com/path?"),
    ("/other?", "https://example.com/other?"),
    ("https://other.example/?", "https://other.example/?"),
    ("?x=2", "https://example.com/path?x=2"),
    ("next", "https://example.com/next"),
])
def test_redirect_explicit_query_preserves_semantics(location, expected):
    client = FakeTransport([(302, location), (200, None)])
    result = resolver.resolve("https://example.com/path?x=1", client)
    assert result["lastObservedUrl"] == expected
    assert client.calls[1][0].url == expected
