"""Provider validity is independent of receipt retention and usage settlement."""
from datetime import datetime, timezone
from decimal import Decimal
import re
import time

STAMP = re.compile(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?Z')

def epoch(value):
    if type(value) is not str or not (match := STAMP.fullmatch(value)):
        raise ValueError('INVALID_PROVIDER_TIME')
    base = datetime.strptime(match[1], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
    return Decimal(int(base.timestamp())) + Decimal('0.' + (match[2] or '0'))

def observed(clock):
    # Floor only the observation, never extend the provider deadline.
    return datetime.fromtimestamp(int(clock), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def validate(observed_at, valid_until, *, matched):
    start = epoch(observed_at)
    if matched:
        if valid_until is None or epoch(valid_until) <= start:
            raise ValueError('INVALID_PROVIDER_TIME')
    elif valid_until is not None:
        raise ValueError('INVALID_PROVIDER_TIME')

def current(observed_at, valid_until, now):
    validate(observed_at, valid_until, matched=True)
    clock = Decimal(str(now))
    return epoch(observed_at) <= clock < epoch(valid_until)

def evidence(value):
    if type(value) is not dict or set(value) != {'threatTypes','observedAt','validUntil'}:
        raise ValueError('INVALID_PROVIDER_EVIDENCE')
    threats = value['threatTypes']
    if type(threats) is not list or len(threats)>3 or any(type(x) is not str or x not in ('MALWARE','SOCIAL_ENGINEERING','UNWANTED_SOFTWARE') for x in threats) or len(set(threats))!=len(threats):
        raise ValueError('INVALID_PROVIDER_EVIDENCE')
    validate(value['observedAt'], value['validUntil'], matched=bool(threats))
    return value


def wall_clock():
    return Decimal(time.time_ns()) / Decimal(1000000000)
