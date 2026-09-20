"""Private IAM-only Dev backend; API Gateway/consumer events fail closed."""
import json
import os
import time

from assessment_dependencies import Dependencies
from assessment_service import Budget, assess, base_result


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
        result = assess(event, Dependencies(), Budget(remaining))
    # Allowlisted operational counts only. Never raw URL/key/checkId/exception.
    print(json.dumps({'event': 'private_url_assessment', 'status': result['processingOutcome'],
                      'verdict': result['verdict'], 'reason': result['reasonCodes'][0],
                      'lookupCount': result['lookupCount'], 'providerCallCount': result['providerCallCount'],
                      'observedHopCount': result['observedHopCount'],
                      'elapsedMs': int((time.monotonic() - started) * 1000)}))
    return result
