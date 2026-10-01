"""Bounded, reconstructable per-contributor metadata; no content or retention changes."""
from decimal import Decimal
from .app_features import FINGERPRINT_PATTERN, SIGNAL_ID_PATTERN

FIELDS = {'lexicalFingerprint': (32, FINGERPRINT_PATTERN),
          'signalIds': (16, SIGNAL_ID_PATTERN), 'indicatorIds': (16, SIGNAL_ID_PATTERN)}


def validate_metadata(row):
    version = row.get('metadataSchemaVersion')
    if type(version) not in (int, Decimal) or version != 1:
        raise ValueError('Campaign metadata requires reviewed migration')
    result = {}
    for field, (limit, pattern) in FIELDS.items():
        values = row.get(field)
        if (type(values) is not list or len(values) > limit
                or any(type(v) is not str or not pattern.fullmatch(v) for v in values)
                or len(set(values)) != len(values)):
            raise ValueError('Campaign metadata is invalid')
        result[field] = sorted(values)
    return result


def merge_metadata(left, right):
    # Keeping the smallest bounded union is associative, including across pages.
    return {field: sorted(set(left[field]) | set(right[field]))[:limit]
            for field, (limit, _) in FIELDS.items()}


def empty_metadata():
    return {field: [] for field in FIELDS}
