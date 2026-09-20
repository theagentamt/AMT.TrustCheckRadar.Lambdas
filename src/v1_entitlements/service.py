"""No purchase/operator grants are exposed through this mobile service."""
from shared_check_authority.core import AuthorityError, OWNER_POLICY, TRIAL_SECONDS, integral


def access_snapshot(writer, event):
    authority = writer.a
    account = authority._account(event)
    pk, old, sources = writer._read(account)
    if old:
        writer.refresh_for_account(event, 'refresh-' + str(old['revision']))
    active_device = True
    try:
        authority._device(event, account)
    except AuthorityError as error:
        if error.code != 'ACTIVE_DEVICE_REQUIRED':
            raise
        active_device = False
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
    # Recheck revision after counter reads; never report a mixed revoked snapshot.
    current = authority._get(authority.s.authority_table, {'PK': pk, 'SK': 'ACCESS'})
    if grant and (not current or current.get('revision') != grant['revision']):
        raise AuthorityError('AUTHORITY_SNAPSHOT_CHANGED')
    authority._assert_account(account)
    if not active_device:
        reason = 'ACTIVE_DEVICE_REQUIRED'
    history = None
    for partition in authority.deletion_partitions(account):
        candidate = authority._get(authority.s.authority_table, {'PK': partition, 'SK': 'TRIAL_HISTORY'})
        if candidate:
            if (candidate.get('recordType') != 'V1_TRIAL_ELIGIBILITY' or candidate.get('policyVersion') != OWNER_POLICY
                    or integral(candidate.get('activatedAtEpoch')) is None):
                raise AuthorityError('AUTHORITY_STATE_INVALID')
            history = candidate
    activated = int(history['activatedAtEpoch']) if history else None
    basis = grant['basis'] if grant else 'none'
    allowance = {'limit': None, 'completedUsed': None, 'reserved': None, 'remaining': None, 'periodEndsAtEpoch': None}
    if period:
        allowance = {'limit': int(period['limit']), 'completedUsed': int(period['usedChecks']),
                     'reserved': int(period['reservedChecks']),
                     'remaining': max(0, int(period['limit'] - period['usedChecks'] - period['reservedChecks'])),
                     'periodEndsAtEpoch': int(period['endEpoch'])}
    # Snapshot is advisory. Every admission repeats the atomic account/device/grant fences.
    return {'schemaVersion': 1, 'policyVersion': OWNER_POLICY, 'activeDevice': active_device,
            'access': {'basis': basis, 'externalChecksAllowed': reason == 'AVAILABLE', 'reason': reason},
            'allowance': allowance,
            'trial': {'activationAvailable': writer.trial_retention_approved and active_device and not history and basis == 'none',
                      'activatedAtEpoch': activated,
                      'expiresAtEpoch': activated + TRIAL_SECONDS if activated is not None else None}}
