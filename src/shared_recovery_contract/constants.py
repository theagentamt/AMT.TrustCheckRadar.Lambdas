VERSION='1.0.0-recovery-candidate.1'
POLICY='recovery-clarification-2026-09-21-v1'
APPROVAL_SHA='173132b8d5a16d3d5a8ccdbc7355a631f8384634945772993200a3559c89da72'
PLAYBOOK='recovery-playbook-1.0'
BUNDLE_SHA='38d69b097513e65283c77be56fe1bd7e58ac3294db343d2e31427613ff3851d0'
SCOPE='recovery_clarification'
EXPOSURES=('clicked_link','credentials_or_mfa','financial_or_identity','sent_payment','software_or_remote_access')
OUTCOMES=('complete','inconclusive','partial','failed','blocked','invalid_input','unsupported','unavailable')


class RecoveryError(ValueError):
    def __init__(self,code):super().__init__(code);self.code=code


def require(condition,code='INPUT_REJECTED'):
    if not condition:raise RecoveryError(code)
