"""Closed operations projection. Rejected data is never echoed or quarantined."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

RETENTION_SECONDS = 7 * 86400
MAX_FACT_BYTES = 8192


class OperationsError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def require(condition, code='OPERATIONS_FACT_INVALID'):
    if not condition:
        raise OperationsError(code)


def plain(value):
    if isinstance(value, Decimal):
        require(value.is_finite() and value == value.to_integral_value())
        return int(value)
    if type(value) is dict:
        return {k: plain(v) for k, v in value.items()}
    if type(value) is list:
        return [plain(v) for v in value]
    return value


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError):
        raise OperationsError('OPERATIONS_FACT_INVALID') from None


def validate(value, *, rejection=False):
    """Use the existing JSON-schema dependency only on the enabled publisher path."""
    try:
        from jsonschema import Draft202012Validator
        value = plain(value)
        require(type(value) is dict and len(canonical(value)) <= MAX_FACT_BYTES)
        # JSON Schema permits integral floats; wire counters/timestamps do not.
        require(all(type(v) in (str, int, type(None)) for v in value.values()))
        name = 'rejection.schema.json' if rejection else 'event.schema.json'
        schema = json.loads((Path(__file__).parent / 'contracts' / name).read_text())
        require(Draft202012Validator(schema).is_valid(value))
        return deepcopy(value)
    except OperationsError:
        raise
    except Exception:
        raise OperationsError('OPERATIONS_FACT_INVALID') from None


def empty_event(**fields):
    schema = json.loads((Path(__file__).parent / 'contracts' / 'event.schema.json').read_text())
    result = dict.fromkeys(schema['required'])
    result.update(schemaVersion='1.0.0-operations-candidate.1', trafficClass='customer', reasonCode='NONE')
    result.update(fields)
    return validate(result)
