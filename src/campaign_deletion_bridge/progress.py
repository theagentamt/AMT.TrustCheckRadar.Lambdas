"""Shared durable-command guards and bounded candidate recomputation."""
from decimal import Decimal
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

SERIALIZER, DESERIALIZER = TypeSerializer(), TypeDeserializer()
PAGE_SIZE = 25
MAX_STEPS = 10


from cleanup_errors import CoverageUnavailable
from shared_campaign_contracts.metadata import validate_metadata, merge_metadata, empty_metadata


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
        'ConditionExpression': '#v = :v AND attribute_not_exists(lifecycleState)', 'ExpressionAttributeNames': {'#v': field},
        'ExpressionAttributeValues': wire({':v': version})}}


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

def recompute_page(ddb, table, pk, now):
    """Publish only after every page, using the original summary version as a fence."""
    summary = get(ddb, table, pk, 'SUMMARY')
    if summary is None:
        return True
    require('lifecycleState' not in summary)
    version, expiry = integer(summary.get('version'),1), integer(summary.get('expiresAt'),1)
    require(expiry > now)
    saved = get(ddb,table,pk,'DELETION_RECOMPUTE')
    if saved is not None:
        validate_repair(saved, summary, now)
    if saved is None or saved.get('summaryVersion') != version:
        state = {'PK':pk,'SK':'DELETION_RECOMPUTE','revision':1 if saved is None else integer(saved.get('revision'),1)+1,
                 'summaryVersion':version,'cursor':None,'contributors':0,'submissions':0,'vectors':0,'sums':[],
                 'metadataSchemaVersion':1, **empty_metadata(),
                 'cutoffEpoch':now,'expiresAt':expiry,'GSI3PK':summary['GSI3PK'],'GSI3SK':expiry}
        put = {'TableName':table,'Item':wire(state)}
        if saved is None:
            put['ConditionExpression']='attribute_not_exists(PK)'
        else:
            put.update(ConditionExpression='revision = :r',ExpressionAttributeValues=wire({':r':saved['revision']}))
        ddb.transact_write_items(TransactItems=[condition(table,pk,'SUMMARY',version),{'Put':put}])
        return False
    cursor = saved['cursor']
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
        try:
            metadata = validate_metadata(row)
        except ValueError:
            raise CoverageUnavailable() from None
        state.update(merge_metadata(state, metadata))
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
        action = {'Delete':{'TableName':table,'Key':key(pk,'SUMMARY'),'ConditionExpression':'#v = :v AND attribute_not_exists(lifecycleState)',
                           'ExpressionAttributeNames':{'#v':'version'},'ExpressionAttributeValues':wire({':v':version})}}
    else:
        centroid = [(v / state['vectors']).quantize(Decimal('.0000001')) for v in state['sums']]
        action = {'Update':{'TableName':table,'Key':key(pk,'SUMMARY'),
            'UpdateExpression':'SET centroid = :centroid, contributorCount = :count, submissionCount = :submissions, #v = :next, metadataSchemaVersion = :mv, lexicalFingerprint = :lex, signalIds = :signals, indicatorIds = :indicators',
            'ConditionExpression':'#v = :v AND attribute_not_exists(lifecycleState)','ExpressionAttributeNames':{'#v':'version'},
            'ExpressionAttributeValues':wire({':v':version,':next':version+1,':centroid':centroid,
                                               ':count':state['contributors'],':submissions':state['submissions'], ':mv':1,
                                               ':lex':state['lexicalFingerprint'],':signals':state['signalIds'],':indicators':state['indicatorIds']})}}
    ddb.transact_write_items(TransactItems=[action,{'Delete':{'TableName':table,'Key':key(pk,'DELETION_RECOMPUTE'),
        'ConditionExpression':'revision = :r','ExpressionAttributeValues':wire({':r':saved['revision']})}}])
    return True


def validate_repair(saved, summary, now):
    expected = {'PK','SK','revision','summaryVersion','cursor','contributors','submissions','vectors','sums',
                'cutoffEpoch','expiresAt','GSI3PK','GSI3SK','metadataSchemaVersion',*empty_metadata()}
    require(set(saved) == expected and saved['PK'] == summary['PK'] and saved['SK'] == 'DELETION_RECOMPUTE'
            and saved['expiresAt'] == summary['expiresAt'] and saved['GSI3PK'] == summary['GSI3PK']
            and saved['GSI3SK'] == summary['expiresAt'])
    for field in ('revision','summaryVersion','cutoffEpoch'):
        integer(saved[field],1)
    for field in ('contributors','submissions','vectors'):
        integer(saved[field])
    require(saved['vectors'] <= saved['contributors'] <= saved['submissions'] <= 3*saved['contributors'])
    require(saved['cutoffEpoch'] <= now and type(saved['sums']) is list and len(saved['sums']) <= 384)
    require(all(type(v) in (int,Decimal) and Decimal(v).is_finite() for v in saved['sums']))
    require((saved['vectors'] == 0) == (len(saved['sums']) == 0))
    cursor = saved['cursor']
    require(cursor is None or (type(cursor) is str and cursor.startswith('CONTRIB#') and len(cursor) <= 2048))
    try:
        metadata = validate_metadata(saved)
    except ValueError:
        raise CoverageUnavailable() from None
    require(all(saved[k] == v for k,v in metadata.items()))
