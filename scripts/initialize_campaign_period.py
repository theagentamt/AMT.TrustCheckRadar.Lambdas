#!/usr/bin/env python3
"""Dev-only create-only registry initialization. Dry-run plan is the default.

The key must already exist under independent infrastructure ownership. No key
creation, retirement, deletion, inventory approval or runtime activation occurs.
"""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from shared_campaign_locators.core import deserialize, serialize, load_inventory, inventory_condition
from shared_campaign_locators.period import PERIOD_SECONDS, RECOVERY_SECONDS

ACCOUNT='107827791950'
REGION='us-east-1'
TABLE='trustcheckradar-dev-campaign-pipeline'
REQUEST_FIELDS={'periodId','keyArn','generation','locatorManifestSha256','locatorInventoryRevision'}
PLAN_FIELDS={'schemaVersion','operation','accountId','region','tableName','request','inventory','row'}
ERROR='CAMPAIGN_PERIOD_INITIALIZATION_UNAVAILABLE'


class Unavailable(RuntimeError):
    def __init__(self):super().__init__(ERROR)


def require(value):
    if not value:raise Unavailable()


def integer(value, minimum=0):
    require(type(value) is int and minimum<=value<=9007199254740991)
    return value


def plain(value):
    if type(value) is Decimal:
        require(value==int(value));return int(value)
    if type(value) is dict:return {k:plain(v) for k,v in value.items()}
    if type(value) is list:return [plain(v) for v in value]
    return value


def exact(left, right):
    return json.dumps(left,sort_keys=True,separators=(',',':'),allow_nan=False)==json.dumps(right,sort_keys=True,separators=(',',':'),allow_nan=False)


def request(value):
    require(type(value) is dict and set(value)==REQUEST_FIELDS)
    integer(value['periodId']);integer(value['locatorInventoryRevision'],1)
    require(type(value['keyArn']) is str and re.fullmatch(
        rf'arn:aws:kms:{REGION}:{ACCOUNT}:key/[0-9a-f]{{8}}(?:-[0-9a-f]{{4}}){{3}}-[0-9a-f]{{12}}',value['keyArn']))
    require(type(value['locatorManifestSha256']) is str and re.fullmatch('[0-9a-f]{64}',value['locatorManifestSha256']))
    try:
        parsed=UUID(value['generation'])
        require(parsed.version==4 and str(parsed)==value['generation'])
    except (ValueError,TypeError,AttributeError):raise Unavailable() from None
    return dict(value)


def row_for(value, changed_at):
    period=value['periodId'];integer(changed_at,1)
    return {'PK':f'PERIOD#{period}','SK':'HMAC_KEY','keyArn':value['keyArn'],'status':'ENABLED','periodId':period,
        'retireAfterEpoch':(period+1)*PERIOD_SECONDS+RECOVERY_SECONDS,'admissionSchemaVersion':1,
        'admissionGeneration':value['generation'],'admissionState':'OPEN','admissionRevision':1,
        'admissionManifestSha256':value['locatorManifestSha256'],
        'admissionInventoryRevision':value['locatorInventoryRevision'],'admissionChangedAtEpoch':changed_at}


