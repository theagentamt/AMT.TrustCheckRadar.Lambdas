"""Modern authoritative outbox locator: original logical deadline, no TTL."""
import hashlib
from . import records as R,configuration as C

FIELDS={'PK','SK','recordType','schemaVersion','recordVersion','environment','accountIdHash','statisticsEventId',
        'eventPK','eventSK','eventExpiresAt','logicalExpiresAt'}


def validate_locator(row,environment,account_hash=None):
    R.need(C.enabled());C.pins()
    R.need(type(row) is dict and set(row)==FIELDS and row['recordType']=='CAMPAIGN_OUTBOX_LOCATOR'
        and row['environment']==environment and R.canonical_uuid(row['statisticsEventId']))
    R.integer(row['schemaVersion'],2,2);R.integer(row['recordVersion'],1,1)
    R.target('outbox',row['PK'],row['SK'])
    R.need(row['PK']=='ACCOUNT#'+row['accountIdHash'] and row['SK']=='OUTBOX#'+row['statisticsEventId']
        and row['eventPK']=='EVENT#'+row['statisticsEventId'] and row['eventSK']=='OBSERVATION_READY')
    if account_hash is not None:R.need(row['accountIdHash']==account_hash)
    R.need(R.integer(row['logicalExpiresAt'],1)==R.integer(row['eventExpiresAt'],1)+86400)
    return row


def paired_delete(client,*,outbox_table,ledger_table,command,locator,event,now):
    """The existing approved account command owns both target and index erasure."""
    from .transactions import TrackedClient,operation
    validate_locator(locator,command['environment'],hashlib.sha256(command['accountId'].encode()).hexdigest())
    event_key={'PK':locator['eventPK'],'SK':locator['eventSK']}
    if event is not None:
        R.need(event.get('accountId')==command['accountId'] and event.get('PK')==event_key['PK']
             and event.get('SK')==event_key['SK'] and event.get('environment')==command['environment']
             and event.get('statisticsEventId')==locator['statisticsEventId'] and event.get('expiresAt')==locator['eventExpiresAt'])
    actions=[C.condition(ledger_table,command),operation('Delete',outbox_table,locator,observed=locator),
             operation('Delete',outbox_table,event,observed=event) if event else
             {'Delete':{'TableName':outbox_table,'Key':C.wire(event_key),'ConditionExpression':'attribute_not_exists(PK)'}}]
    TrackedClient(client,now=lambda:now).transact_write_items(TransactItems=actions)
