"""Fixed-endpoint Lookup GET with bounded DNS, TLS, response and total time."""
import datetime
import http.client
import io
import ipaddress
import json
import socket
import ssl
from urllib.parse import urlencode

from assessment_service import Budget, Unavailable
from url_redirect_resolver.resolver import public_address

HOST = 'webrisk.googleapis.com'
THREAT_TYPES = ('MALWARE', 'SOCIAL_ENGINEERING', 'UNWANTED_SOFTWARE')
MAX_WIRE_BYTES = 32768
MAX_BODY_BYTES = 4096


def parse_lookup(payload):
    if payload == {}:
        return []
    if not isinstance(payload, dict) or set(payload) != {'threat'}:
        raise Unavailable('PROVIDER_RESPONSE_INVALID')
    threat = payload['threat']
    if (not isinstance(threat, dict) or set(threat) != {'threatTypes', 'expireTime'}
            or not isinstance(threat['threatTypes'], list) or not 1 <= len(threat['threatTypes']) <= 3
            or any(not isinstance(x, str) or x not in THREAT_TYPES for x in threat['threatTypes'])
            or len(set(threat['threatTypes'])) != len(threat['threatTypes'])
            or not isinstance(threat['expireTime'], str)):
        raise Unavailable('PROVIDER_RESPONSE_INVALID')
    try:
        timestamp = datetime.datetime.fromisoformat(threat['expireTime'].replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            raise ValueError()
    except ValueError:
        raise Unavailable('PROVIDER_RESPONSE_INVALID') from None
    return threat['threatTypes']


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Unavailable('PROVIDER_RESPONSE_INVALID')
        result[key] = value
    return result


class BufferSocket:
    def __init__(self, data): self.data = data
    def makefile(self, *args): return io.BytesIO(self.data)


def decode_http(raw):
    header_end = raw.find(b'\r\n\r\n')
    if header_end < 0 or header_end > 16384:
        raise Unavailable('PROVIDER_RESPONSE_INVALID')
    response = http.client.HTTPResponse(BufferSocket(raw))
    try:
        response.begin()
        if response.status != 200:
            raise Unavailable('PROVIDER_AUTHORIZATION_FAILED' if response.status in (401, 403) else 'PROVIDER_RATE_LIMITED' if response.status == 429 else 'PROVIDER_UNAVAILABLE')
        if response.getheader('Content-Encoding', 'identity') != 'identity':
            raise Unavailable('PROVIDER_RESPONSE_INVALID')
        lengths = response.headers.get_all('Content-Length', [])
        transfers = response.headers.get_all('Transfer-Encoding', [])
        if len(lengths) > 1 or len(transfers) > 1 or lengths and transfers:
            raise Unavailable('PROVIDER_RESPONSE_INVALID')
        if transfers and transfers != ['chunked']:
            raise Unavailable('PROVIDER_RESPONSE_INVALID')
        expected = int(lengths[0]) if lengths else None
        if expected is not None and not 0 < expected <= MAX_BODY_BYTES:
            raise Unavailable('PROVIDER_RESPONSE_INVALID')
        body = response.read(MAX_BODY_BYTES + 1)
        if expected is not None and len(body) != expected:
            raise Unavailable('PROVIDER_RESPONSE_INVALID')
        if not body or len(body) > MAX_BODY_BYTES:
            raise Unavailable('PROVIDER_RESPONSE_INVALID')
        return parse_lookup(json.loads(body.decode('utf-8'), object_pairs_hook=unique_object))
    except (ValueError, UnicodeError, http.client.HTTPException):
        raise Unavailable('PROVIDER_RESPONSE_INVALID') from None
    finally:
        response.close()


def lookup(uri, api_key, parent_budget):
    # No URL supplied by an event can override this origin, path, method or key.
    budget = Budget(parent_budget.remaining(4.0))
    sock = None
    try:
        import dns.resolver
        resolver = dns.resolver.Resolver(configure=True)
        resolver.timeout = 1.0
        answer = resolver.resolve(HOST + '.', 'A', search=False, lifetime=budget.remaining(2.0))
        addresses = [str(ipaddress.ip_address(str(record))) for record in answer]
        if not addresses or len(addresses) > 32 or any(public_address(x).version != 4 for x in addresses):
            raise Unavailable('PROVIDER_UNAVAILABLE')
        query = urlencode([('uri', uri)] + [('threatTypes', x) for x in THREAT_TYPES])
        headers = (f'GET /v1/uris:search?{query} HTTP/1.1\r\nHost: {HOST}\r\n'
                   f'x-goog-api-key: {api_key}\r\nAccept: application/json\r\n'
                   'Accept-Encoding: identity\r\nConnection: close\r\n\r\n').encode('ascii')
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(budget.remaining(2.0))
        sock.connect((addresses[0], 443))
        sock.settimeout(budget.remaining(2.0))
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=HOST)
        sock.settimeout(budget.remaining(2.0))
        sock.sendall(headers)
        data = bytearray()
        while True:
            sock.settimeout(budget.remaining(2.0))
            block = sock.recv(min(4096, MAX_WIRE_BYTES + 1 - len(data)))
            if not block:
                break
            data.extend(block)
            if len(data) > MAX_WIRE_BYTES:
                raise Unavailable('PROVIDER_RESPONSE_INVALID')
        budget.remaining()
        return decode_http(bytes(data))
    except Unavailable:
        raise
    except Exception:
        raise Unavailable('PROVIDER_UNAVAILABLE') from None
    finally:
        if sock is not None:
            sock.close()
