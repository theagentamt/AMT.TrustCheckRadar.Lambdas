"""Bounded best-effort repair. An exhausted eventual index is never erasure proof."""
from decimal import Decimal
from uuid import UUID
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

SERIALIZER, DESERIALIZER = TypeSerializer(), TypeDeserializer()
PAGE_SIZE = 25
MAX_STEPS = 10


from cleanup_errors import CoverageUnavailable


def wire(value):
    return {k: SERIALIZER.serialize(v) for k, v in value.items()}


def plain(value):
    return {k: DESERIALIZER.deserialize(v) for k, v in value.items()}


def key(pk, sk):
    return wire({'PK': pk, 'SK': sk})


def get(ddb, table, pk, sk):
    raw = ddb.get_item(TableName=table, Key=key(pk, sk), ConsistentRead=True).get('Item')
    return plain(raw) if raw else None


def require(condition):
    if not condition:
        raise CoverageUnavailable()


def integer(value, minimum=0):
    require(type(value) in (int, Decimal) and value >= minimum and value == int(value))
    return int(value)


def condition(table, pk, sk, version, field='version'):
    return {'ConditionCheck': {'TableName': table, 'Key': key(pk, sk),
        'ConditionExpression': '#v = :v', 'ExpressionAttributeNames': {'#v': field},
        'ExpressionAttributeValues': wire({':v': version})}}


def index_cursor(value, partition):
    if value is None:
        return None
    require(type(value) is dict and set(value) == {'PK', 'SK', 'GSI1PK', 'GSI1SK'}
            and value['GSI1PK'] == partition and all(type(v) is str and 0 < len(v) <= 2048 for v in value.values()))
    target(value, partition)
    return value


def target(value, partition):
    pk, sk = value.get('PK'), value.get('SK')
    token = partition.split('#', 2)[2]
    require(value.get('GSI1PK') == partition and type(pk) is str and type(sk) is str
            and ((pk.startswith('EVENT#') and sk == 'FEATURE')
                 or (pk.startswith('CANDIDATE#') and sk == 'CONTRIB#' + token)))
    return pk, sk


def _write_state(table, tombstone, state):
    revision = integer(tombstone.get('cleanupRevision', 0))
    values = {':next': revision + 1, ':state': state, ':expiry': tombstone['expiresAt']}
    expression = 'expiresAt = :expiry AND '
    if revision:
        expression += 'cleanupRevision = :previous'
        values[':previous'] = revision
    else:
        expression += 'attribute_not_exists(cleanupRevision)'
    return {'Update': {'TableName': table, 'Key': key(tombstone['PK'], 'TOMBSTONE'),
        'UpdateExpression': 'SET cleanupRevision = :next, cleanupState = :state',
        'ConditionExpression': expression, 'ExpressionAttributeValues': wire(values)}}


def _advance(ddb, table, tombstone, state, extra=()):
    ddb.transact_write_items(TransactItems=[*extra, _write_state(table, tombstone, state)])



class CommandGuardedClient:
    """Every cleanup write shares the exact durable command fence atomically."""
    def __init__(self, client, ledger_table, command):
        self.client = client
        fields = [(name,value) for name,value in command.items() if name not in ('PK','SK')]
        names = {f'#c{i}':name for i,(name,_) in enumerate(fields)}
        values = {f':c{i}':value for i,(_,value) in enumerate(fields)}
        self.guard = {'ConditionCheck':{'TableName':ledger_table,'Key':key(command['PK'],command['SK']),
            'ConditionExpression':' AND '.join(f'#c{i} = :c{i}' for i in range(len(fields))),
            'ExpressionAttributeNames':names,'ExpressionAttributeValues':wire(values)}}

    def __getattr__(self, name):
        return getattr(self.client,name)

    def transact_write_items(self, **kwargs):
        return self.client.transact_write_items(**(kwargs | {'TransactItems':[self.guard,*kwargs['TransactItems']]}))

    def update_item(self, **kwargs):
        return self.transact_write_items(TransactItems=[{'Update':kwargs}])

