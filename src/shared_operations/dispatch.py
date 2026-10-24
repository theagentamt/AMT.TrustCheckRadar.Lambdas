"""Trusted dispatch hooks. They never obtain credentials or select a provider."""
import json
import time
from contextlib import contextmanager

from .contract import require


def finished(observer, outcome, reason, request_units):
    if observer is None: return
    try:
        observer.finished(outcome, reason, request_units=request_units)
    except Exception:
        # A missing finish stays unknown. It cannot repeat a paid request or
        # invalidate an otherwise usable assessment/accounting decision.
        print(json.dumps({'event': 'operations_finish_unavailable'}, separators=(',', ':')))


class JournalObserver:
    """Server-created per-attempt observer; never constructed from public JSON.

    Runtime construction at the private evaluator remains off until SEC243
    storage/IAM/configuration qualification. The actual adapters accept this
    permanent hook without changing their public outcome schema.
    """
    def __init__(self, publisher, account, proof, execution_token, *, component, ordinal,
                 provider, input_kind=None, clock=time.monotonic):
        self.publisher, self.account, self.proof, self.token = publisher, account, proof, execution_token
        self.component, self.ordinal, self.provider, self.input_kind = component, ordinal, provider, input_kind
        self.clock, self.permit, self.claimed, self.done, self.cost = clock, None, False, False, None

    def start(self):
        require(not self.claimed, 'OPERATIONS_ATTEMPT_ALREADY_CLAIMED')
        self.claimed = True
        self.started_at = self.clock()
        self.permit = self.publisher.start_attempt(self.account, self.proof, self.token,
            component=self.component, ordinal=self.ordinal, provider=self.provider, input_kind=self.input_kind)

    def observed_usage(self, report):
        require(type(report) is dict and set(report) == {'usage', 'failureReason'}, 'OPERATIONS_USAGE_INVALID')
        usage = report['usage']
        if usage is None:
            self.cost = None
            return
        names = {'inputTokens', 'outputTokens', 'cachedTokens', 'reasoningTokens', 'tokenCostUpperNano'}
        require(type(usage) is dict and set(usage) == names
                and all(type(v) is int and v >= 0 for v in usage.values()), 'OPERATIONS_USAGE_INVALID')
        # Round up the existing conservative estimate; invoices remain separate.
        micro = (usage['tokenCostUpperNano'] + 999) // 1000
        self.cost = micro if micro <= 1_000_000_000 else None

    def finished(self, outcome, reason, *, request_units=None):
        if self.permit is None: return  # No acknowledged start, hence no dispatch permit.
        require(not self.done, 'OPERATIONS_ATTEMPT_ALREADY_FINISHED')
        self.done = True
        duration = max(0, self.clock() - self.started_at)
        bucket = ('lt_250ms' if duration < .25 else '250_999ms' if duration < 1 else
                  '1_4s' if duration < 5 else '5_14s' if duration < 15 else '15s_plus')
        return self.publisher.finish_attempt(self.permit, outcome=outcome, reason=reason,
            duration=bucket, request_units=request_units, cost_microusd=0 if request_units == 0 else self.cost)


@contextmanager
def component_attempt(authority, account, proof, execution_token, component):
    publisher = getattr(authority, 'operations', None)
    if publisher is None:
        yield
        return
    observer = JournalObserver(publisher, account, proof, execution_token,
        component=component, ordinal=1, provider=None)
    observer.start()
    try:
        yield
    except Exception:
        finished(observer, 'failed', 'SERVICE_UNAVAILABLE', None)
        raise
    else:
        finished(observer, 'succeeded', 'NONE', None)
