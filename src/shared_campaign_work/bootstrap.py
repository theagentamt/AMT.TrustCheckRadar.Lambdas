"""Pure bounded migration planner; caller must independently approve a closed snapshot.

No SDK calls or approval inference. The operator adds the approved marker Put and
whole-snapshot/closed-writer checks. Only completed retained tombstones are adopted.
"""
from . import configuration as C, records as R
from shared_campaign_locators import period as P

TOMB_FIELDS={'PK','SK','recordType','schemaVersion','environment','periodId','createdAtEpoch',
             'deletionDeadlineEpoch','GSI3PK','GSI3SK'}
SWEEP_FIELDS={'schemaVersion','operationId','inventoryRevision','minimumPeriodId','maximumPeriodId','nextPeriodId'}


def tombstone(row, config, now):
    R.need(type(row) is dict)
    optional={'locatorCleanupRevision','locatorCleanupState','retainedPeriodSweep','retainedPeriodSweepRevision'}
    R.need(set(row)==TOMB_FIELDS|optional)
    R.target('pipeline',row['PK'],row['SK']);period=R.integer(row['periodId'])
    R.need(row['PK'].startswith(f'CONTRIB#{period}#') and row['SK']=='TOMBSTONE'
           and row['recordType']=='CAMPAIGN_DELETION_TOMBSTONE' and row['environment']==config['environment'])
    R.integer(row['schemaVersion'],2,2)
    created=R.integer(row['createdAtEpoch'],1,now);deadline=R.integer(row['deletionDeadlineEpoch'],created+1,created+21*86400)
    R.need(row['GSI3PK']=='EXPIRY#'+config['environment'] and R.integer(row['GSI3SK'])==deadline)
    state=row['locatorCleanupState'];R.integer(row['locatorCleanupRevision'],1)
    R.need(type(state) is dict and set(state)=={'operationId','phase','cursor'}
           and R.canonical_uuid(state['operationId']) and state['phase']=='SEEK' and state['cursor'] is None)
    sweep=row['retainedPeriodSweep'];R.integer(row['retainedPeriodSweepRevision'],1)
    R.need(type(sweep) is dict and set(sweep)==SWEEP_FIELDS and R.canonical_uuid(sweep['operationId']))
    R.integer(sweep['schemaVersion'],1,1);R.integer(sweep['inventoryRevision'],1)
    first=R.integer(sweep['minimumPeriodId']);last=R.integer(sweep['maximumPeriodId']);next_period=R.integer(sweep['nextPeriodId'])
    R.need(first<=next_period<=last==period and last-first<32)
    return deadline


def actions(registries, tombstones, marker, config, now):
    """Return <=99 conditional actions; reserve one for external approved marker.

    Every supplied existing row is exact-CAS checked. Unknown rows/partial indexes
    must be rejected by the operator's complete, reviewed snapshot classification.
    This function cannot certify that an omitted row does not exist.
    """
    now=R.integer(now,1);C.validate_marker(marker,config,now)
    R.need(type(registries) is list and 1<=len(registries)<=8 and type(tombstones) is list and len(tombstones)<=16)
    table=config['resources']['pipeline']['tableName'];by_period={};result=[]
    for old in registries:
        R.need(type(old) is dict and set(old) in (P.BASE_FIELDS,P.BASE_FIELDS|P.ADMISSION_FIELDS))
        period=R.integer(old['periodId']);R.need(period>=marker['minimumPeriodId'] and period not in by_period)
        if set(old)==P.BASE_FIELDS:
            # Explicit externally approved bootstrap of an exact legacy metadata
            # row, never runtime adoption or an erasure/seal assertion.
            admission={'admissionGeneration':config['generation'],'admissionState':'CLOSING' if now>=(period+1)*P.PERIOD_SECONDS else 'OPEN',
                'admissionRevision':1,'admissionManifestSha256':marker['locatorManifestSha256'],
                'admissionInventoryRevision':marker['locatorInventoryRevision'],'admissionChangedAtEpoch':now}
        else:
            R.integer(old['admissionSchemaVersion'],1,1);admission={}
        modern=old|admission|{'admissionSchemaVersion':2,'workSchemaVersion':1,'workManifestSha256':config['manifest'],'workInventoryRevision':config['revision']}
        P.validate(modern,period,marker['locatorManifestSha256'],marker['locatorInventoryRevision'],now,generation=config['generation'])
        by_period[period]=[]
        result.append({'Put':{'TableName':table,'Item':C.wire(modern),**C.exact(old)}})
    seen=set()
    for row in sorted(tombstones,key=lambda r:(r.get('PK',''),r.get('SK',''))):
        deadline=tombstone(row,config,now);period=row['periodId'];R.need(period in by_period)
        identity=(row['PK'],row['SK']);R.need(identity not in seen);seen.add(identity)
        by_period[period].append((row,deadline))
    for period,rows in sorted(by_period.items()):
        control=R.control_key(period)|{'recordType':'CAMPAIGN_PERIOD_WORK_CONTROL','schemaVersion':1,
            'environment':config['environment'],'generation':config['generation'],'periodId':period,
            'manifestSha256':config['manifest'],'inventoryRevision':config['revision'],'revision':1,
            'nextOrdinal':len(rows)+1,'pendingCount':len(rows),'passRevision':0,'passCursor':0,'passHighWater':0,
            'lastProgressAtEpoch':0,'lastFullPassAtEpoch':0}
        R.validate_control(control,config['environment'],config['generation'],period,config['manifest'],config['revision'],now)
        result.append({'Put':{'TableName':table,'Item':C.wire(control),'ConditionExpression':'attribute_not_exists(PK) AND attribute_not_exists(SK)'}})
        for ordinal,(row,deadline) in enumerate(rows,1):
            result.append(C.condition(table,row))
            for record in R.pair(config['environment'],config['generation'],period,ordinal,'pipeline',row['PK'],row['SK'],deadline):
                result.append({'Put':{'TableName':table,'Item':C.wire(record),'ConditionExpression':'attribute_not_exists(PK) AND attribute_not_exists(SK)'}})
    R.need(len(result)<=99)
    return result
