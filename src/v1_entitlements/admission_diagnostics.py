"""Fixed Dev snapshot-admission failure branch; no identity or verified claim values."""
import json
import logging
import os

_BRANCHES = frozenset({
    'ROUTE_SHAPE', 'ORIGINAL_GATE', 'BASE_GATE_CONFIGURATION', 'EXTRA_GATE_CONFIGURATION',
    'CLAIM_CONFIGURATION', 'VERIFIED_CLAIMS', 'SUBJECT_NOT_ADMITTED',
})


def record_failure(branch):
    try:
        if os.environ.get('STAGE') != 'dev':
            return False
        value = {'event': 'dev_access_admission_failure', 'schemaVersion': 1,
                 'branch': branch if type(branch) is str and branch in _BRANCHES else 'UNKNOWN'}
        logging.getLogger(__name__).warning('%s', json.dumps(value, separators=(',', ':')))
        return True
    except Exception:
        return False
