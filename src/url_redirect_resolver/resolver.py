"""Bounded HTTP redirect observation. This module does not assess reputation."""

from dataclasses import dataclass
import ipaddress
import re
import time
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

MAX_URL_BYTES = 2048
MAX_REDIRECTS = 5
TOTAL_SECONDS = 10.0
# Also enforced by the isolated subnet's egress ACL. Conservative exceptions to
# globally routable space are intentionally unsupported, rather than repaired.
DENIED_V4 = tuple(ipaddress.ip_network(value) for value in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15",
    "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
))
SENSITIVE_KEYS = re.compile(
    r"(?:token|secret|password|passwd|signature|credential|authorization|"
    r"api[_-]?key|access[_-]?key|session|code|jwt|otp|samlresponse|ticket)", re.I
)
SENSITIVE_PATH = re.compile(
    r"(?:^|[/_.-])(?:reset|password|signin|sign-in|login|logout|unsubscribe|"
    r"verify|verification|activate|magic-link|oauth|confirm)(?:$|[/_.;-])", re.I
)


class ResolutionStop(Exception):
    def __init__(self, reason, status="partial"):
        super().__init__(reason)  # Never include URL/network exception text.
        self.reason = reason
        self.status = status


class Deadline:
    def __init__(self, seconds=TOTAL_SECONDS, clock=time.monotonic):
        self.clock = clock
        self.end = clock() + min(seconds, TOTAL_SECONDS)

    def remaining(self, ceiling=TOTAL_SECONDS):
        remaining = self.end - self.clock()
        if remaining <= 0:
            raise ResolutionStop("TIME_BUDGET_EXCEEDED")
        return min(ceiling, remaining)


@dataclass(frozen=True)
class Target:
    url: str
    scheme: str
    host: str
    port: int
    request_target: str


def public_address(raw):
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        raise ResolutionStop("INVALID_DNS_ANSWER", "blocked") from None
    if not address.is_global or address.is_multicast or address.is_reserved:
        raise ResolutionStop("NON_PUBLIC_DESTINATION", "blocked")
    if address.version == 4 and any(address in net for net in DENIED_V4):
        raise ResolutionStop("NON_PUBLIC_DESTINATION", "blocked")
    if address.version == 6 and (
        address.ipv4_mapped is not None or address.sixtofour is not None
        or address.teredo is not None
        or address in ipaddress.ip_network("64:ff9b::/96")
        or address in ipaddress.ip_network("64:ff9b:1::/48")
    ):
        raise ResolutionStop("UNSUPPORTED_ADDRESS_ENCODING", "blocked")
    return address


def policy_decode(value):
    # Inspect common nested encodings without changing the URL sent on the wire.
    for _ in range(3):
        decoded = unquote(value)
        if decoded == value:
            return decoded.replace("\\", "/")
        value = decoded
    if re.search(r"%[a-fA-F0-9]{2}", value):
        raise ResolutionStop("EXCESSIVE_URL_ENCODING", "blocked")
    return value.replace("\\", "/")


def join_location(base, location):
    joined = urljoin(base, location)
    # urllib drops an explicitly empty query and can retain the base query for
    # '?'. Preserve this distinction: signed endpoints can give it meaning.
    reference = location.split("#", 1)[0]
    if "?" in reference and not reference.split("?", 1)[1]:
        parsed = urlsplit(joined)
        joined = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")) + "?"
    return joined