def sweep(ddb, table, partition, operation_id, now, *, max_steps=MAX_STEPS, remaining_ms=None):
    """Persist before destructive work; retries resume a pending repair before advancing."""
    require(type(max_steps) is int and 1 <= max_steps <= 20)
    deleted = recomputed = 0
    for _ in range(max_steps):
        if remaining_ms is not None and remaining_ms() < 3000:
            break
        tombstone = get(ddb, table, partition, 'TOMBSTONE')
        require(tombstone and integer(tombstone.get('expiresAt')) > now)
        state = tombstone.get('cleanupState')
        if state is None:
            state = {'operationId': operation_id, 'phase': 'SEEK', 'cursor': None}
            _advance(ddb, table, tombstone, state)
            continue
        require(type(state) is dict)
        phase = state.get('phase')
        expected = {'operationId','phase','cursor'} if phase in ('SEEK','OBSERVED_END') else {'operationId','phase','cursor','targetPK','targetSK','nextCursor'}
        require(phase in ('SEEK','DELETE','RECOMPUTE','OBSERVED_END') and set(state) == expected)
        try:
            parsed = UUID(state['operationId'])
            require(parsed.version == 4 and str(parsed) == state['operationId'])
        except (ValueError, TypeError, AttributeError):
            raise CoverageUnavailable() from None
        index_cursor(state['cursor'], partition)
        if phase in ('DELETE','RECOMPUTE'):
            target({'PK':state['targetPK'],'SK':state['targetSK'],'GSI1PK':partition},partition)
            index_cursor(state['nextCursor'],partition)
        if state['operationId'] != operation_id:
            # Withdrawal and account deletion share the same contributor. Adopt
            # the bounded repair under this command's transaction fence; never
            # reset a deleted contribution's still-pending recomputation.
            _advance(ddb,table,tombstone,state | {'operationId':operation_id})
            continue
        if phase == 'SEEK':
            require(set(state) == {'operationId', 'phase', 'cursor'})
            cursor = index_cursor(state['cursor'], partition)
            args = dict(TableName=table, IndexName='ContributorPeriodIndex',
                KeyConditionExpression='GSI1PK = :pk', ExpressionAttributeValues=wire({':pk': partition}),
                Limit=1, ScanIndexForward=True)
            if cursor:
                args['ExclusiveStartKey'] = wire(cursor)
            page = ddb.query(**args)
            rows = [plain(v) for v in page.get('Items', [])]
            require(len(rows) <= 1)
            next_cursor = index_cursor(plain(page['LastEvaluatedKey']) if page.get('LastEvaluatedKey') else None, partition)
            if not rows:
                # Empty pages with a continuation are not the end of a pass.
                new = {'operationId': operation_id, 'phase': 'SEEK' if next_cursor else 'OBSERVED_END', 'cursor': next_cursor}
            else:
                pk, sk = target(rows[0], partition)
                new = {'operationId': operation_id, 'phase': 'DELETE', 'cursor': cursor,
                       'targetPK': pk, 'targetSK': sk, 'nextCursor': next_cursor}
            _advance(ddb, table, tombstone, new)
        elif phase == 'DELETE':
            require(set(state) == {'operationId','phase','cursor','targetPK','targetSK','nextCursor'})
            pk, sk = target({'PK':state['targetPK'],'SK':state['targetSK'],'GSI1PK':partition}, partition)
            index_cursor(state['cursor'], partition); index_cursor(state['nextCursor'], partition)
            existing = get(ddb, table, pk, sk)
            if existing:
                target(existing, partition)
            actions = [{'Delete': {'TableName': table, 'Key': key(pk, sk),
                        'ConditionExpression': 'attribute_not_exists(PK) OR GSI1PK = :pk',
                        'ExpressionAttributeValues': wire({':pk': partition})}}]
            new = dict(state)
            if pk.startswith('CANDIDATE#'):
                summary = get(ddb, table, pk, 'SUMMARY')
                if summary:
                    version = integer(summary.get('version'), 1)
                    # Every repair deletion invalidates an in-progress aggregate pass.
                    actions.append({'Update': {'TableName':table,'Key':key(pk,'SUMMARY'),
                        'UpdateExpression':'SET #v = :next','ConditionExpression':'#v = :v',
                        'ExpressionAttributeNames':{'#v':'version'},
                        'ExpressionAttributeValues':wire({':v':version,':next':version+1})}})
                new['phase'] = 'RECOMPUTE'
            else:
                actions.extend({'Delete':{'TableName':table,'Key':key(pk,sibling)}} for sibling in ('DEDUPE','CLUSTERED'))
                new = _next_state(state)
            _advance(ddb, table, tombstone, new, actions)
            deleted += len(actions) - (1 if pk.startswith('CANDIDATE#') and summary else 0)
        elif phase == 'RECOMPUTE':
            require(set(state) == {'operationId','phase','cursor','targetPK','targetSK','nextCursor'})
            pk, _ = target({'PK':state['targetPK'],'SK':state['targetSK'],'GSI1PK':partition}, partition)
            require(pk.startswith('CANDIDATE#'))
            if recompute_page(ddb, table, pk, now):
                _advance(ddb, table, tombstone, _next_state(state))
                recomputed += 1
        elif phase == 'OBSERVED_END':
            require(set(state) == {'operationId','phase','cursor'} and state['cursor'] is None)
            # Revisit from the beginning on the next invocation: delayed GSI rows
            # may have appeared behind the previous cursor. This is not proof.
            _advance(ddb, table, tombstone, {'operationId':operation_id,'phase':'SEEK','cursor':None})
            return {'deleted':deleted,'recomputedCandidates':recomputed,'observedPassEnded':True}
        else:
            raise CoverageUnavailable()
    return {'deleted':deleted,'recomputedCandidates':recomputed,'observedPassEnded':False}


