"""No purchase/operator grants are exposed through this mobile service."""
from shared_check_authority.core import AuthorityError, OWNER_POLICY, TRIAL_SECONDS


def access_snapshot(writer, event):
    authority = writer.a
    account = authority._account(event)
    pk, old, sources = writer._read(account)
    activated, histories = writer.trial_history(account, sources)
    if old:
        writer.refresh_for_account(event, 'refresh-' + str(old['revision']))
    baseline = authority._get(authority.s.authority_table, {'PK': pk, 'SK': 'ACCESS'})
    def device_state():
        try:
            return authority._device(event, account)
        except AuthorityError as error:
            if error.code != 'ACTIVE_DEVICE_REQUIRED':
                raise
            return None
    device = device_state()
    active_device = device is not None
    grant = period = None
    reason = 'AVAILABLE'
    try:
        grant, period = authority._grant(pk, allow_exhausted=True)
        if period and period['usedChecks'] + period['reservedChecks'] >= period['limit']:
            reason = 'ALLOWANCE_EXHAUSTED'
    except AuthorityError as error:
        if error.code == 'EXTERNAL_ACCESS_UNAVAILABLE':
            reason = error.code
        else:
            raise
    if grant and grant['basis'] == 'trial':
        if (activated is None or activated != grant['activatedAtEpoch']
                or period['startEpoch'] != activated or period['endEpoch'] != activated + TRIAL_SECONDS):
            raise AuthorityError('AUTHORITY_STATE_INVALID')
    # A snapshot is advisory, but must not combine a moved account/device/grant
    # or changed trial history with counters read from an earlier state.
    current = authority._get(authority.s.authority_table, {'PK': pk, 'SK': 'ACCESS'})
    if current != baseline or grant is not None and grant != baseline:
        raise AuthorityError('AUTHORITY_SNAPSHOT_CHANGED')
    _, current_histories = writer.trial_history(account, sources)
    if current_histories != histories or device_state() != device:
        raise AuthorityError('AUTHORITY_SNAPSHOT_CHANGED')
    if grant and grant.get('validUntilEpoch') is not None and grant['validUntilEpoch'] <= authority.now():
        raise AuthorityError('AUTHORITY_SNAPSHOT_CHANGED')
    if period and period['endEpoch'] <= authority.now():
        raise AuthorityError('AUTHORITY_SNAPSHOT_CHANGED')
    authority._assert_account(account)
    if not active_device:
        reason = 'ACTIVE_DEVICE_REQUIRED'
    basis = grant['basis'] if grant else 'none'
    allowance = {'limit': None, 'completedUsed': None, 'reserved': None, 'remaining': None, 'periodEndsAtEpoch': None}
    if period:
        allowance = {'limit': int(period['limit']), 'completedUsed': int(period['usedChecks']),
                     'reserved': int(period['reservedChecks']),
                     'remaining': int(period['limit'] - period['usedChecks'] - period['reservedChecks']),
                     'periodEndsAtEpoch': int(period['endEpoch'])}
    # Snapshot is advisory. Every admission repeats the atomic account/device/grant fences.
    return {'schemaVersion': 1, 'policyVersion': OWNER_POLICY, 'activeDevice': active_device,
            'access': {'basis': basis, 'externalChecksAllowed': reason == 'AVAILABLE', 'reason': reason},
            'allowance': allowance,
            'trial': {'activationAvailable': writer.trial_retention_approved and active_device and activated is None and basis == 'none',
                      'activatedAtEpoch': activated,
                      'expiresAtEpoch': activated + TRIAL_SECONDS if activated is not None else None}}
