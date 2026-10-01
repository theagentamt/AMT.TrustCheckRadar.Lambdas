"""Strict repair checkpoint identity; legacy evidence requires a live SUMMARY."""
from decimal import Decimal
from .metadata import empty_metadata,validate_metadata

BASE_FIELDS={'PK','SK','revision','summaryVersion','cursor','contributors','submissions','vectors','sums',
             'cutoffEpoch','expiresAt','GSI3PK','GSI3SK','metadataSchemaVersion',*empty_metadata()}
V2_FIELDS={'repairSchemaVersion','periodId'}


def require(value):
    if not value:raise ValueError('Campaign repair evidence is unavailable')


def integer(value,minimum=0):
    require(type(value) in (int,Decimal) and value>=minimum and value==int(value))
    return int(value)


def validate(saved,summary,now):
    """Caller must transactionally compare the observed authoritative SUMMARY."""
    require(type(saved) is dict and type(summary) is dict)
    modern=set(saved)==BASE_FIELDS|V2_FIELDS
    require(modern or set(saved)==BASE_FIELDS)
    if modern:
        require(integer(saved['repairSchemaVersion'])==2
                and integer(saved['periodId'])==integer(summary.get('periodId')))
    require(saved['PK']==summary['PK'] and saved['SK']=='DELETION_RECOMPUTE'
            and saved['expiresAt']==summary['expiresAt'] and saved['GSI3PK']==summary['GSI3PK']
            and saved['GSI3SK']==summary['expiresAt'])
    integer(saved['expiresAt'],1);integer(now,1)
    for field in ('revision','summaryVersion','cutoffEpoch'):integer(saved[field],1)
    for field in ('contributors','submissions','vectors'):integer(saved[field])
    require(saved['vectors']<=saved['contributors']<=saved['submissions']<=3*saved['contributors'])
    require(saved['cutoffEpoch']<=now and type(saved['sums']) is list and len(saved['sums'])<=384)
    require(all(type(v) in (int,Decimal) and Decimal(v).is_finite() for v in saved['sums']))
    require((saved['vectors']==0)==(len(saved['sums'])==0))
    cursor=saved['cursor']
    require(cursor is None or type(cursor) is str and cursor.startswith('CONTRIB#') and len(cursor)<=2048)
    metadata=validate_metadata(saved)
    require(all(saved[k]==v for k,v in metadata.items()))
    return saved


def validate_orphan(saved,pk,period,environment,now,*,expired=False):
    """Only v2 has its own period identity; no clock/locator inference for v1."""
    require(type(saved) is dict and set(saved)==BASE_FIELDS|V2_FIELDS
            and environment in ('dev','uat','prod') and saved.get('PK')==pk
            and integer(saved.get('periodId'))==integer(period))
    summary={'PK':pk,'periodId':period,'expiresAt':saved['expiresAt'],'GSI3PK':'EXPIRY#'+environment}
    validate(saved,summary,now)
    if expired:require(saved['expiresAt']<=now)
    return saved
