"""Immutable publication evidence shared by publication and account cleanup."""
import hashlib
import json
from decimal import Decimal
from uuid import UUID
from .core import require,integer

MUTABLE_AGGREGATE_FIELDS={'state','version','GSI1PK','GSI1SK'}
PHASE_FIELDS={'lifecycleState','lifecycleOperationId','lifecycleStartedAtEpoch',
              'lifecycleInventoryRevision','lifecycleAggregateDigest'}

def _json(value):
    if isinstance(value,Decimal):return int(value) if value==int(value) else float(value)
    if isinstance(value,dict):return {k:_json(v) for k,v in value.items()}
    if isinstance(value,list):return [_json(v) for v in value]
    return value

def digest(value):
    return hashlib.sha256(json.dumps(_json(value),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def phase(candidate,now):
    value=candidate.get('lifecycleState')
    require(value in ('FROZEN','REPAIRING','PUBLISHED','SUPPRESSED'))
    fields={k for k in candidate if k.startswith('lifecycle')}
    require(fields==PHASE_FIELDS-({'lifecycleAggregateDigest'} if value in ('FROZEN','REPAIRING') else set()))
    integer(candidate.get('version'),1);integer(candidate.get('lifecycleInventoryRevision'),1)
    require(integer(candidate.get('lifecycleStartedAtEpoch'),1)<=now)
    try:
        ident=UUID(candidate['lifecycleOperationId']);require(ident.version==4 and str(ident)==candidate['lifecycleOperationId'])
    except (ValueError,TypeError,AttributeError):require(False)
    return value

def verify_aggregate(candidate,aggregate):
    require(candidate['lifecycleState'] in ('PUBLISHED','SUPPRESSED'))
    if candidate['lifecycleState']=='SUPPRESSED':
        require(aggregate is None and candidate.get('lifecycleAggregateDigest')==digest({}));return
    require(type(aggregate) is dict and aggregate.get('state') in ('PENDING_REVIEW','CONFIRMED','PUBLISHED','SUPPRESSED'))
    require(aggregate.get('PK')=='CAMPAIGN#'+candidate['candidateId'] and aggregate.get('SK')=='AGGREGATE'
            and aggregate.get('campaignId')==candidate['candidateId'])
    integer(aggregate.get('version'),1)
    if aggregate['state']=='PUBLISHED':
        require(aggregate.get('GSI1PK')=='STATE#PUBLISHED' and aggregate.get('GSI1SK')==aggregate.get('periodWeek','')+'#CAMPAIGN#'+candidate['candidateId'])
    else:require('GSI1PK' not in aggregate and 'GSI1SK' not in aggregate)
    require(digest({k:v for k,v in aggregate.items() if k not in MUTABLE_AGGREGATE_FIELDS})==candidate.get('lifecycleAggregateDigest'))
