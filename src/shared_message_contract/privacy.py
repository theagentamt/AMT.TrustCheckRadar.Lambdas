"""Additive intake privacy checks; immutable transport schemas stay unchanged.

Reject remaining complete IPv6 literals, including mapped/zone forms. This is a
bounded lexical guard, not ownership inference or comprehensive PII detection.
"""
import ipaddress
import re

from .validation import TOKEN, require, validate_intent as validate_contract_intent

# Complete isolated address-like lexemes only. A namespace/word attached to an
# address is not silently split into a shorter plausible address. Length is bounded
# by the reviewed-text limit before scanning, and no DNS/network operation occurs.
IPV6_LEXEME = re.compile(r'(?<![\w:.%])(?:[0-9a-fA-F]*:){2,}[0-9a-fA-F:.]*(?:%[A-Za-z0-9_.~-]+)?(?![\w:%])')


def reject_residual_ipv6(text):
    require(type(text) is str and len(text) <= 8000)
    residual = TOKEN.sub('', text)
    for match in IPV6_LEXEME.finditer(residual):
        candidate = match.group().rstrip('.')
        address = candidate.split('%', 1)[0]
        # Bare :: commonly acts as punctuation. Its meaning is not inferred.
        if not any(c in '0123456789abcdefABCDEF' for c in address):
            continue
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        require(parsed.version != 6, 'PRIVACY_REVIEW_REQUIRED')
    return text


def validate_runtime_intent(value):
    validate_contract_intent(value)
    reject_residual_ipv6(value['target']['sanitizedText'])
    return value