def validate_plan(plan):
    require(type(plan) is dict and set(plan)==PLAN_FIELDS and type(plan['schemaVersion']) is int
        and plan['schemaVersion']==1 and plan['operation']=='initialize-campaign-period'
        and plan['accountId']==ACCOUNT and plan['region']==REGION and plan['tableName']==TABLE)
    value=request(plan['request'])
    require(type(plan['row']) is dict)
    expected=row_for(value,plan['row'].get('admissionChangedAtEpoch'))
    require(exact(plan['row'],expected) and type(plan['inventory']) is dict)
    require(expected['admissionChangedAtEpoch']//PERIOD_SECONDS==value['periodId'])
    require(integer(plan['inventory'].get('approvedAtEpoch'),1)<expected['admissionChangedAtEpoch'])
    # Booleans must never pass equality as integral row metadata.
    for field in ('periodId','retireAfterEpoch','admissionSchemaVersion','admissionRevision',
                  'admissionInventoryRevision','admissionChangedAtEpoch'):integer(plan['row'].get(field))
    return value


class Initializer:
    def __init__(self,ddb,kms,sts,*,now=lambda:int(time.time())):
        self.ddb,self.kms,self.sts,self.now=ddb,kms,sts,now
        self.last=0

    def clock(self, value, minimum=0):
        now=integer(self.now(),1)
        require(now>=self.last and now>=minimum and now//PERIOD_SECONDS==value['periodId'])
        self.last=now
        return now

    def identity(self):
        require(self.sts.get_caller_identity().get('Account')==ACCOUNT)
        for client in (self.ddb,self.kms,self.sts):
            require(client.meta.region_name==REGION)

    def key(self,value):
        self.clock(value)
        observed=self.kms.describe_key(KeyId=value['keyArn'])['KeyMetadata']
        require(observed.get('Arn')==value['keyArn'] and observed.get('KeyState')=='Enabled'
            and observed.get('KeySpec')=='HMAC_256' and observed.get('KeyUsage')=='GENERATE_VERIFY_MAC'
            and observed.get('KeyManager')=='CUSTOMER')
        self.clock(value)
        tags=self.kms.list_resource_tags(KeyId=value['keyArn'],Limit=50)
        require(not tags.get('Truncated') and not tags.get('NextMarker') and type(tags.get('Tags')) is list)
        expected={'Project':'trustcheckradar','Environment':'dev','Purpose':'campaign-contributor-token','PeriodId':str(value['periodId'])}
        require(len(tags['Tags'])==len(expected) and all(type(t) is dict and set(t)=={'TagKey','TagValue'} for t in tags['Tags']))
        require({t['TagKey']:t['TagValue'] for t in tags['Tags']}==expected)
        self.clock(value)

    def inventory(self,value):
        now=self.clock(value)
        observed=plain(load_inventory(self.ddb,TABLE,'dev',value['locatorManifestSha256'],value['locatorInventoryRevision'],now))
        require(observed['minimumPeriodId']<=value['periodId'])
        self.clock(value)
        return observed

    def current(self,value):
        self.clock(value)
        observed=self.ddb.get_item(TableName=TABLE,Key=serialize({'PK':f"PERIOD#{value['periodId']}",'SK':'HMAC_KEY'}),ConsistentRead=True).get('Item')
        self.clock(value)
        return plain(deserialize(observed)) if observed else None

    def plan(self,arguments):
        value=request(arguments);self.clock(value);self.identity();self.key(value)
        inventory=self.inventory(value)
        require(self.current(value) is None)  # Existing rows require their own exact reviewed plan.
        return {'schemaVersion':1,'operation':'initialize-campaign-period','accountId':ACCOUNT,'region':REGION,
            'tableName':TABLE,'request':value,'inventory':inventory,'row':row_for(value,self.clock(value))}

    def apply(self,plan):
        value=validate_plan(plan)
        self.clock(value,plan['row']['admissionChangedAtEpoch']);self.identity();self.key(value)
        require(exact(self.inventory(value),plan['inventory']))
        existing=self.current(value)
        require(existing is None or exact(existing,plan['row']))
        if existing is None:
            action={'Put':{'TableName':TABLE,'Item':serialize(plan['row']),
                'ConditionExpression':'attribute_not_exists(PK) AND attribute_not_exists(SK)'}}
        else:
            # Never rewrite an existing row, even on duplicate invocation.
            from shared_campaign_locators.period import condition
            action=condition(TABLE,existing)
        self.clock(value)
        try:
            self.ddb.transact_write_items(TransactItems=[inventory_condition(TABLE,plan['inventory']),action])
        except Exception:
            # Uncertain commits are never repeated automatically. A caller may
            # retry this exact plan; current row alone cannot prove inventory CAS.
            raise Unavailable() from None
        # Report only after a fresh proof: changed rows, inventory/key races or
        # period rollover yield unavailable, with no destructive compensation.
        require(exact(self.current(value),plan['row']))
        require(exact(self.inventory(value),plan['inventory']));self.key(value)
        self.clock(value,plan['row']['admissionChangedAtEpoch'])
        return {'schemaVersion':1,'initialized':True,'alreadyPresent':existing is not None,
            'periodId':value['periodId'],'runtimeActivated':False,'historicalCoverageApproved':False}


def _pairs(values):
    result={}
    for key,value in values:
        require(key not in result);result[key]=value
    return result


def read_plan(path):
    data=Path(path).read_bytes();require(len(data)<=16384)
    value=json.loads(data.decode('utf-8'),object_pairs_hook=_pairs,parse_constant=lambda _:(_ for _ in ()).throw(Unavailable()))
    validate_plan(value);return value


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan-out');mode.add_argument('--apply-plan')
    parser.add_argument('--period-id',type=int);parser.add_argument('--key-arn');parser.add_argument('--generation')
    parser.add_argument('--locator-manifest-sha256');parser.add_argument('--locator-inventory-revision',type=int)
    args=parser.parse_args(argv)
    clients=[]
    try:
        arguments={'periodId':args.period_id,'keyArn':args.key_arn,'generation':args.generation,
            'locatorManifestSha256':args.locator_manifest_sha256,'locatorInventoryRevision':args.locator_inventory_revision}
        if args.apply_plan:
            require(all(v is None for v in arguments.values()));plan=read_plan(args.apply_plan)
        else:request(arguments)
        import boto3
        from botocore.config import Config
        cfg=Config(connect_timeout=2,read_timeout=3,retries={'total_max_attempts':1})
        for name in ('dynamodb','kms','sts'):clients.append(boto3.client(name,region_name=REGION,config=cfg))
        tool=Initializer(*clients)
        if args.apply_plan:result=tool.apply(plan)
        else:
            plan=tool.plan(arguments);payload=(json.dumps(plan,indent=2,sort_keys=True)+'\n').encode()
            with open(args.plan_out,'xb') as out:out.write(payload)
            result={'schemaVersion':1,'dryRun':True,'planSha256':hashlib.sha256(payload).hexdigest(),
                'periodId':arguments['periodId'],'runtimeActivated':False,'historicalCoverageApproved':False}
        print(json.dumps(result,sort_keys=True));return 0
    except Exception:
        print(json.dumps({'schemaVersion':1,'ok':False,'code':ERROR},sort_keys=True));return 1
    finally:
        for client in clients:client.close()


if __name__=='__main__':raise SystemExit(main())
