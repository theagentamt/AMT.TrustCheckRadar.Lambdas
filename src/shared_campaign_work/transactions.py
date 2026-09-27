"""Compose tracked writes with paired indexes and one exact counter CAS per period."""
from copy import deepcopy
import time
from . import records as R,configuration as C,targets


def key(row):return C.wire({'PK':row['PK'],'SK':row['SK']})

def operation(kind,table,row,*,observed=None):
    value={'TableName':table,'Item' if kind=='Put' else 'Key':C.wire(row) if kind=='Put' else key(row)}
    value.update(C.exact(observed) if observed is not None else {'ConditionExpression':'attribute_not_exists(PK)'})
    return {kind:value}


def protect(action,observed):
    value=next(iter(action.values()))
    guard=C.exact(observed) if observed is not None else {'ConditionExpression':'attribute_not_exists(PK)'}
    if value.get('ConditionExpression'):
        value['ConditionExpression']='('+value['ConditionExpression']+') AND ('+guard['ConditionExpression']+')'
    else:value['ConditionExpression']=guard['ConditionExpression']
    for field in ('ExpressionAttributeNames','ExpressionAttributeValues'):
        extra=guard.get(field,{})
        R.need(all(value.get(field,{}).get(k,v)==v for k,v in extra.items()))
        if extra:value.setdefault(field,{}).update(extra)
    return action


