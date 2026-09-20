"""Pinned public IPv4 transport: fixed GET, verified TLS, headers only."""

import http.client
import io
import ipaddress
import re
import socket
import ssl

from resolver import ResolutionStop, public_address

MAX_HEADER_BYTES = 16384


class _HeaderSocket:
    def __init__(self, data):
        self.data = data

    def makefile(self, *_args):
        return io.BytesIO(self.data)


def read_headers(sock, deadline):
    data = bytearray()
    while b"\r\n\r\n" not in data:
        if len(data) >= MAX_HEADER_BYTES:
            raise ResolutionStop("RESPONSE_HEADERS_TOO_LARGE")
        sock.settimeout(deadline.remaining(2.0))
        chunk = sock.recv(min(1024, MAX_HEADER_BYTES - len(data)))
        if not chunk:
            raise ResolutionStop("INCOMPLETE_HTTP_HEADERS")
        data.extend(chunk)
    deadline.remaining()
    header = bytes(data).split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
    lines = header.split(b"\r\n")
    if not re.fullmatch(rb"HTTP/1\.[01] [0-9]{3}(?: [\x20-\x7e]*)?", lines[0]):
        raise ResolutionStop("MALFORMED_HTTP_RESPONSE")
    for line in lines[1:-2]:
        name, colon, value = line.partition(b":")
        if not colon or not re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
            raise ResolutionStop("MALFORMED_HTTP_RESPONSE")
        if any(byte < 32 and byte != 9 or byte == 127 for byte in value):
            raise ResolutionStop("MALFORMED_HTTP_RESPONSE")
    response = http.client.HTTPResponse(_HeaderSocket(header))
    try:
        response.begin()
        locations = response.headers.get_all("Location", [])
        if len(locations) > 1:
            raise ResolutionStop("AMBIGUOUS_REDIRECT_LOCATION")
        if response.headers.get_all("Refresh", []):
            raise ResolutionStop("BROWSER_REDIRECT_UNSUPPORTED")
        return response.status, locations[0].strip() if locations else None
    except http.client.HTTPException:
        raise ResolutionStop("MALFORMED_HTTP_RESPONSE") from None
    finally:
        response.close()


class Transport:
    def addresses(self, host, deadline):
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            pass
        import dns.exception
        import dns.resolver

        resolver = dns.resolver.Resolver(configure=True)
        resolver.timeout = 1.0
        addresses = []
        try:
            for kind in ("A", "AAAA"):
                try:
                    answer = resolver.resolve(host + ".", kind, search=False, lifetime=deadline.remaining(2.0))
                    addresses.extend(str(record) for record in answer)
                except dns.resolver.NoAnswer:
                    continue
            return addresses
        except dns.exception.Timeout:
            raise ResolutionStop("DNS_TIMEOUT") from None
        except dns.exception.DNSException:
            raise ResolutionStop("DNS_FAILED") from None

    def fetch(self, target, address, deadline):
        # Defense at the socket boundary as well as in the orchestrating loop.
        if public_address(address).version != 4:
            raise ResolutionStop("IPV6_ONLY_UNSUPPORTED")
        sock = None
        connection = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(deadline.remaining(2.0))
            sock.connect((address, target.port))  # Numeric address: no second DNS lookup.
            if target.scheme == "https":
                sock.settimeout(deadline.remaining(2.0))
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=target.host)
            connection = http.client.HTTPConnection(target.host, target.port)
            connection.sock = sock
            sock.settimeout(deadline.remaining(2.0))
            connection.request("GET", target.request_target, headers={
                "Host": target.host,
                "User-Agent": "AMT-LinkResolver/1.0",
                "Accept": "*/*", "Accept-Encoding": "identity", "Connection": "close",
            })
            return read_headers(sock, deadline)
        except ssl.SSLError:
            raise ResolutionStop("TLS_FAILED") from None
        except (socket.timeout, TimeoutError):
            raise ResolutionStop("NETWORK_TIMEOUT") from None
        except (OSError, http.client.HTTPException):
            raise ResolutionStop("NETWORK_FAILED") from None
        finally:
            if connection is not None:
                connection.close()
            if sock is not None:
                sock.close()