def parse_target(raw):
    if not isinstance(raw, str) or not raw or len(raw) > MAX_URL_BYTES:
        raise ResolutionStop("INVALID_URL", "invalid_input")
    try:
        if len(raw.encode("utf-8")) > MAX_URL_BYTES:
            raise ResolutionStop("INVALID_URL", "invalid_input")
    except UnicodeError:
        raise ResolutionStop("INVALID_URL", "invalid_input") from None
    if any(ord(c) <= 32 or ord(c) == 127 for c in raw) or "\\" in raw:
        raise ResolutionStop("AMBIGUOUS_URL", "invalid_input")
    if re.search(r"%(?![a-fA-F0-9]{2})", raw) or re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", raw, re.I):
        raise ResolutionStop("INVALID_URL_ENCODING", "invalid_input")
    try:
        parsed = urlsplit(raw)
        if parsed.scheme not in ("http", "https"):
            raise ResolutionStop("UNSUPPORTED_SCHEME", "blocked")
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ResolutionStop("INVALID_AUTHORITY", "invalid_input")
        host = parsed.hostname
        # Do not silently map Unicode lookalikes, escaped authorities, or IPv6 scopes.
        if not host.isascii() or "%" in parsed.netloc or parsed.netloc.endswith(":"):
            raise ResolutionStop("AMBIGUOUS_AUTHORITY", "invalid_input")
        host = host.lower()
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        if port != (443 if parsed.scheme == "https" else 80):
            raise ResolutionStop("UNSUPPORTED_PORT", "blocked")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
                raise ResolutionStop("INVALID_HOST", "invalid_input")
            labels = host.split(".")
            if len(labels) < 2 or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in labels):
                raise ResolutionStop("INVALID_HOST", "invalid_input")
            if labels[-1].isdigit() or re.fullmatch(r"(?:0x[0-9a-f]+|[0-9.]+)", host):
                raise ResolutionStop("AMBIGUOUS_ADDRESS", "invalid_input")
            if host.endswith((".localhost", ".local", ".internal", ".home.arpa")):
                raise ResolutionStop("NON_PUBLIC_DESTINATION", "blocked")
        else:
            public_address(str(address))
        # Preserve existing escapes, query ordering, duplicate and blank parameters.
        path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
        # Python's HTTP client rewrites a leading // request target; reject it
        # instead of silently inspecting a different path.
        if path.startswith("//"):
            raise ResolutionStop("AMBIGUOUS_URL", "invalid_input")
        path_for_policy = policy_decode(path)
        if SENSITIVE_PATH.search(path_for_policy) or any(
            SENSITIVE_KEYS.search(policy_decode(pair.split("=", 1)[0]))
            for pair in re.split(r"[&;]", query) if pair
        ):
            raise ResolutionStop("SENSITIVE_LINK_NOT_FETCHED", "blocked")
        authority = f"[{host}]" if ":" in host else host
        has_query = "?" in raw.split("#", 1)[0]
        url = urlunsplit((parsed.scheme, authority, path, query, ""))
        if has_query and not query:
            url += "?"
        if len(url.encode("ascii")) > MAX_URL_BYTES:
            raise ResolutionStop("INVALID_URL", "invalid_input")
        return Target(url, parsed.scheme, host, port, path + ("?" + query if has_query else ""))
    except (ValueError, UnicodeError):
        raise ResolutionStop("INVALID_URL", "invalid_input") from None


def resolve(url, transport, deadline=None):
    deadline = deadline or Deadline()
    result = {
        "schemaVersion": 1, "resolutionStatus": "partial", "hops": [],
        "lastObservedUrl": None, "httpChainComplete": False,
        "warnings": [], "reasonCodes": [], "requestCount": 0,
        "scope": "HTTP_REDIRECTS_ONLY",
    }
    visited = set()
    current = url
    previous_scheme = None
    try:
        while True:
            deadline.remaining()
            target = parse_target(current)
            if target.url in visited:
                raise ResolutionStop("REDIRECT_LOOP")
            visited.add(target.url)
            if target.scheme == "http" and "UNENCRYPTED_CONNECTION" not in result["warnings"]:
                result["warnings"].append("UNENCRYPTED_CONNECTION")
            if previous_scheme == "https" and target.scheme == "http":
                if "HTTPS_TO_HTTP_REDIRECT" not in result["warnings"]:
                    result["warnings"].append("HTTPS_TO_HTTP_REDIRECT")
            # Resolve all A/AAAA answers once, reject mixed public/private results,
            # then connect to one validated numeric address without resolving again.
            addresses = transport.addresses(target.host, deadline)
            if not addresses or len(addresses) > 32:
                raise ResolutionStop("DNS_ANSWER_UNAVAILABLE")
            validated = [public_address(value) for value in addresses]
            ipv4 = [str(value) for value in validated if value.version == 4]
            if not ipv4:
                raise ResolutionStop("IPV6_ONLY_UNSUPPORTED")
            result["requestCount"] += 1
            status, location = transport.fetch(target, ipv4[0], deadline)
            result["lastObservedUrl"] = target.url
            result["hops"].append({"url": target.url, "httpStatus": status})
            if status in (301, 302, 303, 307, 308):
                if not location:
                    raise ResolutionStop("MISSING_REDIRECT_LOCATION")
                if len(result["hops"]) > MAX_REDIRECTS:
                    raise ResolutionStop("REDIRECT_LIMIT_EXCEEDED")
                # Validate the raw Location before urljoin can strip controls.
                if any(ord(c) <= 32 or ord(c) == 127 for c in location) or "\\" in location:
                    raise ResolutionStop("INVALID_REDIRECT_LOCATION", "blocked")
                if len(location.encode("utf-8")) > MAX_URL_BYTES:
                    raise ResolutionStop("INVALID_REDIRECT_LOCATION", "blocked")
                current = join_location(target.url, location)
                previous_scheme = target.scheme
                continue
            if not 200 <= status < 300:
                raise ResolutionStop("HTTP_RESPONSE_UNSUPPORTED")
            result["resolutionStatus"] = "http_chain_complete"
            result["httpChainComplete"] = True
            # Headers alone cannot detect all JS/meta-refresh/interactive pages.
            result["reasonCodes"] = ["HTTP_TERMINAL_OBSERVED", "BROWSER_NAVIGATION_NOT_EVALUATED"]
            return result
    except ResolutionStop as stopped:
        result["resolutionStatus"] = stopped.status
        result["reasonCodes"] = [stopped.reason]
        return result
