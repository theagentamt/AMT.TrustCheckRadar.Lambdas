"""Versioned repair fence; logical deadline, no independent DynamoDB TTL."""
from progress import get,key,wire,require,integer

BASE={'PK','SK','recordType','schemaVersion','environment','periodId','createdAtEpoch',
      'deletionDeadlineEpoch','GSI3PK','GSI3SK'}


def validate(value,environment,partition):
    require(type(value) is dict and set(value) in (BASE,BASE|{'locatorCleanupRevision','locatorCleanupState'})
            and value.get('PK')==partition and value.get('SK')=='TOMBSTONE'
            and value.get('recordType')=='CAMPAIGN_DELETION_TOMBSTONE'
            and integer(value.get('schemaVersion'))==2 and value.get('environment')==environment
            and str(integer(value.get('periodId')))==partition.split('#')[1]
            and value.get('GSI3PK')=='EXPIRY#'+environment)
    created=integer(value.get('createdAtEpoch'),1)
    deadline=integer(value.get('deletionDeadlineEpoch'),1)
    require(created<deadline<=created+21*86400 and integer(value.get('GSI3SK'))==deadline)
    return value


def ensure(ddb,table,environment,partition,period,requested,retention_days):
    existing=get(ddb,table,partition,'TOMBSTONE')
    if existing is not None:
        # No opportunistic upgrade of TTL-bearing legacy evidence. Migration must
        # recover pending repair and preserve its original logical deadline.
        return validate(existing,environment,partition)
    deadline=requested+retention_days*86400
    value={'PK':partition,'SK':'TOMBSTONE','recordType':'CAMPAIGN_DELETION_TOMBSTONE',
        'schemaVersion':2,'environment':environment,'periodId':period,'createdAtEpoch':requested,
        'deletionDeadlineEpoch':deadline,'GSI3PK':'EXPIRY#'+environment,'GSI3SK':deadline}
    ddb.transact_write_items(TransactItems=[{'Put':{'TableName':table,'Item':wire(value),
        'ConditionExpression':'attribute_not_exists(PK) AND attribute_not_exists(SK)'}}])
    return value


def retire(*args,**kwargs):
    # No verified retirement/period inventory path is implemented or approved.
    # Expiry, empty GSI or an elapsed deadline must never remove this proof state.
    require(False)
