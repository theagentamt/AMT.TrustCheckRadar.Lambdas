"""Local deterministic reference only; no AWS endpoint, I/O persistence or model.

Approval metadata must come from the reviewed bundled release, never user answers.
Draft selection policy remains gated independently of the approved basic wording.
"""
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).parent
VERSION = 'recovery-selection-1.0.0-candidate.1'
CONTENT_VERSION = 'recovery-basics-1.0'
EXPOSURES = ('clicked_link', 'credentials_or_mfa', 'financial_or_identity',
             'sent_payment', 'software_or_remote_access')
ACTIONS = ('basics_link', 'basics_password', 'basics_identity', 'basics_payment', 'basics_device')
ACTION_PRESENTATION_ORDER = ('basics_payment', 'basics_password', 'basics_identity', 'basics_link', 'basics_device')


class ContractError(ValueError):
    pass


def read(name):
    return json.loads((ROOT / name).read_text())


def validate_answers(answers):
    errors = list(Draft202012Validator(read('selection-input.schema.json')).iter_errors(answers))
    if errors:
        raise ContractError('INVALID_SELECTION')  # Never include incident answers/errors.
    return answers


def ordered_actions(answers):
    """Draft policy mechanics for fixture review; not a display authorization."""
    validate_answers(answers)
    selected = set(answers['exposures'])
    if answers['unsure'] or not selected:
        selected = set(EXPOSURES)
    if 'clicked_link' in selected:
        selected.add('software_or_remote_access')
    included = {action for exposure, action in zip(EXPOSURES, ACTIONS, strict=True) if exposure in selected}
    return [action for action in ACTION_PRESENTATION_ORDER if action in included]


def select(answers, *, signed_in, today, policy=None, content=None):
    validate_answers(answers)
    if type(signed_in) is not bool or type(today) is not date:
        raise ContractError('INVALID_LOCAL_CONTEXT')
    policy = read('selection-policy.json') if policy is None else policy
    content = read('recovery-basics-approved.json') if content is None else content
    result = {'contractVersion': VERSION, 'contentBundleVersion': CONTENT_VERSION,
              'state': 'limited_help', 'reason': None, 'actionIds': [], 'officialLinkIds': [],
              'reviewDue': False, 'clarification': 'unavailable', 'accounting': 'not_an_analysis_check'}
    if not signed_in:
        result.update(state='sign_in_required', reason='sign_in_required')
    elif not isinstance(policy, dict) or policy.get('approval') != 'Approved' or not policy.get('approvalRecord'):
        result['reason'] = 'selection_unapproved'
    elif {k:v for k,v in policy.items() if k not in ('approval','approvalRecord')} != {k:v for k,v in read('selection-policy.json').items() if k not in ('approval','approvalRecord')}:
        result['reason'] = 'content_incompatible'
    elif not isinstance(content, dict) or not content:
        result['reason'] = 'content_missing'
    elif content.get('approval') == 'Withdrawn':
        result['reason'] = 'content_withdrawn'
    elif policy.get('contractVersion') != VERSION or content.get('bundleVersion') != CONTENT_VERSION:
        result['reason'] = 'content_incompatible'
    elif content.get('approval') != 'Approved':
        result['reason'] = 'content_unapproved'
    else:
        # Exact bundled content is the trust boundary, not arbitrary replacement prose.
        approved = read('recovery-basics-approved.json')
        if content != approved:
            result['reason'] = 'content_incompatible'
        else:
            approved_on = date.fromisoformat(approved['approvedOn'])
            due = today < approved_on or today > approved_on + timedelta(days=180)
            result.update(state='review_due' if due else 'available', reason=None,
                          actionIds=ordered_actions(answers), reviewDue=due,
                          officialLinkIds=['recovery_guidance', 'identity_help', 'fraud_report'])
    Draft202012Validator(read('selection-output.schema.json')).validate(result)
    return result


def content_integrity():
    expected = read('selection-policy.json')['contentSha256']
    return hashlib.sha256((ROOT/'recovery-basics-approved.json').read_bytes()).hexdigest() == expected
