"""Default-disabled, bounded aggregate publication and paired cleanup recovery."""
from shared_research_consent import CURRENT_NOTICE, CURRENT_POLICY
from shared_campaign_locators import period as period_fence
from shared_campaign_locators.publication import verify_aggregate,phase as validate_phase
import hashlib
import json
from decimal import Decimal
from datetime import UTC, datetime
from uuid import UUID, uuid4

PERIOD_SECONDS = 14 * 86400
RECOVERY_SECONDS = 7 * 86400
MUTABLE_AGGREGATE_FIELDS = {'state','version','GSI1PK','GSI1SK'}


class PublicationUnavailable(RuntimeError):
    def __init__(self):
        super().__init__('Campaign publication recovery is unavailable')


def require(value):
    if not value:
        raise PublicationUnavailable()


def integer(value, minimum=0):
    require(type(value) in (int,Decimal) and value >= minimum and value == int(value))
    return int(value)


def _json(value):
    if isinstance(value,Decimal):
        return int(value) if value == int(value) else float(value)
    if isinstance(value,dict):
        return {k:_json(v) for k,v in value.items()}
    if isinstance(value,list):
        return [_json(v) for v in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(_json(value),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


class Publication:
    def __init__(self, *, client, pipeline, intelligence, environment, manifest_sha256,
                 inventory_revision, now, enabled=False, remaining_ms=lambda:30000):
        require(type(enabled) is bool)
        self.enabled = enabled
        self.d, self.pipeline, self.intelligence = client,pipeline,intelligence
        self.env, self.manifest, self.revision, self.now = environment,manifest_sha256,inventory_revision,now
        self.remaining = remaining_ms
        # Dependency import is deferred until enabled invocation, not constructor.
        self.locators = None

    def _key(self,pk,sk):
        return self.locators.serialize({'PK':pk,'SK':sk})

    def _get(self,table,pk,sk):
        require(self.remaining()>=6000)
        raw=self.d.get_item(TableName=table,Key=self._key(pk,sk),ConsistentRead=True).get('Item')
        return self.locators.deserialize(raw) if raw else None

    def _transaction(self,inventory,actions):
        require(self.remaining()>=6000)
        self.d.transact_write_items(TransactItems=[period_fence.condition(self.pipeline,self.period_record),self.locators.inventory_condition(self.pipeline,inventory),*actions])

    def _guard(self,candidate):
        return {'ConditionCheck':{'TableName':self.pipeline,'Key':self._key(candidate['PK'],'SUMMARY'),**self._match(candidate)}}

    def _match(self,candidate):
        fields=('version','researchNoticeVersion','researchPolicyVersion','lifecycleState','lifecycleOperationId','lifecycleStartedAtEpoch','lifecycleInventoryRevision','lifecycleAggregateDigest')
        values={name:candidate[name] for name in fields if name in candidate}
        expression=' AND '.join(f'#f{i} = :f{i}' for i in range(len(values)))
        if 'lifecycleState' not in candidate:
            expression+=' AND attribute_not_exists(lifecycleState)'
        return {'ConditionExpression':expression,'ExpressionAttributeNames':{f'#f{i}':name for i,name in enumerate(values)},
                'ExpressionAttributeValues':self.locators.serialize({f':f{i}':value for i,value in enumerate(values.values())})}

    def process(self,candidate_id):
        require(self.enabled)
        period_fence.configuration()
        from shared_campaign_locators import core
        self.locators=core
        try:
            parsed=UUID(candidate_id)
            require(parsed.version==4 and str(parsed)==candidate_id)
        except (ValueError,TypeError,AttributeError):
            raise PublicationUnavailable() from None
        require(self.env in ('dev','uat','prod') and self.remaining() >= 5000)
        now=self.now()
        inventory=core.load_inventory(self.d,self.pipeline,self.env,self.manifest,self.revision,now)
        candidate=self._get(self.pipeline,'CANDIDATE#'+candidate_id,'SUMMARY')
        if candidate is None:
            # A missing TTL-expired checkpoint does not certify paired cleanup.
            raise PublicationUnavailable()
        self._validate(candidate,candidate_id,now,inventory)
        require(self.remaining()>=6000)
        self.period_record=period_fence.read(self.d,self.pipeline,int(candidate['periodId']),self.manifest,self.revision,now,states=('CLOSING',))
        phase=candidate.get('lifecycleState')
        if phase is None:
            # A pre-existing aggregate with no atomic phase evidence requires
            # reviewed legacy migration, not guessing from partially erased input.
            require(self._get(self.intelligence,'CAMPAIGN#'+candidate_id,'AGGREGATE') is None)
            update={'TableName':self.pipeline,'Key':self._key(candidate['PK'],'SUMMARY'),**self._match(candidate),
                'UpdateExpression':'SET #version = :next, lifecycleState = :phase, lifecycleOperationId = :operation, lifecycleStartedAtEpoch = :now, lifecycleInventoryRevision = :inventory'}
            update['ExpressionAttributeNames']['#version']='version'
            update['ExpressionAttributeValues'].update(core.serialize({':next':candidate['version']+1,':phase':'FROZEN',':operation':str(uuid4()),':now':now,':inventory':inventory['revision']}))
            self._transaction(inventory,[{'ConditionCheck':{'TableName':self.pipeline,'Key':self._key(candidate['PK'],'DELETION_RECOMPUTE'),'ConditionExpression':'attribute_not_exists(PK)'}},{'Update':update}])
            return {'state':'FROZEN','deleted':0,'complete':False}
        require(candidate['lifecycleInventoryRevision']==inventory['revision'])
        if phase=='REPAIRING':
            # Owned deletion repair preserves the original publication clock.
            return {'state':'REPAIRING','deleted':0,'complete':False}
        if phase=='FROZEN':
            return self._publish(candidate,inventory,now)
        require(phase in ('PUBLISHED','SUPPRESSED'))
        if phase=='PUBLISHED':
            self._published(candidate)
        else:
            require(candidate.get('lifecycleAggregateDigest')==digest({}))
        return self._cleanup(candidate,inventory)

    def _validate(self,candidate,identifier,now,inventory):
        from service import TAXONOMY_BUCKETS
        require(candidate.get('researchNoticeVersion') == CURRENT_NOTICE and candidate.get('researchPolicyVersion') == CURRENT_POLICY)
        require(candidate.get('PK')=='CANDIDATE#'+identifier and candidate.get('SK')=='SUMMARY'
                and candidate.get('candidateId')==identifier and candidate.get('taxonomyBucket') in TAXONOMY_BUCKETS
                and candidate.get('GSI2PK')==f"PERIOD#{integer(candidate.get('periodId'))}#BUCKET#{candidate['taxonomyBucket']}"
                and candidate.get('GSI3PK')=='EXPIRY#'+self.env)
        integer(candidate.get('version'),1);integer(candidate.get('expiresAt'),1)
        require(integer(candidate['periodId']) >= inventory['minimumPeriodId']
                and now >= (candidate['periodId']+1)*PERIOD_SECONDS+RECOVERY_SECONDS)
        if 'lifecycleState' in candidate:
            from shared_campaign_locators.core import LocatorUnavailable
            try:validate_phase(candidate,now)
            except LocatorUnavailable:raise PublicationUnavailable() from None
            integer(candidate.get('lifecycleInventoryRevision'),1)
            require(integer(candidate.get('lifecycleStartedAtEpoch'),1) <= now)
            try:
                op=UUID(candidate.get('lifecycleOperationId'))
                require(op.version==4 and str(op)==candidate['lifecycleOperationId'])
            except (ValueError,TypeError,AttributeError):
                raise PublicationUnavailable() from None

    def _contributions(self,candidate,limit=100,max_pages=5):
        rows=[];cursor=None
        for _ in range(max_pages):
            require(self.remaining()>=5000)
            args={'TableName':self.pipeline,'KeyConditionExpression':'PK = :pk AND begins_with(SK, :prefix)',
                'ExpressionAttributeValues':self.locators.serialize({':pk':candidate['PK'],':prefix':'CONTRIB#'}),'ConsistentRead':True,'Limit':limit}
            if cursor:args['ExclusiveStartKey']=cursor
            page=self.d.query(**args)
            values=[self.locators.deserialize(row) for row in page.get('Items',[])]
            require(len(values)<=limit)
            for value in values:
                require(value.get('researchNoticeVersion') == CURRENT_NOTICE and value.get('researchPolicyVersion') == CURRENT_POLICY)
                require(value.get('PK')==candidate['PK'] and value.get('periodId')==candidate['periodId'])
                locator=self.locators.locator_for_target(value,self.env)
                self.locators.get_owned_locator(self.d,self.pipeline,locator)
            rows.extend(values);cursor=page.get('LastEvaluatedKey')
            if not cursor:return rows
        raise PublicationUnavailable()

    def _publish(self,candidate,inventory,now):
        from service import thresholded_dimension_ids,count_band
        rows=self._contributions(candidate)
        eligible=[row for row in rows if integer(row.get('expiresAt'),1)>now]
        publish=candidate['expiresAt']>now and len(eligible)>=10
        aggregate=None
        if publish:
            submissions=sum(min(3,integer(row.get('submissionCount'),1)) for row in eligible)
            aggregate={'PK':'CAMPAIGN#'+candidate['candidateId'],'SK':'AGGREGATE','campaignId':candidate['candidateId'],
                'schemaVersion':1,'taxonomyVersion':1,'categoryId':candidate['taxonomyBucket'],
                'periodWeek':datetime.fromtimestamp((integer(candidate['periodId'])+1)*PERIOD_SECONDS,UTC).strftime('%G-W%V'),
                'state':'PENDING_REVIEW','contributorCount':len(eligible),'submissionCount':submissions,'dimensionSchemaVersion':1,
                'languageIds':thresholded_dimension_ids(eligible,'languageId',10),
                'tacticIds':thresholded_dimension_ids(eligible,'signalIds',10,prefix='tactic.'),
                'channelIds':thresholded_dimension_ids(eligible,'signalIds',10,prefix='channel.'),
                'contributorCountBand':count_band(len(eligible)),'submissionCountBand':count_band(submissions),
                'riskBand':'high','summaryKey':'campaign.'+candidate['taxonomyBucket'],'trendDirection':'new',
                'expiresAt':candidate['lifecycleStartedAtEpoch']+400*86400,'version':1,'environment':self.env}
        phase='PUBLISHED' if publish else 'SUPPRESSED'
        immutable={} if aggregate is None else {k:v for k,v in aggregate.items() if k not in ('state','version')}
        update={'TableName':self.pipeline,'Key':self._key(candidate['PK'],'SUMMARY'),**self._match(candidate),
            'UpdateExpression':'SET lifecycleState = :phase, lifecycleAggregateDigest = :digest'}
        update['ExpressionAttributeValues'].update(self.locators.serialize({':phase':phase,':digest':digest(immutable)}))
        actions=[{'Update':update}]
        if aggregate:
            # Include every counted contributor. No partial/truncated publication
            # is allowed when the full absence proof exceeds DDB's transaction.
            partitions=[]
            for row in eligible:
                partition=row['GSI1PK']
                require(partition not in partitions)
                partitions.append(partition)
                require(self.remaining()>=6000)
                require(self._get(self.pipeline,partition,'TOMBSTONE') is None)
                actions.append({'ConditionCheck':{'TableName':self.pipeline,'Key':self._key(partition,'TOMBSTONE'),
                    'ConditionExpression':'attribute_not_exists(PK)'}})
            require(len(actions)+3<=100)
        if aggregate:
            actions.append({'Put':{'TableName':self.intelligence,'Item':self.locators.serialize(aggregate),
                                  'ConditionExpression':'attribute_not_exists(PK) AND attribute_not_exists(SK)'}})
        else:
            actions.append({'ConditionCheck':{'TableName':self.intelligence,'Key':self._key('CAMPAIGN#'+candidate['candidateId'],'AGGREGATE'),
                                              'ConditionExpression':'attribute_not_exists(PK)'}})
        self._transaction(inventory,actions)
        return {'state':phase,'deleted':0,'complete':False}

    def _published(self,candidate):
        aggregate=self._get(self.intelligence,'CAMPAIGN#'+candidate['candidateId'],'AGGREGATE')
        from shared_campaign_locators.core import LocatorUnavailable
        try:verify_aggregate(candidate,aggregate)
        except LocatorUnavailable:raise PublicationUnavailable() from None

    def _cleanup(self,candidate,inventory):
        args={'TableName':self.pipeline,'KeyConditionExpression':'PK = :pk AND begins_with(SK, :prefix)',
            'ExpressionAttributeValues':self.locators.serialize({':pk':candidate['PK'],':prefix':'CONTRIB#'}),'ConsistentRead':True,'Limit':10}
        page=self.d.query(**args)
        rows=[self.locators.deserialize(v) for v in page.get('Items',[])]
        require(len(rows)<=10)
        deleted=0
        for row in rows:
            if self.remaining()<5000:
                return {'state':candidate['lifecycleState'],'deleted':deleted,'complete':False}
            require(row.get('PK')==candidate['PK'] and row.get('periodId')==candidate['periodId'])
            locator=self.locators.get_owned_locator(self.d,self.pipeline,self.locators.locator_for_target(row,self.env))
            self._transaction(inventory,[self._guard(candidate),*self.locators.paired_delete_actions(self.pipeline,locator)])
            deleted+=1
        if rows or page.get('LastEvaluatedKey'):
            return {'state':candidate['lifecycleState'],'deleted':deleted,'complete':False}
        # Empty base-table pass under the frozen writer boundary; no claim about
        # legacy locators/backup copies/whole-account erasure is made here.
        self._transaction(inventory,[{'Delete':{'TableName':self.pipeline,'Key':self._key(candidate['PK'],'SUMMARY'),**self._match(candidate)}},
            {'ConditionCheck':{'TableName':self.pipeline,'Key':self._key(candidate['PK'],'DELETION_RECOMPUTE'),'ConditionExpression':'attribute_not_exists(PK)'}}])
        return {'state':candidate['lifecycleState'],'deleted':0,'complete':True}

    def expire_locator(self, partition, sort_key):
        """One owned expiration pair; never infer whole-scope absence from a GSI."""
        require(self.enabled)
        period_fence.configuration()
        from shared_campaign_locators import core
        self.locators = core
        require(self.env in ('dev', 'uat', 'prod') and self.remaining() >= 5000)
        require(type(partition) is str and len(partition) <= 96
                and type(sort_key) is str and len(sort_key) <= 80)
        now = self.now()
        inventory = core.load_inventory(self.d, self.pipeline, self.env,
                                        self.manifest, self.revision, now)
        raw = self._get(self.pipeline, partition, sort_key)
        # Missing locator is not proof that a legacy/partially migrated target
        # has disappeared. The caller must retain its unresolved work item.
        require(raw is not None)
        locator = core.validate_locator(raw, self.env, partition)
        require(locator['SK'] == sort_key
                and locator['periodId'] >= inventory['minimumPeriodId']
                and locator['targetExpiresAtEpoch'] <= now)
        require(self.remaining()>=6000)
        self.period_record=period_fence.read(self.d,self.pipeline,int(locator['periodId']),self.manifest,self.revision,now)
        actions = core.paired_delete_actions(self.pipeline, locator)
        if locator['targetKind'] == 'CONTRIBUTION':
            # Summary repair/publication owns live candidate mutations. An
            # orphan target after summary TTL can be erased only with absence
            # rechecked atomically; this does not certify candidate finalization.
            require(self._get(self.pipeline, locator['targetPK'], 'SUMMARY') is None)
            actions += [
                {'ConditionCheck': {'TableName': self.pipeline,
                    'Key': self._key(locator['targetPK'], 'SUMMARY'),
                    'ConditionExpression': 'attribute_not_exists(PK)'}},
                {'ConditionCheck': {'TableName': self.pipeline,
                    'Key': self._key(locator['targetPK'], 'DELETION_RECOMPUTE'),
                    'ConditionExpression': 'attribute_not_exists(PK)'}},
            ]
        self._transaction(inventory, actions)
        return {'expiredPairs': 1, 'scopeComplete': False}
