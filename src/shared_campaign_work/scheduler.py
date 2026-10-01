"""Two numeric period selections per tick; no account-linked global cursor."""
import os
from . import configuration as C,records as R
from .lifecycle import Lifecycle
from .proofs import Proofs,advance_prefix
from .retirement import validate_sealed,retire
from shared_campaign_locators import period as P

FIELDS={'PK','SK','recordType','schemaVersion','environment','generation','manifestSha256','inventoryRevision',
        'revision','nextPeriodId','lastFullPassAtEpoch','lastTickAtEpoch'}
METRICS=('LifecycleHeartbeat','LifecycleFailures','LifecycleWorkAttempted','LifecycleWorkUnverified',
         'LifecycleBacklog','PrivacyDeadlineMissed','LifecycleProgressAgeSeconds','LifecycleFullPassAgeSeconds')


def tick(client,kms,cloudwatch,*,now,remaining_ms=lambda:30000,recover_candidate=None):
    R.need(os.environ.get('CAMPAIGN_PERIOD_LIFECYCLE_ENABLED','false')=='true')
    result={name:0 for name in METRICS};worker=Lifecycle(client,now=now,remaining_ms=remaining_ms)
    config=worker.config;table=worker.table
    def publish():
        R.need(remaining_ms()>=6000)
        cloudwatch.put_metric_data(Namespace='TrustCheckRadar/Campaign',MetricData=[{
          'MetricName':name,'Dimensions':[{'Name':'Environment','Value':config['environment']}],
          'Value':result[name],'Unit':'Seconds' if name.endswith('AgeSeconds') else 'Count'} for name in METRICS])
    try:
        for _ in range(2):
            R.need(remaining_ms()>=6000)
            proof=Proofs(client,now=now,remaining_ms=remaining_ms);first=proof.minimum;last=worker.clock()//P.PERIOD_SECONDS
            if first>last:break
            k={'PK':'PERIOD_SWEEP#'+config['environment'],'SK':'STATE'};cursor=worker.get(table,k)
            if cursor is not None:
                R.need(type(cursor) is dict and set(cursor)==FIELDS and cursor['recordType']=='CAMPAIGN_PERIOD_SWEEP'
                  and cursor['schemaVersion']==1 and cursor['environment']==config['environment'] and cursor['generation']==config['generation']
                  and cursor['manifestSha256']==config['manifest'] and cursor['inventoryRevision']==config['revision'])
                R.integer(cursor['revision'],1);R.integer(cursor['lastFullPassAtEpoch'],0,worker.clock());R.integer(cursor['lastTickAtEpoch'],0,worker.clock())
                selected=R.integer(cursor['nextPeriodId'],int(worker.marker['minimumPeriodId']),last+1)
                if selected<first or selected>last:selected=first
            else:selected=first
            end=selected==last
            changed=k|{'recordType':'CAMPAIGN_PERIOD_SWEEP','schemaVersion':1,'environment':config['environment'],
              'generation':config['generation'],'manifestSha256':config['manifest'],'inventoryRevision':config['revision'],
              'revision':int(cursor['revision'])+1 if cursor else 1,'nextPeriodId':first if end else selected+1,
              'lastFullPassAtEpoch':worker.clock() if end else (cursor['lastFullPassAtEpoch'] if cursor else 0),'lastTickAtEpoch':worker.clock()}
            R.integer(changed['revision'],1)
            action=worker.update(cursor,changed) if cursor else {'Put':{'TableName':table,'Item':C.wire(changed),'ConditionExpression':'attribute_not_exists(PK)'}}
            # Advance before an attempt: missing/poison periods cannot starve the
            # next period. A failed/lost advance may only delay one bounded pass.
            worker.call(client.transact_write_items,TransactItems=[*proof.guards,action])
            R.need(remaining_ms()>=6000)
            try:
                registry=worker.get(table,{'PK':f'PERIOD#{selected}','SK':'HMAC_KEY'})
                if registry and registry.get('admissionState')=='SEALED':
                    validate_sealed(registry,config,worker.marker,worker.clock())
                    R.need(proof.sealed(selected))
                    if os.environ.get('CAMPAIGN_PERIOD_RETIREMENT_ENABLED','false')=='true':
                        retire(client,kms,selected,now=now,remaining_ms=remaining_ms)
                        if selected==proof.minimum:advance_prefix(client,now=now,remaining_ms=remaining_ms)
                else:
                    outcome=worker.period_pass(selected,recover_candidate=recover_candidate)
                    result['LifecycleWorkAttempted']+=outcome['attempted'];result['LifecycleWorkUnverified']+=outcome['unverified']
                    result['LifecycleBacklog']+=outcome['backlog'];result['PrivacyDeadlineMissed']+=outcome['overdue']
                    result['LifecycleProgressAgeSeconds']=max(result['LifecycleProgressAgeSeconds'],outcome['progressAgeSeconds'])
                    result['LifecycleFullPassAgeSeconds']=max(result['LifecycleFullPassAgeSeconds'],outcome['fullPassAgeSeconds'])
            except Exception:
                result['LifecycleWorkUnverified']+=1
        result['LifecycleHeartbeat']=1
    except Exception:
        result['LifecycleFailures']=1
        publish()
        raise R.WorkUnavailable() from None
    publish()
    return {'schemaVersion':1,'periodComplete':False,'metrics':result}
