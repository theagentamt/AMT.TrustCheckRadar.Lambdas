"""Fail-contained capture wired only from existing authenticated consumers.

API Gateway's verified authorizer context remains the existing trust boundary;
this is neither an independent JWT-signature verifier nor a new HTTP endpoint.
"""
import time

from .core import Authority, AuthorityError, TrustedWorkerContext
from .operations_binding import ReceiptBinding
from .operations_ingestion import ReceiptIngestion

# Internal request-time safety bounds, not a new retention/cost/SLA policy.
# Existing consumers reserve six seconds after provider work. Leave two seconds
# before returning the already validated response; this is a pre-dispatch
# reservation, not an end-to-end SDK completion guarantee. Calls stay one-shot.
MIN_REMAINING_MS = 6000
MAX_CAPTURE_SECONDS = 4
RESULTS = frozenset({'DISABLED', 'TIME_SKIPPED', 'INPUT_REJECTED', 'AUTH_REJECTED',
    'ACCOUNT_REJECTED', 'SOURCE_REJECTED', 'CONFLICT', 'WRITE_UNKNOWN', 'UNAVAILABLE',
    'CAPTURED', 'REVALIDATED', 'EXPIRED', 'NOT_RECORDED'})


def capture_authenticated(authority, event, proof, *, family, client_check_id,
                          transport_version, remaining_ms, clock=time.monotonic):
    """Return a finite internal result only; never alter a consumer response."""
    try:
        if getattr(getattr(authority, 's', None), 'operations_producer_enabled', False) is not True:
            return 'DISABLED'
        if not isinstance(authority, Authority):
            return 'INPUT_REJECTED'
        try:
            binding = ReceiptBinding(family, client_check_id, transport_version)
            prefix, _, _, _, _ = binding.contract()
        except Exception:
            return 'INPUT_REJECTED'
        if (type(event) is not dict or event.get('version') != '2.0'
                or event.get('routeKey') not in {f'POST {prefix}', f'POST {prefix}/prepare', f'POST {prefix}/reconcile'}
                or event.get('requestContext', {}).get('http', {}).get('method') != 'POST'
                or event.get('isBase64Encoded') is True or event.get('rawQueryString') or event.get('queryStringParameters')):
            return 'INPUT_REJECTED'
        config = authority.client.meta.config
        if (config.retries.get('total_max_attempts') != 1
                or not 0 < config.connect_timeout <= .2 or not 0 < config.read_timeout <= .3):
            return 'TIME_SKIPPED'
        remaining = remaining_ms()
        started = clock()
        if type(remaining) is not int or remaining < MIN_REMAINING_MS or type(started) not in (int, float):
            return 'TIME_SKIPPED'
        import math
        if not math.isfinite(started):
            return 'TIME_SKIPPED'
        deadline = started + min(MAX_CAPTURE_SECONDS, (remaining - 2000) / 1000)
        last = started
        def can_continue():
            nonlocal last
            try:
                current = clock()
                left = remaining_ms()
                valid = (type(current) in (int, float) and math.isfinite(current)
                    and last <= current < deadline and type(left) is int and left >= 2000)
                if valid: last = current
                return valid
            except Exception:
                return False
        if not can_continue():
            return 'TIME_SKIPPED'
        # Only the established authorizer context is read. Body/account fields,
        # provider output and supplied worker-like dictionaries select no owner.
        owner = authority._account(event)
        if not can_continue():
            return 'TIME_SKIPPED'
        result = ReceiptIngestion(authority, can_continue=can_continue).capture(
            TrustedWorkerContext(owner), proof, binding=binding)
        decision = result.get('decision')
        return decision if decision in RESULTS else 'UNAVAILABLE'
    except AuthorityError as error:
        if error.code == 'AUTHENTICATION_REQUIRED': return 'AUTH_REJECTED'
        if error.code in ('ACCOUNT_UNAVAILABLE', 'OPERATIONS_INGESTION_ACCOUNT_UNAVAILABLE'): return 'ACCOUNT_REJECTED'
        if error.code == 'OPERATIONS_INGESTION_CONFLICT': return 'CONFLICT'
        if error.code in ('OPERATIONS_INGESTION_TIME_BUDGET', 'OPERATIONS_RETIREMENT_TIME_BUDGET'): return 'TIME_SKIPPED'
        if error.code in ('OPERATIONS_INGESTION_WRITE_UNKNOWN', 'OPERATIONS_RETIREMENT_TRANSACTION_UNCERTAIN'): return 'WRITE_UNKNOWN'
        if error.code in ('OPERATIONS_INGESTION_SOURCE_INVALID', 'OPERATIONS_INGESTION_SOURCE_UNAVAILABLE',
                          'OPERATION_PROOF_REQUIRED', 'KEY_VERSION_UNAVAILABLE'): return 'SOURCE_REJECTED'
        return 'UNAVAILABLE'
    except Exception:
        return 'UNAVAILABLE'