def _next_state(state):
    return {'operationId':state['operationId'],
            'phase':'SEEK' if state['nextCursor'] else 'OBSERVED_END','cursor':state['nextCursor']}


def recompute_page(ddb, table, pk, now):
    """Publish only after every page, using the original summary version as a fence."""
    summary = get(ddb, table, pk, 'SUMMARY')
    if summary is None:
        return True
    version, expiry = integer(summary.get('version'),1), integer(summary.get('expiresAt'),1)
    require(expiry > now)
    saved = get(ddb,table,pk,'DELETION_RECOMPUTE')
    if saved is None or saved.get('summaryVersion') != version:
        state = {'PK':pk,'SK':'DELETION_RECOMPUTE','revision':1 if saved is None else integer(saved.get('revision'),1)+1,
                 'summaryVersion':version,'cursor':None,'contributors':0,'submissions':0,'vectors':0,'sums':[],
                 'cutoffEpoch':now,'expiresAt':expiry,'GSI3PK':summary['GSI3PK'],'GSI3SK':expiry}
        put = {'TableName':table,'Item':wire(state)}
        if saved is None:
            put['ConditionExpression']='attribute_not_exists(PK)'
        else:
            put.update(ConditionExpression='revision = :r',ExpressionAttributeValues=wire({':r':saved['revision']}))
        ddb.transact_write_items(TransactItems=[condition(table,pk,'SUMMARY',version),{'Put':put}])
        return False
    expected = {'PK','SK','revision','summaryVersion','cursor','contributors','submissions','vectors','sums','cutoffEpoch','expiresAt','GSI3PK','GSI3SK'}
    require(set(saved) == expected and saved['expiresAt'] == expiry and saved['GSI3PK'] == summary['GSI3PK'] and saved['GSI3SK'] == expiry)
    for field in ('revision','contributors','submissions','vectors','cutoffEpoch'):
        integer(saved[field])
    require(saved['cutoffEpoch'] <= now and type(saved['sums']) is list and len(saved['sums']) <= 384)
    require(all(type(v) in (int,Decimal) and Decimal(v).is_finite() for v in saved['sums']))
    cursor = saved['cursor']
    require(cursor is None or (type(cursor) is str and cursor.startswith('CONTRIB#') and len(cursor) <= 2048))
    args = dict(TableName=table, KeyConditionExpression='PK = :pk AND begins_with(SK, :prefix)',
                ExpressionAttributeValues=wire({':pk':pk,':prefix':'CONTRIB#'}),ConsistentRead=True,Limit=PAGE_SIZE)
    if cursor:
        args['ExclusiveStartKey'] = key(pk,cursor)
    page = ddb.query(**args)
    state = dict(saved); state['sums'] = list(saved['sums']); state['revision'] += 1
    rows = [plain(v) for v in page.get('Items',[])]
    require(len(rows) <= PAGE_SIZE)
    for row in rows:
        require(row.get('PK') == pk and type(row.get('SK')) is str and row['SK'].startswith('CONTRIB#'))
        if integer(row.get('expiresAt'),1) <= saved['cutoffEpoch']:
            continue
        state['contributors'] += 1
        state['submissions'] += min(3,integer(row.get('submissionCount'),1))
        require(type(row.get('vectorApplied')) is bool)
        if row['vectorApplied']:
            vector = row.get('vector')
            require(type(vector) is list and 1 <= len(vector) <= 384
                    and all(type(v) in (int,Decimal) and Decimal(v).is_finite() and abs(v) <= 1 for v in vector))
            if not state['sums']:
                state['sums'] = [Decimal(0)] * len(vector)
            require(len(vector) == len(state['sums']))
            state['sums'] = [a+b for a,b in zip(state['sums'],vector)]
            state['vectors'] += 1
    last = plain(page['LastEvaluatedKey']) if page.get('LastEvaluatedKey') else None
    if last:
        require(set(last) == {'PK','SK'} and last['PK'] == pk and type(last['SK']) is str and last['SK'].startswith('CONTRIB#'))
        state['cursor'] = last['SK']
        ddb.transact_write_items(TransactItems=[condition(table,pk,'SUMMARY',version),{'Put':{
            'TableName':table,'Item':wire(state),'ConditionExpression':'revision = :r',
            'ExpressionAttributeValues':wire({':r':saved['revision']})}}])
        return False
    # A candidate with no usable vectors cannot have a trustworthy centroid.
    require(state['contributors'] == 0 or state['vectors'] > 0)
    if state['contributors'] == 0:
        action = {'Delete':{'TableName':table,'Key':key(pk,'SUMMARY'),'ConditionExpression':'#v = :v',
                           'ExpressionAttributeNames':{'#v':'version'},'ExpressionAttributeValues':wire({':v':version})}}
    else:
        centroid = [(v / state['vectors']).quantize(Decimal('.0000001')) for v in state['sums']]
        action = {'Update':{'TableName':table,'Key':key(pk,'SUMMARY'),
            'UpdateExpression':'SET centroid = :centroid, contributorCount = :count, submissionCount = :submissions, #v = :next',
            'ConditionExpression':'#v = :v','ExpressionAttributeNames':{'#v':'version'},
            'ExpressionAttributeValues':wire({':v':version,':next':version+1,':centroid':centroid,
                                               ':count':state['contributors'],':submissions':state['submissions']})}}
    ddb.transact_write_items(TransactItems=[action,{'Delete':{'TableName':table,'Key':key(pk,'DELETION_RECOMPUTE'),
        'ConditionExpression':'revision = :r','ExpressionAttributeValues':wire({':r':saved['revision']})}}])
    return True
