"""Bounded read-only traversal; every capability remains authenticated and fenced."""
import time
import uuid
from .cursor import ExportError, encode, require, unique_pairs
import json

VERSION = '1.0.0-account-export-candidate.2'
MANIFEST_VERSION = 'v1-user-visible-2026-09-21'
MAX_PAGE_BYTES = 65536
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_PAGES = 256
WINDOW_SECONDS = 900
SCOPE = {
    'version': MANIFEST_VERSION,
    'included': ['profile', 'identity', 'devices', 'recovery', 'subscriptions',
                 'usage', 'purchases', 'access', 'allowance', 'trial', 'receipts',
                 'history', 'recognition', 'participation', 'consent',
                 'analysis_requests','scan_consumption','research_observations','research_contributions'],
    'excluded': ['original_submissions', 'security_and_internal_controls',
                 'research_pipeline_internals', 'other_peoples_data',
                 'non_user_linkable_aggregates', 'historical_backups',
                 'transient_streams_and_queues', 'operational_logs',
                 'external_provider_and_store_records'],
    'consistency': 'OBSERVED_VALUES',
    'cancellation': 'LOCAL_DOWNLOAD_ONLY',
}


def parse(event):
    require(type(event) is dict and event.get('version') == '2.0'
            and event.get('routeKey') == 'POST /v1/users/account-export')
    require(event.get('rawQueryString') in (None, '') and event.get('queryStringParameters') in (None, {}))
    require(event.get('isBase64Encoded') is None or event.get('isBase64Encoded') is False)
    raw = event.get('body')
    require(type(raw) is str)
    try:
        require(len(raw.encode('utf-8')) <= 12288)
        body = json.loads(raw, object_pairs_hook=unique_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ExportError('INVALID_REQUEST') from None
    require(type(body) is dict and type(body.get('schemaVersion')) is int and body['schemaVersion'] == 1)
    if body.get('action') == 'START_EXPORT':
        require(set(body) == {'schemaVersion', 'action'})
    else:
        require(body.get('action') == 'CONTINUE_EXPORT' and set(body) == {'schemaVersion', 'action', 'cursor'})
        require(type(body['cursor']) is str and 40 <= len(body['cursor']) <= 8192)
    return body


class Export:
    def __init__(self, reader, cursor, now=lambda: int(time.time())):
        self.reader, self.cursor, self.now = reader, cursor, now

    def page(self, event, body):
        # auth() checks JWT, recent signed auth_time, exact current device pointer,
        # active profile and deletion fence. It performs no entitlement lookup.
        context = self.reader.auth(event)
        now = self.now()
        if body['action'] == 'START_EXPORT':
            token = {'version': VERSION, 'manifest': MANIFEST_VERSION,
                     'account': context['account'], 'device': context['device'],
                     'bindingVersion': context['bindingVersion'],
                     'operationId': str(uuid.uuid4()), 'startedAtEpoch': now,
                     'expiresAtEpoch': now + WINDOW_SECONDS, 'pageNumber': 0,
                     'familyIndex': 0, 'position': None, 'totalBytes': 0,
                     'inventory': self.reader.inventory(context)}
        else:
            token = self.cursor.open(body['cursor'])
        self._validate(token, context, now)
        plan = self.reader.plan(context, token['inventory'])
        index = token['familyIndex']
        require(index < len(plan), 'INVALID_CURSOR')
        family, items, position = self.reader.read(context, plan[index], token['position'], token['startedAtEpoch'])
        require(type(items) is list and len(items) <= 25, 'SERVICE_UNAVAILABLE', 503)
        next_index = index if position is not None else index + 1
        complete = next_index == len(plan)
        response = {'schemaVersion': 1, 'exportTransportVersion': VERSION,
                    'operation': 'ACCOUNT_EXPORT', 'operationId': token['operationId'],
                    'status': 'COMPLETE' if complete else 'IN_PROGRESS',
                    'startedAtEpoch': token['startedAtEpoch'], 'expiresAtEpoch': token['expiresAtEpoch'],
                    'observedAtEpoch': self.now(), 'pageNumber': token['pageNumber'],
                    'family': family, 'items': items, 'nextCursor': None, 'scope': SCOPE}
        # Include the next capability in the wire byte budget. Fixed point is
        # bounded because decimal byte counters can change token length slightly.
        if not complete:
            next_token = {**token, 'pageNumber': token['pageNumber'] + 1,
                          'familyIndex': next_index, 'position': position}
            require(next_token['pageNumber'] < MAX_PAGES, 'EXPORT_LIMIT_EXCEEDED', 413)
            for _ in range(4):
                response['nextCursor'] = self.cursor.seal(next_token)
                total = token['totalBytes'] + len(encode(response))
                if next_token['totalBytes'] == total:
                    break
                next_token['totalBytes'] = total
            else:
                raise ExportError('SERVICE_UNAVAILABLE', 503)
        size = len(encode(response))
        require(size <= MAX_PAGE_BYTES and token['totalBytes'] + size <= MAX_TOTAL_BYTES,
                'EXPORT_LIMIT_EXCEEDED', 413)
        after = self.reader.auth(event)
        require(after == context, 'ACCESS_CHANGED', 409)
        self.reader.assert_inventory(context, token['inventory'])
        require(self.now() < token['expiresAtEpoch'], 'EXPORT_EXPIRED', 410)
        return response

    def _validate(self, token, context, now):
        require(set(token) == {'version', 'manifest', 'account', 'device', 'bindingVersion',
                              'operationId', 'startedAtEpoch', 'expiresAtEpoch', 'pageNumber',
                              'familyIndex', 'position', 'totalBytes', 'inventory'}, 'INVALID_CURSOR')
        require(token['version'] == VERSION and token['manifest'] == MANIFEST_VERSION
                and token['account'] == context['account'] and token['device'] == context['device']
                and token['bindingVersion'] == context['bindingVersion'], 'INVALID_CURSOR')
        for field in ('bindingVersion', 'startedAtEpoch', 'expiresAtEpoch', 'pageNumber', 'familyIndex', 'totalBytes'):
            require(type(token[field]) is int and token[field] >= 0, 'INVALID_CURSOR')
        require(token['startedAtEpoch'] <= now and token['expiresAtEpoch'] == token['startedAtEpoch'] + WINDOW_SECONDS,
                'INVALID_CURSOR')
        require(now < token['expiresAtEpoch'], 'EXPORT_EXPIRED', 410)
        require(token['pageNumber'] < MAX_PAGES and token['totalBytes'] <= MAX_TOTAL_BYTES, 'EXPORT_LIMIT_EXCEEDED', 413)
        try:
            require(str(uuid.UUID(token['operationId'], version=4)) == token['operationId'], 'INVALID_CURSOR')
        except (ValueError, TypeError, AttributeError):
            raise ExportError('INVALID_CURSOR') from None
