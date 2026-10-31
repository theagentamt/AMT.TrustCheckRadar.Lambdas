"""Private IAM-only Dev backend; API Gateway/consumer events fail closed."""
import json
import os
import time

from assessment_dependencies import Dependencies
from assessment_service import Budget, assess, base_result, validate_request_envelope, Unavailable


def lambda_handler(event, context):
    started = time.monotonic()
    if os.environ.get('STAGE') != 'dev':
        result = base_result()
        result.update(processingOutcome='unavailable', reasonCodes=['DEV_ONLY_DISABLED'])
    else:
        remaining = max(0, context.get_remaining_time_in_millis() / 1000 - 1.0)
        requested = event.get('executionBudgetMs') if isinstance(event, dict) else None
        if type(requested) is int and 1000 <= requested <= 18000:
            remaining = min(remaining, requested / 1000)
        if type(event) is dict and event.get("schemaVersion") == 2:
            from guarded_service import assess as guarded_assess
            from guarded_dependencies import Dependencies as GuardedDependencies
            result = guarded_assess(event, GuardedDependencies(), Budget(remaining))
        else:
            # Legacy private requests have no guarded evidence semantics. Retire
            # them explicitly instead of silently using resolve-before-lookup.
            result = base_result()
            try:
                validate_request_envelope(event)
            except Unavailable:
                pass
            else:
                result.update(checkId=event['checkId'], processingOutcome='unavailable',
                              reasonCodes=['CONFIGURATION_UNAVAILABLE'])
    # Allowlisted operational counts only. Never raw URL/key/checkId/exception.
    print(json.dumps({'event': 'private_url_assessment', 'status': result['processingOutcome'],
                      'verdict': result['verdict'], 'reason': result['reasonCodes'][0],
                      'lookupCount': result['lookupCount'], 'providerCallCount': result['providerCallCount'],
                      'observedHopCount': result['observedHopCount'],
                      'elapsedMs': int((time.monotonic() - started) * 1000)}))
    return result
