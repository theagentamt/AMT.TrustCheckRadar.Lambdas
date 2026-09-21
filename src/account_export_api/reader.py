"""Exact account-owned read paths; no Scan, mutation or external provider call."""
import hashlib
import base64
import re
import hmac
from datetime import datetime, timezone
from .cursor import require, encode, ExportError
from .projection import project, integer, text, pick
from shared_check_authority.inventory import verified_inventory


class Reader:
    def __init__(self, authority, tables, cognito, user_pool_id, purchase_reader=None, kms=None):
        self.a, self.tables, self.cognito, self.pool = authority, tables, cognito, user_pool_id
        self.purchase_reader, self.kms = purchase_reader, kms

    def auth(self, event):
        account = self.a._account(event)
        device, version = self.a._device(event, account)
        claims = event['requestContext']['authorizer']['jwt']['claims']
        def epoch(value):
            return int(value) if type(value) is str and value.isdigit() else value
        auth, issued = epoch(claims.get('auth_time')), epoch(claims.get('iat'))
        now = self.a.now()
        require(type(auth) is int and type(issued) is int and 0 < auth <= issued <= now + 60
                and now - 300 <= auth <= now + 60, 'REAUTHENTICATION_REQUIRED', 401)
        return {'account':account,'device':device,'bindingVersion':int(version)}

    def _table(self, name):
        return self.a.ddb.Table(self.tables[name])

    def _get(self, table, pk, sk):
        return self._table(table).get_item(Key={'PK':pk,'SK':sk}, ConsistentRead=True).get('Item')

    def inventory(self, context):
        require(self.purchase_reader is not None,'SOURCE_UNAVAILABLE',503)
        purchase_inventory = self.purchase_reader.inventory()
        inventory = verified_inventory(self.a.ddb, self.a.s.authority_table, self.a.s.hmac_keys)
        history = self._get('history_control', 'USER#'+context['account'], 'STATE')
        state = None
        if history is not None:
            require(history.get('accountStatus') == 'ACTIVE', 'SOURCE_UNAVAILABLE', 503)
            state = {'historyGeneration':integer(history.get('historyGeneration')),
                     'recognitionGeneration':integer(history.get('recognitionGeneration'))}
        current = self.a.now() // (14 * 86400)
        periods = [current]
        if current > 0 and self.a.now() - current * 14 * 86400 < 7 * 86400:
            periods.append(current - 1)
        campaign = {}
        for period in periods:
            row = self._get('pipeline', 'PERIOD#'+str(period), 'HMAC_KEY')
            require(row is not None and row.get('PK') == 'PERIOD#'+str(period) and row.get('SK') == 'HMAC_KEY'
                    and integer(row.get('periodId')) == period and integer(row.get('retireAfterEpoch')) == (period+1)*14*86400+7*86400
                    and row.get('status') == 'ENABLED'
                    and type(row.get('keyArn')) is str
                    and re.fullmatch(r'arn:aws:kms:us-east-1:107827791950:key/[a-f0-9-]{36}', row['keyArn']),
                    'SOURCE_UNAVAILABLE',503)
            campaign[str(period)] = row['keyArn']
        return {'authorityRevision':int(inventory['revision']),
                'keys':sorted(self.a.s.hmac_keys),'history':state,'campaign':campaign,
                'purchaseRevision':int(purchase_inventory['revision'])}

    def assert_inventory(self, context, inventory):
        require(self.inventory(context) == inventory, 'SOURCE_CHANGED', 409)

    def plan(self, context, inventory):
        self.assert_inventory(context, inventory)
        pk = 'USER#'+context['account']
        plan = [('profile','users',pk,'PROFILE',True), ('identity',None,None,None,True),
                ('devices','devices',pk,'DEVICE#',False), ('recovery','recovery',pk,'RECOVERY#',False),
                ('subscriptions','entitlements',pk,'ENTITLEMENT',False), ('usage','entitlements',pk,'USAGE#',False),
                ('purchases','entitlements',pk,'PURCHASE_TOKEN#',False)]
        for kid in inventory['keys']:
            part = self.a._partition(context['account'],kid)
            plan += [(name,'authority',part,sort,single) for name,sort,single in
                     [('access','ACCESS',True),('allowance','PERIOD#',False),('trial','TRIAL_HISTORY',True),('receipts','CHECK#',False)]]
        history = inventory['history']
        if history:
            plan += [('history','history_content',pk+'#HISTORY#'+str(history['historyGeneration']),'COMPLETE#',False),
                     ('recognition','history_control',pk,'PROGRESS#'+str(history['recognitionGeneration']),True)]
        else:
            plan += [('history',None,None,None,True),('recognition',None,None,None,True)]
        account_hash = hashlib.sha256(context['account'].encode()).hexdigest()
        plan += [('analysis_requests','abuse','ANALYSIS#REQUEST#'+account_hash,'',False),
                 ('scan_consumption','abuse','ANALYSIS#CONSUMPTION#'+account_hash,'',False),
                 ('research_observations','outbox','ACCOUNT#'+account_hash,'OUTBOX#',False)]
        for period, arn in sorted(inventory['campaign'].items()):
            plan.append(('research_contributions','pipeline',period,arn,False))
        plan += [('participation','users',pk,'CAMPAIGN_PARTICIPATION',True),
                 ('consent','users',pk,'CAMPAIGN_CONSENT#',False)]
        return plan

    def read(self, context, entry, position, cutoff):
        family, table, pk, sort, single = entry
        if family == 'research_contributions':
            return self._research(context, pk, sort, position)
        if family == 'identity':
            require(position is None, 'INVALID_CURSOR')
            response = self.cognito.admin_get_user(UserPoolId=self.pool,Username=context['account'])
            attrs = response.get('UserAttributes')
            require(type(attrs) is list and response.get('Username') == context['account'], 'SOURCE_UNAVAILABLE', 503)
            values = {}
            allowed = {'email','email_verified','given_name','family_name','phone_number','phone_number_verified','custom:over_18','sub'}
            for attr in attrs:
                require(type(attr) is dict and type(attr.get('Name')) is str, 'SOURCE_UNAVAILABLE', 503)
                key = attr['Name']
                if key in allowed:
                    require(key not in values, 'SOURCE_UNAVAILABLE', 503)
                    values[key] = text(attr.get('Value'))
            require(values.pop('sub',None) == context['account'], 'SOURCE_UNAVAILABLE', 503)
            return family,[values],None
        if table is None:
            require(position is None, 'INVALID_CURSOR')
            return family,[],None
        if family == 'purchases':
            require(self.purchase_reader is not None, 'SOURCE_UNAVAILABLE', 503)
            page = self.purchase_reader.owned_page(context['account'],cursor=position,limit=20)
            require(type(page.get('records')) is list and len(page['records']) <= 20,'SOURCE_UNAVAILABLE',503)
            for row in page['records']:
                require(type(row) is dict and set(row) == {'platform','productId','verifiedAtEpoch'},'SOURCE_UNAVAILABLE',503)
            return family,[{k:integer(v) if k == 'verifiedAtEpoch' else text(v) for k,v in row.items()}
                           for row in page['records']],page['cursor']
        if single:
            require(position is None, 'INVALID_CURSOR')
            row = self._get(table,pk,sort)
            rows = [] if row is None else [row]
            continuation = None
        else:
            args = {'KeyConditionExpression':'PK = :pk' + (' AND begins_with(SK, :prefix)' if sort else ''),
                    'ExpressionAttributeValues':{':pk':pk, **({':prefix':sort} if sort else {})},
                    'ConsistentRead':True,'Limit':25,'ScanIndexForward':True}
            if position is not None:
                require(type(position) is str and position.startswith(sort) and len(position.encode()) <= 2048, 'INVALID_CURSOR')
                args['ExclusiveStartKey'] = {'PK':pk,'SK':position}
            page = self._table(table).query(**args)
            rows = page.get('Items',[])
            key = page.get('LastEvaluatedKey')
            if key is not None:
                require(type(key) is dict and set(key) == {'PK','SK'} and key['PK'] == pk
                        and type(key['SK']) is str and key['SK'].startswith(sort), 'SOURCE_UNAVAILABLE', 503)
            continuation = key['SK'] if key else None
        require(type(rows) is list and len(rows) <= 25, 'SOURCE_UNAVAILABLE', 503)
        result, size, last = [], 0, position
        for row in rows:
            require(type(row) is dict and row.get('PK') == pk and type(row.get('SK')) is str
                    and (row['SK'] == sort if single else row['SK'].startswith(sort)), 'SOURCE_UNAVAILABLE', 503)
            if family == 'receipts':
                require(row.get('SK') == 'CHECK#'+str(row.get('checkId')), 'SOURCE_UNAVAILABLE',503)
                self.a._verify_token(context['account'],row.get('checkId'),row.get('payloadHmac'),allow_expired=True)
            value = self._observation(context,row) if family == 'research_observations' else project(row, family, self.a.now())
            if value is not None:
                item_size = len(encode(value)) + 1
                if size + item_size > 48000:
                    require(bool(result) and last is not None and not single, 'EXPORT_LIMIT_EXCEEDED', 413)
                    return family,result,last
                result.append(value); size += item_size
            last = row['SK']
        return family,result,continuation

    def _observation(self, context, locator):
        account_hash = hashlib.sha256(context['account'].encode()).hexdigest()
        event = locator.get('statisticsEventId')
        require(locator.get('recordType') == 'CAMPAIGN_OUTBOX_LOCATOR'
                and locator.get('accountIdHash') == account_hash
                and locator.get('environment') == 'dev' and type(event) is str
                and locator.get('SK') == 'OUTBOX#'+event
                and locator.get('eventPK') == 'EVENT#'+event
                and locator.get('eventSK') == 'OBSERVATION_READY', 'SOURCE_UNAVAILABLE',503)
        if integer(locator.get('eventExpiresAt')) <= self.a.now():
            return None
        row = self._get('outbox','EVENT#'+event,'OBSERVATION_READY')
        require(row is not None and row.get('accountId') == context['account']
                and row.get('statisticsEventId') == event and row.get('environment') == 'dev'
                and row.get('PK') == 'EVENT#'+event and row.get('SK') == 'OBSERVATION_READY',
                'SOURCE_UNAVAILABLE',503)
        return project(row,'research_observations',self.a.now())

    def _research(self, context, period, key_arn, position):
        require(self.kms is not None, 'SOURCE_UNAVAILABLE',503)
        mac = self.kms.generate_mac(KeyId=key_arn,
            Message=b'campaign-contributor:v1\0'+context['account'].encode(),MacAlgorithm='HMAC_SHA_256')['Mac']
        require(type(mac) is bytes and len(mac) == 32,'SOURCE_UNAVAILABLE',503)
        token = base64.urlsafe_b64encode(mac).decode().rstrip('=')
        partition = 'CONTRIB#'+period+'#'+token
        table = self._table('pipeline')
        args = {'IndexName':'ContributorPeriodIndex','KeyConditionExpression':'GSI1PK = :pk',
                'ExpressionAttributeValues':{':pk':partition},'Limit':25,'ScanIndexForward':True}
        if position is not None:
            require(type(position) is dict and set(position) == {'PK','SK','GSI1PK','GSI1SK'}
                    and position['GSI1PK'] == partition and all(type(v) is str and len(v) <= 2048 for v in position.values()),
                    'INVALID_CURSOR')
            args['ExclusiveStartKey'] = position
        page = table.query(**args)
        rows = page.get('Items',[])
        require(type(rows) is list and len(rows) <= 25,'SOURCE_UNAVAILABLE',503)
        records = []
        for found in rows:
            require(type(found) is dict and found.get('GSI1PK') == partition,'SOURCE_UNAVAILABLE',503)
            pk,sk = found.get('PK'),found.get('SK')
            require(type(pk) is str and type(sk) is str
                    and ((pk.startswith('EVENT#') and sk == 'FEATURE') or
                         (pk.startswith('CANDIDATE#') and sk == 'CONTRIB#'+token)), 'SOURCE_UNAVAILABLE',503)
            row = self._get('pipeline',pk,sk)
            # A stale index entry is not exported; a concurrent erase may remove it.
            if row is None:
                continue
            require(row.get('GSI1PK') == partition and integer(row.get('periodId')) == int(period), 'SOURCE_UNAVAILABLE',503)
            if sk == 'FEATURE':
                require(row.get('contributorToken') == token and row.get('environment') == 'dev','SOURCE_UNAVAILABLE',503)
            result = project(row,'research_contributions',self.a.now())
            if result is not None:
                records.append(result)
        key = page.get('LastEvaluatedKey')
        if key is not None:
            require(type(key) is dict and set(key) == {'PK','SK','GSI1PK','GSI1SK'}
                    and key.get('GSI1PK') == partition and all(type(v) is str and len(v) <= 2048 for v in key.values()),
                    'SOURCE_UNAVAILABLE',503)
        return 'research_contributions',records,key
