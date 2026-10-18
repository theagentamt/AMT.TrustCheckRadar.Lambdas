"""Fail-contained capture wired only from existing authenticated consumers.

API Gateway's verified authorizer context remains the existing trust boundary;
this is neither an independent JWT-signature verifier nor a new HTTP endpoint.
"""
import time
import math
from dataclasses import dataclass, replace
from threading import Lock

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
                          transport_version, remaining_ms, clock=time.monotonic, attempt=None, attempt_scope=None):
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
        ingestion = ReceiptIngestion(authority, can_continue=can_continue)
        # Invalid/stale tickets preserve the existing lifecycle-only capture.
        if (_finish_ticket_matches(attempt, authority, owner, proof, binding, attempt_scope, event)
                and attempt.claim.consume(attempt)):
            try:
                result = ingestion.finish_attempt(TrustedWorkerContext(owner), proof, attempt.execution_token,
                    attempt.reference, outcome=attempt.outcome, duration_ms=attempt.duration_ms, binding=binding)
            except AuthorityError as error:
                if error.code != 'OPERATIONS_INGESTION_SOURCE_INVALID': raise
                result = ingestion.capture(TrustedWorkerContext(owner), proof, binding=binding)
        else:
            result = ingestion.capture(TrustedWorkerContext(owner), proof, binding=binding)
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


class _Once:
    def __init__(self):
        self.lock, self.state, self.ticket = Lock(), 'NEW', None

    def register(self, ticket):
        with self.lock:
            if self.state != 'NEW' or self.ticket is not None: return False
            self.ticket = ticket
            return True

    def enter(self, ticket):
        with self.lock:
            if self.state != 'NEW' or self.ticket is not ticket: return False
            self.state = 'ENTERING'
            return True

    def entered(self, original, entered):
        with self.lock:
            if self.state != 'ENTERING' or self.ticket is not original: return False
            self.ticket, self.state = entered, 'ENTERED'
            return True

    def complete(self, ticket):
        with self.lock:
            if self.state != 'ENTERED' or self.ticket is not ticket: return False
            self.state = 'MEASURING'
            return True

    def seal(self, original, completed):
        with self.lock:
            if self.state != 'MEASURING' or self.ticket is not original: return False
            self.ticket, self.state = completed, 'MEASURED'
            return True

    def consume(self, ticket):
        with self.lock:
            if self.state != 'MEASURED' or self.ticket is not ticket: return False
            self.state = 'CONSUMED'
            return True


@dataclass(frozen=True, repr=False)
class _AttemptTicket:
    authority: object
    owner: str
    proof: str
    execution_token: str
    binding: ReceiptBinding
    reference: str
    scope: object
    started: float
    claim: _Once
    outcome: object = None
    duration_ms: object = None


def _finish_ticket_matches(ticket, authority, owner, proof, binding, scope, event):
    return (type(ticket) is _AttemptTicket and ticket.authority is authority
        and ticket.owner == owner and ticket.proof == proof and ticket.binding == binding
        and type(scope) is object and ticket.scope is scope
        and event.get('routeKey') == f'POST {binding.contract()[0]}'
        and ticket.outcome in ('succeeded', 'failed')
        and type(ticket.duration_ms) is int and 0 <= ticket.duration_ms <= 10**13
        and type(ticket.claim) is _Once)


