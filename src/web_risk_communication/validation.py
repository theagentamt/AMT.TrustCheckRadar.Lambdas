import json
import re
from typing import Any
from urllib import parse


def extract_url(event: Any) -> str:
    if isinstance(event, str):
        raw_value = event
    elif isinstance(event, dict):
        body = event.get("body", event)
        if isinstance(body, str):
            body = body.strip()
            if body.startswith("{"):
                parsed_body = json.loads(body)
                raw_value = parsed_body.get("url")
            else:
                raw_value = body
        elif isinstance(body, dict):
            raw_value = body.get("url")
        else:
            raw_value = None
    else:
        raw_value = None

    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ValueError("A non-empty url is required")
    return raw_value.strip()


def normalize_url(raw_url: str) -> tuple[str, str, str]:
    if len(raw_url) > 2048:
        raise ValueError("url is too long")
    if any(ord(char) < 32 for char in raw_url):
        raise ValueError("url contains invalid control characters")

    parsed = parse.urlsplit(raw_url.strip())
    scheme = parsed.scheme.lower()
    if not scheme:
        raise ValueError("url must include a scheme")
    if not parsed.hostname:
        raise ValueError("url must include a valid hostname")
    if re.search(r"<|>|\"|'|`", raw_url):
        raise ValueError("url contains unsupported characters")

    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as err:
        raise ValueError("url hostname is invalid") from err

    if not re.fullmatch(r"[a-z0-9.-]+", hostname):
        raise ValueError("url hostname contains unsupported characters")
    if hostname in {"localhost"} or hostname.endswith(".local"):
        raise ValueError("url hostname is not allowed")

    netloc = hostname
    if parsed.port and not _is_default_port(scheme, parsed.port):
        netloc = f"{hostname}:{parsed.port}"

    normalized_path = parse.quote(parse.unquote(parsed.path or "/"), safe="/%:@-._~!$&()*+,;=")
    normalized_query = parse.urlencode(parse.parse_qsl(parsed.query, keep_blank_values=True), doseq=True)
    normalized_url = parse.urlunsplit((scheme, netloc, normalized_path, normalized_query, ""))
    domain_uri = parse.urlunsplit((scheme, hostname, "/", "", ""))
    return normalized_url, hostname, domain_uri


def _is_default_port(scheme: str, port: int) -> bool:
    default_ports = {
        "http": 80,
        "https": 443,
        "ftp": 21,
    }
    return default_ports.get(scheme) == port