class TrackedClient:
    def __init__(self,client,*,registries=(),now=lambda:int(time.time()),remaining_ms=lambda:30000,allowed_periods=None):
        self.allowed_periods=allowed_periods
        self.client=client;self.registries={int(row['periodId']):row for row in registries}
        self.now=now;self.remaining=remaining_ms

    def __getattr__(self,name):return getattr(self.client,name)

    def call(self,method,**kwargs):
        R.need(self.remaining()>=6000)
        return method(**kwargs)

    def read(self,table,rowkey):
        raw=self.call(self.client.get_item,TableName=table,Key=C.wire(rowkey),ConsistentRead=True).get('Item')
        return C.plain(raw) if raw else None

    def transact_write_items(self,**kwargs):
        from shared_campaign_locators import period as P
        config=C.pins();now=R.integer(self.now(),1)
        C.binding(self.client,config,self.remaining)
        pipeline=config['resources']['pipeline']['tableName']
        marker=C.validate_marker(self.read(pipeline,{'PK':'INVENTORY#'+config['environment'],'SK':'CAMPAIGN_PERIOD_WORK'}),config,now)
        tables={row['tableName']:family for family,row in config['resources'].items()}
        actions=deepcopy(kwargs.get('TransactItems'))
        R.need(type(actions) is list and 1<=len(actions)<=100)
        groups={};extra=[];seen=set()
        for action in actions:
            R.need(type(action) is dict and len(action)==1)
            kind,value=next(iter(action.items()));table=value.get('TableName')
            if kind=='ConditionCheck' or table not in tables:continue
            R.need(kind in ('Put','Delete','Update'))
            target_key=C.plain(value.get('Item' if kind=='Put' else 'Key',{}))
            pk,sk=target_key.get('PK'),target_key.get('SK');family=tables[table]
            # Internal protocol metadata is written only by its dedicated paths.
            if family=='pipeline' and type(pk) is str and pk.startswith(('PERIOD#','PERIOD_WORK#','PERIOD_WORK_CONTROL#','WORK_LOOKUP#','INVENTORY#','PERIOD_SWEEP#','PERIOD_RETIRED_PREFIX#')):
                continue
            R.target(family,pk,sk);R.need((table,pk,sk) not in seen);seen.add((table,pk,sk))
            target_key={'PK':pk,'SK':sk}
            observed=self.read(table,target_key)
            lookup_key=R.lookup_key(family,pk,sk);lookup=self.read(pipeline,lookup_key)
            work=None
            if lookup is not None:
                R.need(type(lookup) is dict and set(lookup)==R.LOOKUP_FIELDS)
                period=R.integer(lookup['periodId']);ordinal=R.integer(lookup['ordinal'],1)
                work=self.read(pipeline,R.work_key(period,ordinal))
                R.validate_pair(work,lookup,config['environment'],config['generation'],family=family,pk=pk,sk=sk)
                if observed is not None:R.need(targets.deadline(family,observed)==work['deadlineEpoch'])
            elif observed is not None:
                raise R.WorkUnavailable() # Existing unindexed target is never silently adopted.
            elif kind=='Delete':
                protect(action,None);extra.append({'ConditionCheck':{'TableName':pipeline,'Key':C.wire(lookup_key),'ConditionExpression':'attribute_not_exists(PK)'}})
                continue
            else:
                R.need(kind=='Put' and len(self.registries)==1)
                period=next(iter(self.registries))
            R.need(self.allowed_periods is None or period in self.allowed_periods)
            if period not in groups:
                registry=self.registries.get(period) or self.read(pipeline,{'PK':f'PERIOD#{period}','SK':'HMAC_KEY'})
                P.validate(registry,period,marker['locatorManifestSha256'],int(marker['locatorInventoryRevision']),now,
                           states=('OPEN','CLOSING','DRAINING'))
                R.need(registry.get('workSchemaVersion')==1 and registry.get('workManifestSha256')==config['manifest']
                       and registry.get('workInventoryRevision')==config['revision'] and period>=marker['minimumPeriodId'])
                control=R.validate_control(self.read(pipeline,R.control_key(period)),config['environment'],config['generation'],
                                           period,config['manifest'],config['revision'],now)
                groups[period]={'registry':registry,'before':control,'next':deepcopy(control)}
            group=groups[period];state=group['next'];registry=group['registry']
            R.need(registry['admissionState']!='DRAINING' or kind=='Delete')
            protect(action,observed)
            if kind=='Delete':
                R.need(work is not None and state['pendingCount']>0)
                extra.extend([operation('Delete',pipeline,work,observed=work),operation('Delete',pipeline,lookup,observed=lookup)])
                state['pendingCount']-=1;state['lastProgressAtEpoch']=now
                continue
            next_deadline=targets.deadline(family,target_key if kind!='Put' else C.plain(value['Item'])) if kind=='Put' else targets.updated_deadline(family,observed,value)
            if work is None:
                R.need(now<next_deadline and state['nextOrdinal']<R.MAX_ORDINAL)
                new_work,new_lookup=R.pair(config['environment'],config['generation'],period,state['nextOrdinal'],family,pk,sk,next_deadline)
                extra.extend([operation('Put',pipeline,new_work),operation('Put',pipeline,new_lookup)])
                state['nextOrdinal']+=1;state['pendingCount']+=1
            else:
                R.need(next_deadline<=work['deadlineEpoch'])
                if next_deadline<work['deadlineEpoch']:
                    extra.extend([operation('Put',pipeline,work|{'deadlineEpoch':next_deadline},observed=work),
                                  operation('Put',pipeline,lookup|{'deadlineEpoch':next_deadline},observed=lookup)])
                else:extra.extend([C.condition(pipeline,work),C.condition(pipeline,lookup)])
        # Merge only an identical existing registry guard; never duplicate item actions.
        for group in groups.values():
            registry_guard=P.condition(pipeline,group['registry'])
            matches=[a for a in actions if next(iter(a.values())).get('TableName')==pipeline
                     and next(iter(a.values())).get('Key')==registry_guard['ConditionCheck']['Key']]
            R.need(len(matches)<=1 and (not matches or 'ConditionCheck' in matches[0]))
            if matches:
                if matches[0]!=C.condition(pipeline,group['registry']):protect(matches[0],group['registry'])
            else:extra.append(registry_guard)
            before,state=group['before'],group['next'];R.need(before['revision']<R.MAX_ORDINAL)
            # Publication can pin the counter before reading every contributor.
            # Any intervening qualified tombstone/repair mutation changes this
            # revision; fold only the identical proof into our one counter CAS.
            control_matches=[a for a in actions if next(iter(a.values())).get('TableName')==pipeline
                             and next(iter(a.values())).get('Key')==key(before)]
            R.need(len(control_matches)<=1)
            if control_matches:
                R.need(control_matches[0]==C.condition(pipeline,before));actions.remove(control_matches[0])
            update={'TableName':pipeline,'Key':key(before),**C.exact(before),
                    'UpdateExpression':'SET revision=:revision, nextOrdinal=:next, pendingCount=:pending, lastProgressAtEpoch=:progress'}
            update['ExpressionAttributeValues'].update(C.wire({':revision':before['revision']+1,':next':state['nextOrdinal'],
                ':pending':state['pendingCount'],':progress':state['lastProgressAtEpoch']}))
            extra.append({'Update':update})
        if groups:extra.append(C.condition(pipeline,marker))
        combined=actions+extra
        R.need(len(combined)<=100)
        identities=[]
        for action in combined:
            item=next(iter(action.values()));row=item.get('Key',item.get('Item',{}))
            ident=(item['TableName'],repr(row.get('PK')),repr(row.get('SK')))
            R.need(ident not in identities);identities.append(ident)
        return self.call(self.client.transact_write_items,**(kwargs|{'TransactItems':combined}))