def begin_authenticated_attempt(authority, event, proof, execution_token, *, family,
        client_check_id, transport_version, scope, request_started, provider_budget_ms,
        remaining_ms, clock=time.monotonic):
    """Optional internal start only; no new dispatcher, retry or public ticket.

    Spend at most the observed spare above the original evaluator/settlement
    reservation, with a one-second pre-dispatch cap. Synchronous SDK completion
    cannot be hard-bounded by this guard; callers must recompute their original
    provider deadline after optional work, independently of this return value.
    """
    try:
        if getattr(getattr(authority, 's', None), 'operations_producer_enabled', False) is not True:
            return None
        if not isinstance(authority, Authority) or type(scope) is not object:
            return None
        binding = ReceiptBinding(family, client_check_id, transport_version)
        prefix = binding.contract()[0]
        if (type(event) is not dict or event.get('version') != '2.0'
                or event.get('routeKey') != f'POST {prefix}'
                or event.get('requestContext', {}).get('http', {}).get('method') != 'POST'
                or event.get('isBase64Encoded') is True or event.get('rawQueryString') or event.get('queryStringParameters')):
            return None
        if type(provider_budget_ms) is not int or not 1000 <= provider_budget_ms <= 18000:
            return None
        config = authority.client.meta.config
        if (config.retries.get('total_max_attempts') != 1
                or not 0 < config.connect_timeout <= .2 or not 0 < config.read_timeout <= .3):
            return None
        observed = clock()
        if (type(request_started) not in (int, float) or not math.isfinite(request_started)
                or type(observed) not in (int, float) or not math.isfinite(observed)
                or observed < request_started):
            return None
        spare = request_started + 25 - observed - 5 - provider_budget_ms / 1000
        if spare < .5: return None
        deadline, last = observed + min(1.0, spare), observed
        floor = provider_budget_ms + 6000
        def can_continue():
            nonlocal last
            try:
                current, remaining = clock(), remaining_ms()
                valid = (type(current) in (int, float) and math.isfinite(current)
                    and last <= current < deadline and type(remaining) is int and remaining >= floor)
                if valid: last = current
                return valid
            except Exception:
                return False
        if not can_continue(): return None
        owner = authority._account(event)
        if not can_continue(): return None
        result = ReceiptIngestion(authority, can_continue=can_continue).start_attempt(
            TrustedWorkerContext(owner), proof, execution_token, binding=binding)
        if result.get('decision') != 'CAPTURED' or not can_continue(): return None
        from shared_operations.projection import validate_capture
        projected = validate_capture(result['projection'])
        events = [e for e in projected['events'] if e['eventType'].startswith('attempt_')]
        if (projected['sourceState'] != 'ADMITTED' or projected['reference']['service'] != family
                or len(events) != 1 or events[0]['eventType'] != 'attempt_started'):
            return None
        began = clock()
        if type(began) not in (int, float) or not math.isfinite(began) or not last <= began < deadline:
            return None
        claim = _Once()
        ticket = _AttemptTicket(authority, owner, proof, execution_token, binding,
            events[0]['attemptRef'], scope, began, claim)
        return ticket if claim.register(ticket) else None
    except Exception:
        # Unknown start acknowledgement is not a usable measurement ticket.
        return None


def enter_internal_attempt(ticket, *, clock=time.monotonic):
    """Pure one-shot entry stamp after request preparation, before invocation."""
    try:
        if (type(ticket) is not _AttemptTicket or type(ticket.claim) is not _Once
                or not ticket.claim.enter(ticket)):
            return None
        entered = clock()
        if (type(entered) not in (int, float) or not math.isfinite(entered)
                or entered < ticket.started):
            return None
        stamped = replace(ticket, started=entered)
        return stamped if ticket.claim.entered(ticket, stamped) else None
    except Exception:
        return None


def complete_internal_attempt(ticket, *, succeeded, clock=time.monotonic):
    """Pure, once-only timing of an entered internal invoke+validation boundary."""
    try:
        if (type(ticket) is not _AttemptTicket or type(ticket.claim) is not _Once
                or type(succeeded) is not bool or not ticket.claim.complete(ticket)):
            return None
        ended = clock()
        if (type(ended) not in (int, float) or not math.isfinite(ended)
                or ended < ticket.started or ended - ticket.started > 10**10):
            return None
        measured = replace(ticket, outcome='succeeded' if succeeded else 'failed',
            duration_ms=int((ended - ticket.started) * 1000))
        return measured if ticket.claim.seal(ticket, measured) else None
    except Exception:
        return None
