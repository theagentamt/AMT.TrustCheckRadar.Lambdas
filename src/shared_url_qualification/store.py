"""No URL/account/proof records. Conditional durable caps and match single flight.

Counters are conservative permits, never refunds. A crashed lock stays closed for
that target until run end; there is deliberately no lease takeover/retry ambiguity.
"""
import secrets
from math import ceil
from . import Closed
from shared_lookup_freshness import evidence, epoch, current

MAX_NEW = 10
MAX_ATTEMPTS = 60
RETENTION_SECONDS = 7 * 86400


class Store:
    def __init__(self, policy, resource):
        self.p, self.ddb = policy, resource
        self.table = resource.Table(policy.table)
        self.client = resource.meta.client
        if self.client.meta.config.retries.get('total_max_attempts') != 1:
            raise Closed('STORE_CLOSED')

    def healthy(self):
        self.p.active()
        try:
            row = self.table.get_item(Key={'PK': 'CONTROL', 'SK': 'CLEANUP'}, ConsistentRead=True).get('Item')
            stamp = row['lastSweepEpoch']
            if isinstance(stamp, bool) or int(stamp) != stamp or not self.p.now() - 900 <= stamp <= self.p.now():
                raise ValueError()
        except Exception:
            raise Closed('CLEANUP_CLOSED') from None

    def key(self, sort):
        return {'PK': 'RUN#' + self.p.run_id, 'SK': sort}

    def expiry(self, at):
        return {'expiresAt': at, 'GSI1PK': 'URL_QUALIFICATION_EXPIRING',
                'GSI1SK': f'{at:012d}#{self.p.run_id}'}

    def counter_action(self, field, limit):
        self.p.active()
        # First use creates one aggregate run row. Window identity prevents reuse
        # of a run id with changed clocks from silently resetting its cap.
        return {'Update': {'TableName': self.p.table, 'Key': self.key('BUDGET'),
            'UpdateExpression': 'SET manifestDigest = :digest, startEpoch = :start, endEpoch = :end, expiresAt = :expiry, GSI1PK = :index, GSI1SK = :sort ADD #count :one',
            'ConditionExpression': '(attribute_not_exists(#count) OR #count < :cap) AND (attribute_not_exists(startEpoch) OR (startEpoch = :start AND endEpoch = :end AND manifestDigest = :digest))',
            'ExpressionAttributeNames': {'#count': field},
            'ExpressionAttributeValues': {':one': 1, ':cap': limit, ':digest': self.p.manifest_digest, ':start': self.p.start, ':end': self.p.end,
                ':expiry': self.p.end + RETENTION_SECONDS, ':index': 'URL_QUALIFICATION_EXPIRING',
                ':sort': f'{self.p.end + RETENTION_SECONDS:012d}#{self.p.run_id}'}}}

    def admission_actions(self, url):
        self.p.require_admission(url)
        self.healthy()
        return [self.counter_action('newAdmissions', MAX_NEW)]

    def cached(self, target):
        try:
            row = self.table.get_item(Key=self.key('MATCH#' + target), ConsistentRead=True).get('Item')
            if not row:
                return None
            observation = evidence({k: row[k] for k in ('threatTypes', 'observedAt', 'validUntil')})
            if (row.get('manifestDigest') != self.p.manifest_digest or not observation['threatTypes'] or epoch(observation['validUntil']) - epoch(observation['observedAt']) > RETENTION_SECONDS
                    or row['expiresAt'] != ceil(epoch(observation['validUntil']))):
                raise Closed('CACHE_INVALID')
            if current(observation['observedAt'], observation['validUntil'], self.p.now()):
                return observation
            return None
        except Closed:
            raise
        except Exception:
            raise Closed('CACHE_CLOSED') from None

    def acquire(self, target):
        owner = secrets.token_hex(16)
        try:
            self.table.put_item(Item=self.key('LOCK#' + target) | {'owner': owner} | self.expiry(self.p.end),
                                ConditionExpression='attribute_not_exists(PK)')
            return owner
        except Exception:
            raise Closed('LOOKUP_BUSY') from None

    def release(self, target, owner):
        try:
            self.table.delete_item(Key=self.key('LOCK#' + target), ConditionExpression='#owner = :owner',
                                   ExpressionAttributeNames={'#owner': 'owner'}, ExpressionAttributeValues={':owner': owner})
        except Exception:
            # Ambiguous release stays closed; no retry, no caller-sensitive logs.
            pass

    def lookup(self, url, budget, call):
        target = self.p.require_provider(url)
        self.healthy()
        cached = self.cached(target)
        if cached is not None:
            return cached
        owner = self.acquire(target)
        release = False
        try:
            # Another worker may have published a match before we acquired.
            self.p.require_provider(url)
            self.healthy()
            cached = self.cached(target)
            if cached is not None:
                release = True
                return cached
            budget.remaining()
            try:
                self.client.transact_write_items(TransactItems=[self.counter_action('providerAttempts', MAX_ATTEMPTS)])
            except Exception:
                raise Closed('PROVIDER_CAP_CLOSED') from None
            # Count a permit even if DNS/TLS/timeout prevents the actual GET. A
            # lost transaction response cannot authorize a provider request.
            budget.remaining()
            self.p.require_provider(url)
            result = evidence(call())
            if result['threatTypes']:
                if not current(result['observedAt'], result['validUntil'], self.p.now()):
                    raise Closed('PROVIDER_VALIDITY_REJECTED')
                if epoch(result['validUntil']) - epoch(result['observedAt']) > RETENTION_SECONDS:
                    # Do not retain this oversized lifetime. Preserve the real
                    # match once, leaving the lock closed against refetch.
                    return result
                # Preserve exact provider clocks, never slide a hit's lifetime.
                row = self.key('MATCH#' + target) | result | {'manifestDigest': self.p.manifest_digest} | self.expiry(ceil(epoch(result['validUntil'])))
                try:
                    self.client.transact_write_items(TransactItems=[
                    {'ConditionCheck': {'TableName': self.p.table, 'Key': self.key('LOCK#' + target),
                        'ConditionExpression': '#owner = :owner', 'ExpressionAttributeNames': {'#owner': 'owner'},
                        'ExpressionAttributeValues': {':owner': owner}}},
                    {'Put': {'TableName': self.p.table, 'Item': row}}])
                except Exception:
                    # A confirmed match remains meaningful despite failed cache
                    # persistence. Retain the lock so it cannot cause refetch.
                    return result
            release = True
            return result
        finally:
            if release:
                self.release(target, owner)


def load_store(policy):
    import boto3
    from botocore.config import Config
    return Store(policy, boto3.resource('dynamodb', region_name='us-east-1',
                 config=Config(connect_timeout=0.2, read_timeout=0.3, retries={'total_max_attempts': 1})))
