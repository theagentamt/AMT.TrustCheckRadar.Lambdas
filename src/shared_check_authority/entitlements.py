"""Undeployed V1 authority writers. Trusted store/operator adapters are mandatory.

No HTTP event is accepted as paid or operator authority. The store callback must
verify purchase ownership, current monthly period and ordered lifecycle evidence;
its typed result is an internal trust boundary, not receipt verification itself.
"""
from copy import deepcopy
from dataclasses import dataclass, field
import json
import hashlib
import re

from .core import (AuthorityError, OWNER_POLICY, PAID_COMPLETED_CHECKS,
                   TRIAL_COMPLETED_CHECKS, TRIAL_SECONDS, integral)


@dataclass(frozen=True)
class VerifiedMonthlyDecision:
    """Only a server verifier with an account-bound store ledger may create this."""
    account: str = field(repr=False)
    store: str
    product_id: str
    subscription_id: str = field(repr=False)
    source_revision: int
    verified_at_epoch: int
    active: bool
    period_id: str | None = field(default=None, repr=False)
    period_start_epoch: int | None = None
    period_end_epoch: int | None = None
    billing_period: str = 'P1M'
    require_existing_usage: bool = False
    access_until_epoch: int | None = None
    head_token_digest: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class TrustedLifecycleWorker:
    """Server-created worker identity; never deserialize HTTP/PubSub body fields."""
    principal_arn: str


@dataclass(frozen=True)
class TrustedOperator:
    """Construct from authenticated IAM context, never request-body fields."""
    principal_arn: str
    session_id: str = field(repr=False)


def _json_integral(value):
    number = integral(value)
    if number is None:
        raise AuthorityError('AUTHORITY_STATE_INVALID')
    return number


