"""Resource-bound sealed proof and contiguous retired-prefix consumers."""
from . import configuration as C,records as R
from .retirement import validate_sealed,validate_retired
from shared_campaign_locators import period as P

PREFIX_FIELDS={'PK','SK','recordType','schemaVersion','environment','generation','manifestSha256','inventoryRevision',
               'minimumPeriodId','retiredThroughPeriodId','revision','qualifiedAtEpoch','pipelineTableId','outboxTableId'}

class Proofs:
    def __init__(self,client,*,now,remaining_ms=lambda:30000):
        self.d=client;self.now=now;self.remaining=remaining_ms;self.config=C.pins()
        self.table=self.config['resources']['pipeline']['tableName'];C.binding(client,self.config,remaining_ms)
        self.marker=C.validate_marker(self.get({'PK':'INVENTORY#'+self.config['environment'],'SK':'CAMPAIGN_PERIOD_WORK'}),self.config,self.clock())
        self.guards=[C.condition(self.table,self.marker)]
        self.prefix=self.get({'PK':'PERIOD_RETIRED_PREFIX#'+self.config['environment'],'SK':'STATE'})
        self.minimum=int(self.marker['minimumPeriodId'])
        if self.prefix is not None:
            row=self.prefix;self.validate_prefix(row)
            self.minimum=int(row['retiredThroughPeriodId'])+1;self.guards.append(C.condition(self.table,row))
        else:self.guards.append({'ConditionCheck':{'TableName':self.table,'Key':C.wire({'PK':'PERIOD_RETIRED_PREFIX#'+self.config['environment'],'SK':'STATE'}),'ConditionExpression':'attribute_not_exists(PK)'}})
    def clock(self):return R.integer(self.now(),1)
    def call(self,method,**kw):R.need(self.remaining()>=6000);return method(**kw)
    def get(self,k):
        raw=self.call(self.d.get_item,TableName=self.table,Key=C.wire(k),ConsistentRead=True).get('Item')
        return C.plain(raw) if raw else None
    def validate_prefix(self,row):
        R.need(type(row) is dict and set(row)==PREFIX_FIELDS and row['PK']=='PERIOD_RETIRED_PREFIX#'+self.config['environment']
          and row['SK']=='STATE' and row['recordType']=='CAMPAIGN_RETIRED_PREFIX' and row['environment']==self.config['environment']
          and row['generation']==self.config['generation'] and row['manifestSha256']==self.config['manifest']
          and row['inventoryRevision']==self.config['revision'] and row['minimumPeriodId']==self.marker['minimumPeriodId']
          and row['pipelineTableId']==self.config['resources']['pipeline']['tableId']
          and row['outboxTableId']==self.config['resources']['outbox']['tableId'])
        R.integer(row['schemaVersion'],1,1);R.integer(row['revision'],1);R.integer(row['qualifiedAtEpoch'],1,self.clock())
        through=R.integer(row['retiredThroughPeriodId'],int(self.marker['minimumPeriodId']))
        R.need((through+1)*P.PERIOD_SECONDS+P.RECOVERY_SECONDS<=row['qualifiedAtEpoch'])
        return row
    def sealed(self,period):
        if period<self.minimum:return True
        row=self.get({'PK':f'PERIOD#{period}','SK':'HMAC_KEY'})
        if row is None or row.get('admissionState')!='SEALED':return False
        validate_sealed(row,self.config,self.marker,self.clock());R.need(row['periodId']==period)
        control=R.validate_control(self.get(R.control_key(period)),self.config['environment'],self.config['generation'],period,
                                  self.config['manifest'],self.config['revision'],self.clock())
        R.need(control['pendingCount']==0 and control['nextOrdinal']==row['sealNextOrdinal'])
        page=self.call(self.d.query,TableName=self.table,ConsistentRead=True,Limit=1,
           KeyConditionExpression='PK=:pk',
           ExpressionAttributeValues=C.wire({':pk':R.work_key(period,1)['PK']}))
        R.need(not page.get('Items') and not page.get('LastEvaluatedKey'))
        self.guards.extend([C.condition(self.table,row),C.condition(self.table,control)])
        return True


def advance_prefix(client,*,now,remaining_ms=lambda:30000):
    proof=Proofs(client,now=now,remaining_ms=remaining_ms);period=proof.minimum
    R.need(proof.sealed(period))
    row=proof.get({'PK':f'PERIOD#{period}','SK':'HMAC_KEY'})
    validate_retired(row,proof.config,proof.marker,proof.clock())
    old=proof.prefix
    value={'PK':'PERIOD_RETIRED_PREFIX#'+proof.config['environment'],'SK':'STATE','recordType':'CAMPAIGN_RETIRED_PREFIX',
       'schemaVersion':1,'environment':proof.config['environment'],'generation':proof.config['generation'],
       'manifestSha256':proof.config['manifest'],'inventoryRevision':proof.config['revision'],
       'minimumPeriodId':proof.marker['minimumPeriodId'],'retiredThroughPeriodId':period,
       'revision':int(old['revision'])+1 if old else 1,'qualifiedAtEpoch':proof.clock(),
       'pipelineTableId':proof.config['resources']['pipeline']['tableId'],'outboxTableId':proof.config['resources']['outbox']['tableId']}
    proof.validate_prefix(value)
    guard=C.exact(old) if old else {'ConditionExpression':'attribute_not_exists(PK)'}
    actions=[a for a in proof.guards if next(iter(a.values()))['Key']!=C.wire({'PK':value['PK'],'SK':value['SK']})]
    actions.append({'Put':{'TableName':proof.table,'Item':C.wire(value),**guard}})
    proof.call(client.transact_write_items,TransactItems=actions)
    return {'retiredThroughPeriodId':period}


class GuardedClient:
    def __init__(self,client,proof):self.client=client;self.proof=proof
    def __getattr__(self,name):return getattr(self.client,name)
    def transact_write_items(self,**kw):
        actions=list(kw['TransactItems'])
        for guard in self.proof.guards:
            value=guard['ConditionCheck'];matches=[a for a in actions if next(iter(a.values())).get('TableName')==value['TableName'] and next(iter(a.values())).get('Key')==value['Key']]
            R.need(not matches or matches==[guard])
            if not matches:actions.append(guard)
        R.need(len(actions)<=100)
        return self.proof.call(self.client.transact_write_items,**(kw|{'TransactItems':actions}))
