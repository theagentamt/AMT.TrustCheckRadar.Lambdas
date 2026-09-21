"""Strong contributor traversal. Whole-account/campaign completion stays gated."""
from uuid import UUID
from shared_campaign_locators import owned_page,validate_locator,paired_delete_actions
from progress import get,key,wire,integer,require,recompute_page
from tombstone import validate as validate_tombstone


def _advance(ddb,table,tombstone,state,extra=()):
    revision=integer(tombstone.get('locatorCleanupRevision',0))
    values={':state':state,':next':revision+1,':deadline':tombstone['deletionDeadlineEpoch'],':schema':2}
    expression='schemaVersion = :schema AND deletionDeadlineEpoch = :deadline AND attribute_not_exists(expiresAt) AND '
    if revision:
        expression+='locatorCleanupRevision = :previous';values[':previous']=revision
    else:
        expression+='attribute_not_exists(locatorCleanupRevision)'
    action={'Update':{'TableName':table,'Key':key(tombstone['PK'],'TOMBSTONE'),
        'UpdateExpression':'SET locatorCleanupRevision = :next, locatorCleanupState = :state',
        'ConditionExpression':expression,'ExpressionAttributeValues':wire(values)}}
    ddb.transact_write_items(TransactItems=[*extra,action])


def sweep(ddb,table,environment,partition,operation_id,now,*,max_steps=10,remaining_ms=None):
    require(type(max_steps) is int and 1<=max_steps<=20)
    deleted=recomputed=0
    for _ in range(max_steps):
        if remaining_ms is not None and remaining_ms()<3000:break
        tomb=get(ddb,table,partition,'TOMBSTONE')
        validate_tombstone(tomb,environment,partition)
        state=tomb.get('locatorCleanupState')
        if state is None:
            _advance(ddb,table,tomb,{'operationId':operation_id,'phase':'SEEK','cursor':None})
            continue
        require(type(state) is dict and state.get('phase') in ('SEEK','DELETE','RECOMPUTE'))
        try:
            operation=UUID(state.get('operationId'))
            require(operation.version==4 and str(operation)==state['operationId'])
        except (ValueError,TypeError,AttributeError):
            require(False)
        phase=state['phase']
        require(set(state)==({'operationId','phase','cursor'} if phase=='SEEK' else {'operationId','phase','cursor','locator','nextCursor'}))
        require(state['cursor'] is None or (type(state['cursor']) is str and state['cursor'].startswith('LOCATOR#')))
        if phase!='SEEK':
            locator=validate_locator(state['locator'],environment,partition)
            require(state['nextCursor'] is None or (type(state['nextCursor']) is str and state['nextCursor'].startswith('LOCATOR#')))
        if state['operationId']!=operation_id:
            _advance(ddb,table,tomb,state|{'operationId':operation_id});continue
        if phase=='SEEK':
            items,cursor=owned_page(ddb,table,environment,partition,state['cursor'],limit=1)
            if not items:
                if cursor is not None:
                    _advance(ddb,table,tomb,state|{'cursor':cursor});continue
                # A final strongly consistent restart also detects any forbidden
                # insertion behind the cursor. This is not a migration approval.
                items,_=owned_page(ddb,table,environment,partition,None,limit=1)
                if items:
                    _advance(ddb,table,tomb,state|{'cursor':None});continue
                _advance(ddb,table,tomb,state|{'cursor':None})
                return {'deleted':deleted,'recomputedCandidates':recomputed,'locatorPassEnded':True}
            _advance(ddb,table,tomb,state|{'phase':'DELETE','locator':items[0],'nextCursor':cursor})
        elif phase=='DELETE':
            actions=paired_delete_actions(table,locator)
            if locator['targetKind']=='CONTRIBUTION':
                summary=get(ddb,table,locator['targetPK'],'SUMMARY')
                if summary:
                    version=integer(summary.get('version'),1)
                    require('lifecycleState' not in summary)
                    actions.append({'Update':{'TableName':table,'Key':key(locator['targetPK'],'SUMMARY'),
                        'UpdateExpression':'SET #v = :next','ConditionExpression':'#v = :v AND attribute_not_exists(lifecycleState)',
                        'ExpressionAttributeNames':{'#v':'version'},'ExpressionAttributeValues':wire({':v':version,':next':version+1})}})
                new=state|{'phase':'RECOMPUTE'}
            else:
                new={'operationId':operation_id,'phase':'SEEK','cursor':state['nextCursor']}
            _advance(ddb,table,tomb,new,actions)
            deleted+=1
        elif recompute_page(ddb,table,locator['targetPK'],now):
            _advance(ddb,table,tomb,{'operationId':operation_id,'phase':'SEEK','cursor':state['nextCursor']})
            recomputed+=1
    return {'deleted':deleted,'recomputedCandidates':recomputed,'locatorPassEnded':False}
