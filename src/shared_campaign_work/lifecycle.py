"""Authoritative bounded work passes and irreversible drain/seal transitions."""
from copy import deepcopy
import os
import re
from . import configuration as C,records as R,targets
from .transactions import TrackedClient,operation,key
from shared_campaign_locators import period as P

SEAL_FIELDS={'sealSchemaVersion','sealedAtEpoch','sealManifestSha256','sealInventoryRevision',
             'sealGeneration','sealPipelineTableId','sealOutboxTableId','sealNextOrdinal'}

class Lifecycle:
    def __init__(self,client,*,now,remaining_ms=lambda:30000):
        R.need(os.environ.get('CAMPAIGN_PERIOD_LIFECYCLE_ENABLED','false')=='true')
        self.d=client;self.now=now;self.remaining=remaining_ms;self.config=C.pins()
        self.table=self.config['resources']['pipeline']['tableName']
        C.binding(client,self.config,remaining_ms)
        self.marker=C.validate_marker(self.get(self.table,{'PK':'INVENTORY#'+self.config['environment'],'SK':'CAMPAIGN_PERIOD_WORK'}),self.config,self.clock())

    def clock(self):return R.integer(self.now(),1)
    def call(self,method,**kw):
        R.need(self.remaining()>=6000);return method(**kw)
    def get(self,table,k):
        raw=self.call(self.d.get_item,TableName=table,Key=C.wire(k),ConsistentRead=True).get('Item')
        return C.plain(raw) if raw else None
    def control(self,period):
        return R.validate_control(self.get(self.table,R.control_key(period)),self.config['environment'],self.config['generation'],period,
                                  self.config['manifest'],self.config['revision'],self.clock())
    def registry(self,period):
        return P.validate(self.get(self.table,{'PK':f'PERIOD#{period}','SK':'HMAC_KEY'}),period,
             self.marker['locatorManifestSha256'],int(self.marker['locatorInventoryRevision']),self.clock(),states=('OPEN','CLOSING','DRAINING'))
    def tx(self,actions):return self.call(self.d.transact_write_items,TransactItems=[C.condition(self.table,self.marker),*actions])
    def update(self,before,after):
        changed={k:v for k,v in after.items() if k not in ('PK','SK') and before.get(k)!=v}
        R.need(changed)
        value={'TableName':self.table,'Key':key(before),**C.exact(before),
               'UpdateExpression':'SET '+', '.join(f'#u{i}=:u{i}' for i in range(len(changed)))}
        value['ExpressionAttributeNames'].update({f'#u{i}':k for i,k in enumerate(changed)})
        value['ExpressionAttributeValues'].update(C.wire({f':u{i}':v for i,v in enumerate(changed.values())}))
        return {'Update':value}

    def begin_drain(self,period):
        record=self.registry(period);now=self.clock()
        R.need(now>=record['retireAfterEpoch'])
        if record['admissionState']=='DRAINING':
            self.tx([P.condition(self.table,record)]);return record
        R.need(record['admissionRevision']<R.MAX_ORDINAL)
        changed=record|{'admissionState':'DRAINING','admissionRevision':record['admissionRevision']+1,'admissionChangedAtEpoch':now}
        self.tx([self.update(record,changed)])
        return changed

    def _group(self,work,registry):
        """Whole-period drain preserves account locator→target atomicity."""
        family=work['targetTableFamily'];table=self.config['resources'][family]['tableName']
        pk,sk=work['targetPK'],work['targetSK'];row=self.get(table,{'PK':pk,'SK':sk})
        keys=[{'PK':pk,'SK':sk}]
        if row is not None:
            R.need(targets.deadline(family,row)==work['deadlineEpoch'])
            if family=='outbox':
                import hashlib
                if sk=='OBSERVATION_READY':
                    account=row.get('accountId');R.need(type(account) is str and 0<len(account)<=200)
                    account_hash=hashlib.sha256(account.encode()).hexdigest()
                    locator_key={'PK':'ACCOUNT#'+account_hash,'SK':'OUTBOX#'+pk[6:]}
                    locator=self.get(table,locator_key)
                    if locator is not None:
                        from .outbox import validate_locator
                        validate_locator(locator,self.config['environment'],account_hash)
                        R.need(locator['eventPK']==pk and locator['eventExpiresAt']==row['expiresAt'])
                    keys.append(locator_key)
                else:
                    from .outbox import validate_locator
                    validate_locator(row,self.config['environment'])
                    event=self.get(table,{'PK':row['eventPK'],'SK':'OBSERVATION_READY'})
                    if event is not None:
                        R.need(type(event.get('accountId')) is str and hashlib.sha256(event['accountId'].encode()).hexdigest()==row['accountIdHash']
                               and event.get('expiresAt')==row['eventExpiresAt'] and event.get('environment')==self.config['environment'])
                    keys.append({'PK':row['eventPK'],'SK':'OBSERVATION_READY'})
            elif sk.startswith('LOCATOR#'):
                from shared_campaign_locators.core import validate_locator,locator_for_target
                validate_locator(row,self.config['environment'])
                R.need(row['periodId']==registry['periodId'])
                paired=self.get(table,{'PK':row['targetPK'],'SK':row['targetSK']})
                if paired is not None:R.need(locator_for_target(paired,self.config['environment'])==row)
                keys.append({'PK':row['targetPK'],'SK':row['targetSK']})
                if row['targetPK'].startswith('EVENT#'):
                    keys.extend({'PK':row['targetPK'],'SK':s} for s in ('DEDUPE','CLUSTERED'))
            elif pk.startswith('EVENT#'):
                keys.extend({'PK':pk,'SK':s} for s in ('FEATURE','DEDUPE','CLUSTERED') if s!=sk)
                feature=row if sk=='FEATURE' else self.get(table,{'PK':pk,'SK':'FEATURE'})
                if feature:
                    from shared_campaign_locators.core import locator_for_target
                    locator=locator_for_target(feature,self.config['environment'])
                    keys.append({'PK':locator['PK'],'SK':locator['SK']})
            elif sk.startswith('CONTRIB#'):
                from shared_campaign_locators.core import locator_for_target
                locator=locator_for_target(row,self.config['environment']);keys.append({'PK':locator['PK'],'SK':locator['SK']})
        unique={(k['PK'],k['SK']):k for k in keys};R.need(len(unique)<=5)
        guards=[]
        if family=='pipeline' and registry['admissionState']!='DRAINING':
            # A retained suppression/repair fence cannot be erased while its
            # period still permits additions. Valid new clocks reach recoveryEnd.
            R.need(sk not in ('TOMBSTONE','DELETION_RECOMPUTE'))
            if pk.startswith('CANDIDATE#') and sk.startswith('CONTRIB#'):
                summary=self.get(table,{'PK':pk,'SK':'SUMMARY'})
                if summary is not None:
                    R.need(targets.deadline(family,summary)<=self.clock())
                    unique[(pk,'SUMMARY')]={'PK':pk,'SK':'SUMMARY'}
                else:guards.append({'ConditionCheck':{'TableName':table,'Key':C.wire({'PK':pk,'SK':'SUMMARY'}),'ConditionExpression':'attribute_not_exists(PK)'}})
        observed=[]
        for k in unique.values():
            target=self.get(table,k)
            lookup=self.get(self.table,R.lookup_key(family,k['PK'],k['SK']))
            if lookup is not None:
                R.need(lookup.get('periodId')==registry['periodId'])
                if registry['admissionState']!='DRAINING' and not (family=='outbox' and sk=='OBSERVATION_READY' and k['SK'].startswith('OUTBOX#')):R.need(R.integer(lookup.get('deadlineEpoch'),1)<=self.clock())
            if target is not None:
                if 'periodId' in target:R.need(target['periodId']==registry['periodId'])
                if registry['admissionState']!='DRAINING' and not (family=='outbox' and sk=='OBSERVATION_READY' and k['SK'].startswith('OUTBOX#')):R.need(targets.deadline(family,target)<=self.clock())
            observed.append((k,target))
        actions=[operation('Delete',table,target,observed=target) if target else {'Delete':{'TableName':table,'Key':C.wire(k),'ConditionExpression':'attribute_not_exists(PK)'}} for k,target in observed]+guards
        # TrackedClient rereads all members and guards exact target/index/counter
        # state. Absent siblings require both target and lookup absence.
        TrackedClient(self.d,registries=[registry],now=self.now,remaining_ms=self.remaining,allowed_periods={int(registry['periodId'])}).transact_write_items(TransactItems=actions)

    def period_pass(self,period,*,max_attempts=8,recover_candidate=None):
        R.need(type(max_attempts) is int and 1<=max_attempts<=8 and period>=self.marker['minimumPeriodId'])
        record=self.registry(period);now=self.clock();control=self.control(period)
        if now>=record['retireAfterEpoch']:record=self.begin_drain(period)
        elif now>=(period+1)*P.PERIOD_SECONDS and record['admissionState']=='OPEN':
            changed=record|{'admissionState':'CLOSING','admissionRevision':record['admissionRevision']+1,'admissionChangedAtEpoch':now}
            R.need(changed['admissionRevision']<=R.MAX_ORDINAL);self.tx([self.update(record,changed)]);record=changed
        totals={'attempted':0,'unverified':0,'overdue':0,'fullPass':False,'sealed':False,'backlog':int(control['pendingCount'])}
        if control['passCursor']==control['passHighWater']:
            next_control=control|{'revision':control['revision']+1,'passRevision':control['passRevision']+1,
                                 'passCursor':0,'passHighWater':control['nextOrdinal']-1}
            R.validate_control(next_control,self.config['environment'],self.config['generation'],period,self.config['manifest'],self.config['revision'],now)
            self.tx([P.condition(self.table,record),self.update(control,next_control)]);control=next_control
        args={'TableName':self.table,'KeyConditionExpression':'PK=:pk AND SK BETWEEN :lo AND :hi',
              'ExpressionAttributeValues':C.wire({':pk':R.work_key(period,1)['PK'],':lo':R.work_key(period,int(control['passCursor'])+1)['SK'],
                  ':hi':R.work_key(period,max(1,int(control['passHighWater'])))['SK']}),'ConsistentRead':True,'Limit':16}
        page=self.call(self.d.query,**args);items=page.get('Items',[])
        R.need(type(items) is list and len(items)<=16)
        for raw in items[:max_attempts]:
            work=C.plain(raw)
            # Only numeric canonical work keys can advance the numeric cursor.
            R.need(type(work.get('SK')) is str and re.fullmatch(r'WORK#[0-9]{20}',work['SK']))
            ordinal=R.integer(int(work['SK'][5:]),1);R.need(work.get('PK')==R.work_key(period,ordinal)['PK'] and work.get('SK')==R.work_key(period,ordinal)['SK']
                and control['passCursor']<ordinal<=control['passHighWater'])
            current=self.control(period)
            R.need(current['passRevision']==control['passRevision'] and current['passCursor']==control['passCursor'])
            next_control=current|{'revision':current['revision']+1,'passCursor':ordinal}
            present=self.get(self.table,R.work_key(period,ordinal))
            R.need(present is None or present==work)
            work_guard=C.condition(self.table,work) if present is not None else {'ConditionCheck':{'TableName':self.table,'Key':C.wire(R.work_key(period,ordinal)),'ConditionExpression':'attribute_not_exists(PK)'}}
            self.tx([P.condition(self.table,record),work_guard,self.update(current,next_control)])
            control=next_control
            if present is None:continue # A prior atomic paired delete consumed this ordinal.
            try:
                lookup=self.get(self.table,R.lookup_key(work['targetTableFamily'],work['targetPK'],work['targetSK']))
                R.validate_pair(work,lookup,self.config['environment'],self.config['generation'])
                due=work['deadlineEpoch']<=self.clock();totals['overdue']+=int(due)
                # Pipeline summary/repair state drains only behind DRAINING.
                # Earlier outbox deadlines are safe to erase as owned pairs.
                if record['admissionState']=='CLOSING' and not due and work['targetTableFamily']=='pipeline' and work['targetSK']=='SUMMARY' and recover_candidate is not None:
                    R.need(self.remaining()>=6000);totals['attempted']+=1
                    recover_candidate(work['targetPK'][10:])
                elif record['admissionState']=='DRAINING' or due:
                    R.need(self.remaining()>=6000);totals['attempted']+=1;self._group(work,record)
            except Exception:
                totals['unverified']+=1
            R.need(self.remaining()>=6000)
        if len(items)<=max_attempts and not page.get('LastEvaluatedKey'):
            current=self.control(period);R.need(current['passRevision']==control['passRevision'])
            done=current|{'revision':current['revision']+1,'passCursor':current['passHighWater'],'lastFullPassAtEpoch':self.clock()}
            self.tx([P.condition(self.table,record),self.update(current,done)]);control=done;totals['fullPass']=True
        else:control=self.control(period)
        totals['backlog']=int(control['pendingCount'])
        if record['admissionState']=='DRAINING' and control['pendingCount']==0:
            totals['sealed']=self.seal(record,control)
        totals['progressAgeSeconds']=0 if not totals['backlog'] else max(0,self.clock()-int(control['lastProgressAtEpoch'] or record['admissionChangedAtEpoch']))
        totals['fullPassAgeSeconds']=max(0,self.clock()-int(control['lastFullPassAtEpoch']))
        return totals

    def seal(self,record,control):
        period=int(record['periodId']);R.need(record['admissionState']=='DRAINING' and control['pendingCount']==0)
        page=self.call(self.d.query,TableName=self.table,KeyConditionExpression='PK=:pk',
                       ExpressionAttributeValues=C.wire({':pk':R.work_key(period,1)['PK']}),ConsistentRead=True,Limit=1)
        R.need(not page.get('Items') and not page.get('LastEvaluatedKey'))
        C.binding(self.d,self.config,self.remaining);now=self.clock()
        changed=record|{'admissionState':'SEALED','admissionRevision':record['admissionRevision']+1,'admissionChangedAtEpoch':now,
             'sealSchemaVersion':1,'sealedAtEpoch':now,'sealManifestSha256':self.config['manifest'],'sealInventoryRevision':self.config['revision'],
             'sealGeneration':self.config['generation'],'sealPipelineTableId':self.config['resources']['pipeline']['tableId'],
             'sealOutboxTableId':self.config['resources']['outbox']['tableId'],'sealNextOrdinal':control['nextOrdinal']}
        R.need(changed['admissionRevision']<=R.MAX_ORDINAL)
        self.tx([C.condition(self.table,control),self.update(record,changed)])
        return True