class EntitlementWriter:
    def __init__(self, authority, *, approved_products, operator_principals,
                 store_verifier=None, verification_max_age_seconds, trial_retention_approved=False, lifecycle_principals=frozenset()):
        if (type(verification_max_age_seconds) is not int or verification_max_age_seconds <= 0
                or not isinstance(approved_products, frozenset)
                or any(not isinstance(x, tuple) or len(x) != 2 or x[0] not in ('google_play', 'app_store')
                       or not isinstance(x[1], str) or not x[1] for x in approved_products)
                or not isinstance(operator_principals, frozenset)
                or any(not isinstance(x, str) or not re.fullmatch(r'arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9_+=,.@/-]+', x)
                       for x in operator_principals)):
            raise AuthorityError('WRITER_CONFIGURATION_UNAVAILABLE')
        if not isinstance(lifecycle_principals, frozenset) or any(not isinstance(x,str) or not re.fullmatch(r'arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9_+=,.@/-]+',x) for x in lifecycle_principals):
            raise AuthorityError('WRITER_CONFIGURATION_UNAVAILABLE')
        self.lifecycle_principals = lifecycle_principals
        self.a = authority
        self.products = approved_products
        self.operators = operator_principals
        self.verify_store = store_verifier
        self.verification_max_age = verification_max_age_seconds
        self.trial_retention_approved = trial_retention_approved is True

    def _read(self, account):
        self.a._assert_account(account)
        pk = self.a._partition(account, self.a.s.active_key_id)
        # Rotation requires migration; do not create independently spendable grants.
        for other in self.a.deletion_partitions(account):
            if other != pk and self.a._get(self.a.s.authority_table, {'PK': other, 'SK': 'ACCESS'}):
                raise AuthorityError('AUTHORITY_MIGRATION_REQUIRED')
        row = self.a._get(self.a.s.authority_table, {'PK': pk, 'SK': 'ACCESS'})
        if row is not None and (row.get('recordType') != 'V1_ACCESS_AUTHORITY'
                or integral(row.get('schemaVersion')) != 1 or row.get('policyVersion') != OWNER_POLICY
                or integral(row.get('revision')) is None or row['revision'] < 1
                or not isinstance(row.get('sources'), dict)
                or not set(row['sources']).issubset({'trial', 'paid', 'complimentary'})):
            raise AuthorityError('AUTHORITY_STATE_INVALID')
        if row is not None:
            self._validate_sources(row['sources'])
            if row.get('state') not in ('ACTIVE', 'INACTIVE'):
                raise AuthorityError('AUTHORITY_STATE_INVALID')
            if row['state'] == 'ACTIVE':
                basis = row.get('basis')
                source = row['sources'].get(basis) if isinstance(basis, str) else None
                fields = ('validFromEpoch', 'validUntilEpoch') + (() if basis == 'complimentary' else ('periodId', 'periodRevision'))
                if (not source or source['state'] != 'ACTIVE'
                        or integral(row.get('validFromEpoch')) is None
                        or row.get('validUntilEpoch') is not None and integral(row['validUntilEpoch']) is None
                        or basis != 'complimentary' and integral(row.get('periodRevision')) is None
                        or any(row.get(name) != source.get(name) for name in fields)):
                    raise AuthorityError('AUTHORITY_STATE_INVALID')
        return pk, row, deepcopy(row['sources']) if row else {}

    @staticmethod
    def _validate_sources(sources):
        """Only accept source forms actually emitted by these trusted writers."""
        period_fields = {'validFromEpoch', 'validUntilEpoch', 'periodId', 'periodRevision'}
        paid_fields = {'state', 'store', 'productId', 'subscriptionDigest', 'sourceRevision'}
        for basis, source in sources.items():
            if not isinstance(source, dict) or source.get('state') not in ('ACTIVE', 'INACTIVE'):
                raise AuthorityError('AUTHORITY_STATE_INVALID')
            if basis == 'paid':
                allowed = paid_fields | period_fields
                shape = set(source) - {'headTokenDigest'}
                if ('headTokenDigest' in source and (not isinstance(source['headTokenDigest'],str) or not re.fullmatch(r'[0-9a-f]{64}',source['headTokenDigest']))
                        or shape not in (paid_fields, allowed)
                        or source['state'] == 'ACTIVE' and shape != allowed
                        or source.get('store') not in ('google_play', 'app_store')
                        or not isinstance(source.get('productId'), str) or not source['productId']
                        or not isinstance(source.get('subscriptionDigest'), str)
                        or not re.fullmatch(r'[0-9a-f]{64}', source['subscriptionDigest'])
                        or integral(source.get('sourceRevision')) is None or source['sourceRevision'] < 1):
                    raise AuthorityError('AUTHORITY_STATE_INVALID')
                if shape == paid_fields:
                    continue  # A verified inactive subscription may never have a funded period.
            elif basis == 'trial':
                if set(source) != {'state'} | period_fields:
                    raise AuthorityError('AUTHORITY_STATE_INVALID')
            elif set(source) != {'state', 'validFromEpoch', 'validUntilEpoch'}:
                raise AuthorityError('AUTHORITY_STATE_INVALID')
            start, end = integral(source.get('validFromEpoch')), integral(source.get('validUntilEpoch'))
            if (start is None or start < 0 or (source.get('validUntilEpoch') is None and basis != 'complimentary')
                    or source.get('validUntilEpoch') is not None and (end is None or end < start or end == start and not (basis == 'paid' and source['state'] == 'INACTIVE'))
                    or basis == 'trial' and end != start + TRIAL_SECONDS):
                raise AuthorityError('AUTHORITY_STATE_INVALID')
            if basis != 'complimentary':
                if (not isinstance(source.get('periodId'), str)
                        or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', source['periodId'])
                        or integral(source.get('periodRevision')) is None or source['periodRevision'] < 1):
                    raise AuthorityError('AUTHORITY_STATE_INVALID')

    def trial_history(self, account, sources=None):
        """Validate retained eligibility evidence without repairing or extending it.

        Multiple retained namespaces may carry the same historical activation;
        conflicting clocks must never be normalized into new eligibility.
        """
        histories = {}
        activated = None
        for partition in self.a.deletion_partitions(account):
            row = self.a._get(self.a.s.authority_table, {'PK': partition, 'SK': 'TRIAL_HISTORY'})
            if row is None:
                histories[partition] = None
                continue
            epoch = integral(row.get('activatedAtEpoch'))
            if (set(row) != {'PK', 'SK', 'recordType', 'policyVersion', 'activationKind', 'activatedAtEpoch'}
                    or row['PK'] != partition or row['SK'] != 'TRIAL_HISTORY'
                    or row.get('recordType') != 'V1_TRIAL_ELIGIBILITY'
                    or row.get('policyVersion') != OWNER_POLICY or row.get('activationKind') != 'explicit'
                    or epoch is None or not 0 <= epoch <= self.a.now()
                    or activated is not None and activated != epoch):
                raise AuthorityError('AUTHORITY_STATE_INVALID')
            activated = epoch
            histories[partition] = row
        if sources is not None and 'trial' in sources:
            source = sources['trial']
            if (not isinstance(source, dict) or activated is None
                    or source.get('validFromEpoch') != activated
                    or source.get('validUntilEpoch') != activated + TRIAL_SECONDS):
                raise AuthorityError('AUTHORITY_STATE_INVALID')
        return activated, histories

    def _device_conditions(self, event, account):
        fingerprint, version = self.a._device(event, account)
        return [self.a._check(self.a.s.devices_table, {'PK': 'USER#' + account, 'SK': 'ACTIVE_BINDING'},
                             'recordType = :t AND stateVersion = :v AND bindingFingerprint = :f',
                             {':t': 'ACTIVE_BINDING_POINTER', ':v': version, ':f': fingerprint}),
                self.a._check(self.a.s.devices_table, {'PK': 'USER#' + account, 'SK': 'DEVICE#' + fingerprint},
                             '#s = :s AND accountId = :a AND bindingFingerprint = :f',
                             {':s': 'ACTIVE', ':a': account, ':f': fingerprint}, {'#s': 'status'})]

    def _put(self, item, condition='attribute_not_exists(PK)', values=None):
        op = {'TableName': self.a.s.authority_table, 'Item': item, 'ConditionExpression': condition}
        if values:
            op['ExpressionAttributeValues'] = values
        return {'Put': op}

    def _effective(self, pk, old, sources):
        now = self.a.now()
        row = {'PK': pk, 'SK': 'ACCESS', 'recordType': 'V1_ACCESS_AUTHORITY',
               'schemaVersion': 1, 'policyVersion': OWNER_POLICY,
               'revision': (int(old['revision']) if old else 0) + 1, 'sources': sources,
               'state': 'INACTIVE'}
        for basis in ('complimentary', 'paid', 'trial'):
            source = sources.get(basis)
            if not source or source.get('state') != 'ACTIVE':
                continue
            start, end = source.get('validFromEpoch'), source.get('validUntilEpoch')
            if (integral(start) is None or start > now
                    or (end is None and basis != 'complimentary')
                    or (end is not None and (integral(end) is None or end <= now))):
                continue
            row.update(state='ACTIVE', basis=basis, validFromEpoch=start, validUntilEpoch=end)
            if basis != 'complimentary':
                row.update(periodId=source['periodId'], periodRevision=source['periodRevision'])
            if basis == 'trial':
                row.update(activationKind='explicit', activatedAtEpoch=start)
            break
        return row

    def _commit(self, account, pk, old, sources, operation_id, action, details, *, extras=(), actor=None, request_digest=None):
        if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', operation_id):
            raise AuthorityError('WRITER_INPUT_REJECTED')
        kid = self.a.s.active_key_id
        op_sk = 'AUTHORITY_OP#' + self.a._mac(kid, 'authority-operation', operation_id)
        digest = self.a._mac(kid, 'authority-mutation', json.dumps(
            {'action': action, 'details': details, 'actor': actor}, sort_keys=True, separators=(',', ':'), default=_json_integral))
        def receipt():
            found = self.a._get(self.a.s.authority_table, {'PK': pk, 'SK': op_sk})
            if found:
                if found.get('mutationDigest') != digest:
                    raise AuthorityError('AUTHORITY_OPERATION_CONFLICT')
                return {'revision': int(found['revision']), 'state': found['effectiveState'],
                        'basis': found.get('effectiveBasis')}
            return None
        prior = receipt()
        if prior:
            return prior
        row = self._effective(pk, old, sources)
        audit = {'PK': pk, 'SK': op_sk, 'recordType': 'V1_AUTHORITY_AUDIT',
                 'policyVersion': OWNER_POLICY, 'mutationDigest': digest,
                 'action': action, 'recordedAtEpoch': self.a.now(), 'revision': row['revision'],
                 'effectiveState': row['state'], 'effectiveBasis': row.get('basis')}
        if request_digest is not None:
            if not isinstance(request_digest, str) or not re.fullmatch(r'[0-9a-f]{64}', request_digest):
                raise AuthorityError('WRITER_INPUT_REJECTED')
            audit['requestDigest'] = request_digest
        if actor:
            audit['operatorPrincipalArn'] = actor['principal']
            audit['operatorSessionDigest'] = actor['session']
            audit['reasonCode'] = details['reasonCode']
            audit['complimentaryEnabled'] = details['enabled']
            audit['complimentaryExpiresAtEpoch'] = details['expiresAt']
        condition = 'revision = :revision' if old else 'attribute_not_exists(PK)'
        values = {':revision': old['revision']} if old else None
        items = self.a._account_conditions(account)
        for other in self.a.deletion_partitions(account):
            if other != pk:
                items.append(self.a._check(self.a.s.authority_table, {'PK': other, 'SK': 'ACCESS'}, 'attribute_not_exists(PK)'))
        items += list(extras) + [self._put(row, condition, values), self._put(audit)]
        if len(items) > 100:
            raise AuthorityError('PURCHASE_USAGE_RECONCILIATION_REQUIRED')
        try:
            self.a._transact(items)
        except AuthorityError:
            # A lost successful response is reconciled with its exact mutation ID.
            committed = receipt()
            if committed:
                return committed
            raise
        return {'revision': row['revision'], 'state': row['state'], 'basis': row.get('basis')}

    def activate_trial(self, verified_event):
        if not self.trial_retention_approved:
            raise AuthorityError('TRIAL_RETENTION_APPROVAL_REQUIRED')
        account = self.a._account(verified_event)
        device_conditions = self._device_conditions(verified_event, account)
        pk, old, sources = self._read(account)
        activated, _ = self.trial_history(account, sources)
        if activated is not None:
            return {'activatedAtEpoch': activated,
                    'validUntilEpoch': activated + TRIAL_SECONDS,
                    'alreadyActivated': True}
        if any(s.get('state') == 'ACTIVE' and (s.get('validUntilEpoch') is None or s['validUntilEpoch'] > self.a.now())
               for s in sources.values()):
            raise AuthorityError('TRIAL_NOT_ELIGIBLE')
        now = self.a.now()
        revision = (int(old['revision']) if old else 0) + 1
        period_id = 'trial-' + self.a._mac(self.a.s.active_key_id, 'trial-period', account)[:40]
        sources['trial'] = {'state': 'ACTIVE', 'validFromEpoch': now, 'validUntilEpoch': now + TRIAL_SECONDS,
                            'periodId': period_id, 'periodRevision': revision}
        history = {'PK': pk, 'SK': 'TRIAL_HISTORY', 'recordType': 'V1_TRIAL_ELIGIBILITY',
                   'policyVersion': OWNER_POLICY, 'activationKind': 'explicit', 'activatedAtEpoch': now}
        period = self._period(pk, period_id, revision, now, now + TRIAL_SECONDS, TRIAL_COMPLETED_CHECKS)
        extras = device_conditions + [self._put(history), self._put(period)]
        for other in self.a.deletion_partitions(account):
            if other != pk:
                extras.append(self.a._check(self.a.s.authority_table, {'PK': other, 'SK': 'TRIAL_HISTORY'}, 'attribute_not_exists(PK)'))
        self._commit(account, pk, old, sources, 'explicit-trial-activation', 'ACTIVATE_TRIAL', {}, extras=extras)
        stored = self.a._get(self.a.s.authority_table, {'PK': pk, 'SK': 'TRIAL_HISTORY'})
        if not stored:
            raise AuthorityError('TRANSACTION_UNCERTAIN')
        activated = int(stored['activatedAtEpoch'])
        return {'activatedAtEpoch': activated, 'validUntilEpoch': activated + TRIAL_SECONDS, 'alreadyActivated': False}

    @staticmethod
    def _period(pk, period_id, revision, start, end, limit):
        return {'PK': pk, 'SK': 'PERIOD#' + period_id, 'recordType': 'V1_ALLOWANCE_PERIOD',
                'policyVersion': OWNER_POLICY, 'grantRevision': revision, 'limit': limit,
                'startEpoch': start, 'endEpoch': end, 'usedChecks': 0, 'reservedChecks': 0}

    def synchronize_paid(self, verified_event, purchase_reference, operation_id):
        account = self.a._account(verified_event)
        device_conditions = self._device_conditions(verified_event, account)
        if self.verify_store is None:
            raise AuthorityError('VERIFIED_STORE_AUTHORITY_UNAVAILABLE')
        # purchase_reference is forwarded to the verifier only; never persisted/logged.
        decision = self.verify_store(account, purchase_reference)
        return self.commit_verified_paid(verified_event, decision, operation_id)

    def commit_verified_paid(self, verified_event, decision, operation_id, *, observation=None, transaction_extras=(), request_digest=None):
        """Internal atomic adapter boundary; never deserialize a client decision.

        A modern Play adapter supplies pre-provider ACCESS/device observations and
        exact ownership CAS actions. Existing verifier callers keep their API.
        """
        account = self.a._account(verified_event)
        device_conditions = self._device_conditions(verified_event, account)
        expected = None
        if observation is not None:
            if set(observation) != {'access', 'device'} or self.a._device(verified_event, account) != observation['device']:
                raise AuthorityError('STORE_OBSERVATION_CHANGED')
            expected = observation['access']
        return self._commit_paid(account, decision, operation_id, observed=observation is not None,
            expected_access=expected, extras=device_conditions + list(transaction_extras), request_digest=request_digest)

    def commit_background_paid(self, worker, decision, operation_id, *, expected_access, ownership_actions, token_actions=(), request_digest=None):
        if type(worker) is not TrustedLifecycleWorker or worker.principal_arn not in self.lifecycle_principals:
            raise AuthorityError('LIFECYCLE_AUTHORIZATION_REQUIRED')
        if type(decision) is not VerifiedMonthlyDecision or not isinstance(expected_access,dict) or not ownership_actions:
            raise AuthorityError('LIFECYCLE_OBSERVATION_REQUIRED')
        # Only pre-existing ownership may be reconciled in the background. The
        # adapter supplies exact inventory/owner/locator ConditionChecks, never
        # ownership claims, automatic restores or an invented device session.
        if any(set(action) != {'ConditionCheck'} for action in ownership_actions):
            raise AuthorityError('LIFECYCLE_OBSERVATION_REQUIRED')
        return self._commit_paid(decision.account, decision, operation_id, observed=True,
            expected_access=expected_access, extras=list(ownership_actions)+list(token_actions), request_digest=request_digest)

    def _commit_paid(self, account, decision, operation_id, *, observed, expected_access, extras, request_digest):
        if type(decision) is not VerifiedMonthlyDecision:
            raise AuthorityError('VERIFIED_STORE_AUTHORITY_UNAVAILABLE')
        now = self.a.now()
        if (decision.account != account or (decision.store, decision.product_id) not in self.products
                or type(decision.active) is not bool or type(decision.require_existing_usage) is not bool or decision.billing_period != 'P1M'
                or type(decision.source_revision) is not int or decision.source_revision < 1
                or type(decision.verified_at_epoch) is not int
                or not now - self.verification_max_age <= decision.verified_at_epoch <= now
                or not isinstance(decision.subscription_id, str) or not 1 <= len(decision.subscription_id) <= 512
                or decision.head_token_digest is not None and (not isinstance(decision.head_token_digest,str) or not re.fullmatch(r'[0-9a-f]{64}',decision.head_token_digest))):
            raise AuthorityError('VERIFIED_STORE_AUTHORITY_UNAVAILABLE')
        pk, old, sources = self._read(account)
        if observed and old != expected_access:
            raise AuthorityError('STORE_OBSERVATION_CHANGED')
        kid = self.a.s.active_key_id
        subscription = self.a._mac(kid, 'store-subscription', decision.store + '\0' + decision.subscription_id)
        previous = sources.get('paid')
        if previous and (previous.get('subscriptionDigest') != subscription
                         or decision.source_revision < previous.get('sourceRevision', 0)):
            # Linked token/subscription changes require an explicit verified migration.
            raise AuthorityError('STORE_AUTHORITY_MIGRATION_REQUIRED')
        source = {'state': 'ACTIVE' if decision.active else 'INACTIVE', 'store': decision.store,
                  'productId': decision.product_id, 'subscriptionDigest': subscription,
                  'sourceRevision': decision.source_revision}
        if decision.head_token_digest is not None:
            source['headTokenDigest'] = decision.head_token_digest
        elif previous and 'headTokenDigest' in previous:
            source['headTokenDigest'] = previous['headTokenDigest']
        if decision.active:
            start, end = decision.period_start_epoch, decision.period_end_epoch
            access_end = end if decision.access_until_epoch is None else decision.access_until_epoch
            if (not isinstance(decision.period_id, str) or not 1 <= len(decision.period_id) <= 512
                    or type(start) is not int or type(end) is not int or type(access_end) is not int
                    or not start < end <= access_end or not start <= now < access_end):
                raise AuthorityError('VERIFIED_MONTHLY_PERIOD_UNAVAILABLE')
            period_id = 'paid-' + self.a._mac(kid, 'store-period', subscription + '\0' + decision.period_id)[:40]
            period = self.a._get(self.a.s.authority_table, {'PK': pk, 'SK': 'PERIOD#' + period_id})
            from . import purchase_usage
            if period is not None:
                purchase_usage.validate_period(period)
                if period['startEpoch'] != start or period['endEpoch'] != end:
                    raise AuthorityError('IMMUTABLE_PERIOD_CONFLICT')
            usage_key = purchase_usage.key(hashlib.sha256((decision.store+'\0'+decision.subscription_id).encode()).hexdigest(), hashlib.sha256(decision.period_id.encode()).hexdigest())
            observed_usage = self.a._get(self.a.s.authority_table, usage_key)
            if observed_usage is None and (period is not None or decision.require_existing_usage):
                raise AuthorityError('PURCHASE_USAGE_UNAVAILABLE')
            usage_actions, usage_seed = purchase_usage.funding_actions(self.a.s.authority_table, usage_key, start, end, observed_usage, now=now, access_until_epoch=access_end)
            extras.extend(usage_actions)
            if period:
                if (period.get('recordType') != 'V1_ALLOWANCE_PERIOD' or period.get('policyVersion') != OWNER_POLICY
                        or period.get('startEpoch') != start or period.get('endEpoch') != end
                        or period.get('limit') != PAID_COMPLETED_CHECKS or integral(period.get('grantRevision')) is None
                        or integral(period.get('usedChecks')) is None or integral(period.get('reservedChecks')) is None
                        or period['usedChecks'] < 0 or period['reservedChecks'] < 0):
                    raise AuthorityError('IMMUTABLE_PERIOD_CONFLICT')
                if period.get('purchaseUsageKey') != usage_key or any(period.get(k) != v for k,v in usage_seed.items()):
                    raise AuthorityError('PURCHASE_USAGE_MISMATCH')
                revision = int(period['grantRevision'])
                if purchase_usage.effective_access_end(period) != access_end:
                    updated_period = dict(period, accessUntilEpoch=access_end)
                    extras.append({'Put': {'TableName': self.a.s.authority_table, 'Item': updated_period, **purchase_usage.exact_condition(period)}})
                else:
                    extras.append({'ConditionCheck': {'TableName': self.a.s.authority_table, 'Key': {'PK': pk, 'SK': period['SK']}, **purchase_usage.exact_condition(period)}})
            else:
                # Temporary grace/deferral access may overlap a newly funded
                # period. Only immutable funded accounting dates must not overlap.
                if previous and previous.get('periodId'):
                    previous_period = self.a._get(self.a.s.authority_table, {'PK': pk, 'SK': 'PERIOD#'+previous['periodId']})
                    purchase_usage.validate_period(previous_period)
                    if (previous_period['grantRevision'] != previous['periodRevision']
                            or previous_period['startEpoch'] != previous['validFromEpoch']):
                        raise AuthorityError('IMMUTABLE_PERIOD_CONFLICT')
                    if previous_period['endEpoch'] > start:
                        raise AuthorityError('OVERLAPPING_STORE_PERIOD')
                    extras.append({'ConditionCheck': {'TableName': self.a.s.authority_table,
                        'Key': {'PK': pk, 'SK': previous_period['SK']}, **purchase_usage.exact_condition(previous_period)}})
                revision = (int(old['revision']) if old else 0) + 1
                if usage_seed['reservedChecks'] != 0:
                    raise AuthorityError('PURCHASE_USAGE_RECONCILIATION_REQUIRED')
                period_row = self._period(pk, period_id, revision, start, end, PAID_COMPLETED_CHECKS)
                period_row.update(usage_seed, purchaseUsageKey=usage_key, accessUntilEpoch=access_end)
                extras.append(self._put(period_row))
            source.update(validFromEpoch=start, validUntilEpoch=access_end, periodId=period_id, periodRevision=revision)
        elif previous:
            # Keep period identity/history while disabling new paid admission.
            source.update({k: v for k, v in previous.items() if k in ('periodId', 'periodRevision', 'validFromEpoch', 'validUntilEpoch')})
            if decision.access_until_epoch is not None and 'periodId' in previous:
                from . import purchase_usage
                access_end = decision.access_until_epoch
                if type(access_end) is not int or access_end < previous['validFromEpoch']:
                    raise AuthorityError('VERIFIED_STORE_AUTHORITY_UNAVAILABLE')
                period = self.a._get(self.a.s.authority_table, {'PK': pk, 'SK': 'PERIOD#'+previous['periodId']})
                pointer = purchase_usage.validate_period(period)
                if (period['grantRevision'] != previous['periodRevision']
                        or period['startEpoch'] != previous['validFromEpoch']):
                    raise AuthorityError('IMMUTABLE_PERIOD_CONFLICT')
                usage = self.a._get(self.a.s.authority_table, pointer)
                closed_pending = False
                if usage is None:
                    if now < purchase_usage.usage_deadline(period) or now < access_end+purchase_usage.RETENTION_SECONDS or period['reservedChecks'] != 0:
                        raise AuthorityError('PURCHASE_USAGE_RECONCILIATION_REQUIRED')
                    extras.append(self.a._check(self.a.s.authority_table,pointer,'attribute_not_exists(PK)'))
                else:
                    purchase_usage.validate(usage,pointer,now,allow_expired=True)
                    if any(usage[k] != period[k] for k in ('startEpoch','endEpoch','usedChecks','reservedChecks','limit')) or purchase_usage.effective_access_end(usage)!=purchase_usage.effective_access_end(period):
                        raise AuthorityError('PURCHASE_USAGE_MISMATCH')
                    if access_end + purchase_usage.RETENTION_SECONDS <= now and period['reservedChecks'] > 0:
                        extras.extend(purchase_usage.due_reservation_actions(self.a.ddb, self.a.s.authority_table,
                            period, usage, access_end, now=now))
                        closed_pending = True
                    else:
                        extras.extend(purchase_usage.access_window_actions(self.a.s.authority_table,pointer,usage,access_end,now=now))
                updated_period = dict(period, accessUntilEpoch=access_end)
                if closed_pending:
                    updated_period['reservedChecks'] = 0
                extras.append({'Put': {'TableName': self.a.s.authority_table, 'Item': updated_period, **purchase_usage.exact_condition(period)}})
                source['validUntilEpoch'] = access_end
        if previous and decision.source_revision == previous['sourceRevision'] and source != previous:
            raise AuthorityError('STORE_REVISION_CONFLICT')
        sources['paid'] = source
        details = source if request_digest is None else {'source': source, 'requestDigest': request_digest}
        return self._commit(account, pk, old, sources, operation_id, 'SYNC_PAID', details, extras=extras, request_digest=request_digest)

    def set_complimentary(self, operator, account, operation_id, *, enabled, reason_code, expires_at=None):
        if (type(operator) is not TrustedOperator or operator.principal_arn not in self.operators
                or not isinstance(operator.session_id, str) or not 1 <= len(operator.session_id) <= 256):
            raise AuthorityError('OPERATOR_AUTHORIZATION_REQUIRED')
        if (type(enabled) is not bool or reason_code not in ('OWNER_GRANT', 'SUPPORT_GRANT', 'REVOKE', 'CORRECTION')
                or (expires_at is not None and (type(expires_at) is not int or expires_at <= self.a.now()))
                or (not enabled and expires_at is not None)
                or (enabled and reason_code == 'REVOKE')
                or (not enabled and reason_code in ('OWNER_GRANT', 'SUPPORT_GRANT'))):
            raise AuthorityError('WRITER_INPUT_REJECTED')
        pk, old, sources = self._read(account)
        source = {'state': 'ACTIVE' if enabled else 'INACTIVE', 'validFromEpoch': self.a.now(), 'validUntilEpoch': expires_at}
        sources['complimentary'] = source
        actor = {'principal': operator.principal_arn,
                 'session': self.a._mac(self.a.s.active_key_id, 'operator-session', operator.session_id)}
        return self._commit(account, pk, old, sources, operation_id, 'SET_COMPLIMENTARY',
                            {'enabled': enabled, 'expiresAt': expires_at, 'reasonCode': reason_code}, actor=actor)

    def refresh_for_account(self, verified_event, operation_id):
        """Recompute expired overlay fallback; never extend/create underlying grants."""
        account = self.a._account(verified_event)
        pk, old, sources = self._read(account)
        if not old:
            raise AuthorityError('EXTERNAL_ACCESS_UNAVAILABLE')
        candidate = self._effective(pk, old, sources)
        if {k: v for k, v in candidate.items() if k != 'revision'} == {k: v for k, v in old.items() if k != 'revision'}:
            return {'revision': int(old['revision']), 'state': old['state'], 'basis': old.get('basis')}
        return self._commit(account, pk, old, sources, operation_id, 'REFRESH_ACCESS', {'atEpoch': self.a.now()})
