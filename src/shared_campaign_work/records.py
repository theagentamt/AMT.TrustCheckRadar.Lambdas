"""Strict paired period-work records; no approval or storage mutations."""
import base64
import hashlib
from uuid import UUID
from shared_campaign_contracts.app_features import TAXONOMY_BUCKETS
import re
from decimal import Decimal

MAX_ORDINAL=9007199254740991
WORK_PREFIX='PERIOD_WORK#'
CONTROL_PREFIX='PERIOD_WORK_CONTROL#'
LOOKUP_PREFIX='WORK_LOOKUP#'
DOMAIN=b'amt.campaign.period-work.lookup.v1\x00'
WORK_FIELDS={'PK','SK','recordType','schemaVersion','environment','generation','periodId','ordinal',
             'targetTableFamily','targetPK','targetSK','deadlineEpoch'}
LOOKUP_FIELDS={'PK','SK','recordType','schemaVersion','environment','generation','periodId','ordinal','deadlineEpoch'}
CONTROL_FIELDS={'PK','SK','recordType','schemaVersion','environment','generation','periodId',
                'manifestSha256','inventoryRevision','revision','nextOrdinal','pendingCount',
                'passRevision','passCursor','passHighWater','lastProgressAtEpoch','lastFullPassAtEpoch'}

class WorkUnavailable(RuntimeError):
    def __init__(self):super().__init__('Campaign period work is unavailable')


def need(value):
    if not value:raise WorkUnavailable()


def integer(value,minimum=0,maximum=MAX_ORDINAL):
    need(type(value) is int or type(value) is Decimal and value.is_finite())
    need(minimum<=value<=maximum and value==int(value))
    return int(value)


def identity(environment,generation):
    from uuid import UUID
    need(environment in ('dev','uat','prod'))
    try:
        value=UUID(generation);need(value.version==4 and str(value)==generation)
    except (ValueError,TypeError,AttributeError):raise WorkUnavailable() from None


def canonical_uuid(value):
    try:
        parsed=UUID(value);return parsed.version==4 and str(parsed)==value
    except (ValueError,TypeError,AttributeError):return False


def token(value):
    if type(value) is not str or re.fullmatch('[A-Za-z0-9_-]{43}',value) is None:return False
    try:return base64.urlsafe_b64encode(base64.urlsafe_b64decode(value+'=')).decode().rstrip('=')==value
    except ValueError:return False


def period_text(value):
    need(type(value) is str and re.fullmatch('0|[1-9][0-9]{0,15}',value))
    return integer(int(value))


def target(family,pk,sk):
    need(family in ('pipeline','outbox') and type(pk) is str and type(sk) is str
         and 0<len(pk.encode('utf8'))<=2048 and 0<len(sk.encode('utf8'))<=1024)
    parts=pk.split('#');valid=False
    if family=='pipeline':
        if len(parts)==2 and parts[0]=='EVENT':valid=canonical_uuid(parts[1]) and sk in ('FEATURE','DEDUPE','CLUSTERED')
        elif len(parts)==2 and parts[0]=='CANDIDATE':
            valid=canonical_uuid(parts[1]) and (sk in ('SUMMARY','DELETION_RECOMPUTE') or sk.startswith('CONTRIB#') and token(sk[8:]))
        elif len(parts)==3 and parts[0]=='CONTRIB':
            period_text(parts[1]);loc=sk.split('#')
            valid=token(parts[2]) and (sk=='TOMBSTONE' or len(loc)==3 and loc[0]=='LOCATOR' and loc[1] in ('EVENT','CANDIDATE') and canonical_uuid(loc[2]))
        elif len(parts)==3 and parts[0]=='BUCKET':
            period_text(parts[1]);valid=parts[2] in TAXONOMY_BUCKETS and sk=='CREATION_CONTROL'
    elif len(parts)==2:
        valid=(parts[0]=='EVENT' and canonical_uuid(parts[1]) and sk=='OBSERVATION_READY'
               or parts[0]=='ACCOUNT' and re.fullmatch('[0-9a-f]{64}',parts[1]) is not None
               and sk.startswith('OUTBOX#') and canonical_uuid(sk[7:]))
    need(valid)
    return family,pk,sk


def lookup_key(family,pk,sk):
    values=target(family,pk,sk)
    framed=DOMAIN+b''.join(len(v.encode('utf8')).to_bytes(4,'big')+v.encode('utf8') for v in values)
    return {'PK':LOOKUP_PREFIX+hashlib.sha256(framed).hexdigest(),'SK':'RECORD'}


def work_key(period,ordinal):
    return {'PK':WORK_PREFIX+str(integer(period)), 'SK':f'WORK#{integer(ordinal,1):020d}'}


def control_key(period):return {'PK':CONTROL_PREFIX+str(integer(period)),'SK':'STATE'}


def pair(environment,generation,period,ordinal,family,pk,sk,deadline):
    identity(environment,generation);target(family,pk,sk)
    if pk.startswith(('CONTRIB#','BUCKET#')):need(period_text(pk.split('#')[1])==integer(period))
    common={'schemaVersion':1,'environment':environment,'generation':generation,'periodId':integer(period),
            'ordinal':integer(ordinal,1),'deadlineEpoch':integer(deadline,1)}
    work=work_key(period,ordinal)|common|{'recordType':'CAMPAIGN_PERIOD_WORK',
        'targetTableFamily':family,'targetPK':pk,'targetSK':sk}
    lookup=lookup_key(family,pk,sk)|common|{'recordType':'CAMPAIGN_PERIOD_WORK_LOOKUP'}
    return work,lookup


def validate_pair(work,lookup,environment,generation,*,family=None,pk=None,sk=None):
    need(type(work) is dict and set(work)==WORK_FIELDS and type(lookup) is dict and set(lookup)==LOOKUP_FIELDS)
    family=work['targetTableFamily'] if family is None else family
    pk=work['targetPK'] if pk is None else pk;sk=work['targetSK'] if sk is None else sk
    expected=pair(environment,generation,work['periodId'],work['ordinal'],family,pk,sk,work['deadlineEpoch'])
    need(work==expected[0] and lookup==expected[1])
    # Python bool/int equality cannot certify a DynamoDB field's type.
    for row in (work,lookup):
        integer(row['schemaVersion'],1,1);integer(row['periodId']);integer(row['ordinal'],1);integer(row['deadlineEpoch'],1)
    return work,lookup


def validate_control(row,environment,generation,period,manifest,revision,now):
    identity(environment,generation);now=integer(now,1)
    need(type(row) is dict and set(row)==CONTROL_FIELDS and type(manifest) is str and re.fullmatch('[0-9a-f]{64}',manifest))
    need(row['PK']==control_key(period)['PK'] and row['SK']=='STATE' and row['recordType']=='CAMPAIGN_PERIOD_WORK_CONTROL'
         and row['environment']==environment and row['generation']==generation and row['manifestSha256']==manifest)
    integer(row['schemaVersion'],1,1);need(integer(row['periodId'])==integer(period))
    need(integer(row['inventoryRevision'],1)==integer(revision,1))
    integer(row['revision'],1);last=integer(row['nextOrdinal'],1)-1
    need(integer(row['pendingCount'])<=last and integer(row['passCursor'])<=integer(row['passHighWater'])<=last)
    integer(row['passRevision']);integer(row['lastProgressAtEpoch'],0,now);integer(row['lastFullPassAtEpoch'],0,now)
    return row
