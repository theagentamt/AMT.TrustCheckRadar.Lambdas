"""Reviewed-plan-only operator helper. Default validates locally; never prints identities/secrets."""
import argparse, base64, hashlib, http.client, json, os, re, secrets, time, tempfile
from pathlib import Path
from uuid import UUID, uuid4

ACCOUNT='107827791950'; REGION='us-east-1'
USERS='trustcheckradar-dev-users'; LEDGER='trustcheckradar-dev-deletion-ledger'
WRITER='trustcheckradar-dev-post-confirmation'; API='trustcheckradar-dev-account-data-api'
HOST='api-dev.andmorethings.net'; PATH='/v1/users/account-deletion'
PLAN_FIELDS={'schemaVersion','runId','poolId','clientId','poolSettingsSha256','clientSettingsSha256','writerCodeSha256','writerRevisionId','apiCodeSha256','apiRevisionId'}
JOURNAL_FIELDS={'schemaVersion','runId','poolId','clientId','email','operationId','startedAtEpoch','subject','stage'}
STAGES={'INTENT','CREATED','PROFILE_READY','POST_ATTEMPTED','ACCEPTED'}
class Refused(RuntimeError): pass
def need(value):
    if not value: raise Refused('QUALIFICATION_UNVERIFIED')
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def canonical(value):
    try:return isinstance(value,str) and str(UUID(value))==value
    except ValueError:return False
def pairs(values):
    result={}
    for k,v in values:
        need(k not in result);result[k]=v
    return result
def read_json(path):
    raw=Path(path).read_bytes();need(len(raw)<=65536)
    return json.loads(raw,object_pairs_hook=pairs)
def validate_plan(p):
    need(isinstance(p,dict) and set(p)==PLAN_FIELDS and type(p['schemaVersion']) is int and p['schemaVersion']==1)
    need(isinstance(p['runId'],str) and re.fullmatch('[0-9a-f]{12}',p['runId']))
    need(isinstance(p['poolId'],str) and re.fullmatch(REGION+'_[A-Za-z0-9]+',p['poolId']))
    need(isinstance(p['clientId'],str) and re.fullmatch('[A-Za-z0-9]{10,64}',p['clientId']))
    for f in ('poolSettingsSha256','clientSettingsSha256'):need(isinstance(p[f],str) and re.fullmatch('[0-9a-f]{64}',p[f]))
    for f in ('writerCodeSha256','apiCodeSha256'):
        try:need(len(base64.b64decode(p[f],validate=True))==32)
        except (TypeError,ValueError):raise Refused('QUALIFICATION_UNVERIFIED') from None
    for f in ('writerRevisionId','apiRevisionId'):need(canonical(p[f]))
    return p

def pool_settings(pool):
    # Pin the complete returned settings except changing time/census metadata.
    return {k:v for k,v in pool.items() if k not in {'CreationDate','LastModifiedDate','EstimatedNumberOfUsers'}}
def client_settings(client):
    return {k:v for k,v in client.items() if k not in {'CreationDate','LastModifiedDate'}}
def attributes(user):
    values=user.get('UserAttributes',user.get('Attributes',[]));need(isinstance(values,list))
    return pairs((a['Name'],a['Value']) for a in values)
def http_call(method,token,body=None):
    conn=http.client.HTTPSConnection(HOST,timeout=10)
    try:
        payload=None if body is None else json.dumps(body,separators=(',',':'))
        conn.request(method,PATH,body=payload,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        response=conn.getresponse();raw=response.read(65537);need(len(raw)<=65536)
        return response.status,json.loads(raw,object_pairs_hook=pairs)
    finally:conn.close()


def monitor_revocation(cognito,http,token,*,operation_id,max_seconds=600,now=lambda:int(time.time()),monotonic=time.monotonic,sleep=time.sleep):
    """Observe only. Failure never converts the committed POST to a failure."""
    report={'httpAccepted':True,'monitorCompleted':False,'polls':0,'applicationGetDenied':False,'cognitoGetUserRejected':False,'erasureVerified':False}
    try:
        parts=token.split('.');need(len(parts)==3)
        claims=json.loads(base64.urlsafe_b64decode(parts[1]+'='*((-len(parts[1]))%4)))
        expiry=claims.get('exp');need(type(expiry) is int and now()<expiry)
        start=monotonic()
        while monotonic()-start<max_seconds and report['polls']<120 and now()<expiry-5:
            report['polls']+=1
            report['applicationGetDenied']=False;report['cognitoGetUserRejected']=False
            status,body=http('GET',token)
            if now()>=expiry-5 or monotonic()-start>=max_seconds:
                report['category']='OBSERVATION_BUDGET_EXHAUSTED';return report
            if status==401 and isinstance(body,dict) and isinstance(body.get('error'),dict) and body['error'].get('code')=='UNAUTHORIZED':report['applicationGetDenied']=True
            elif not (status==200 and isinstance(body,dict) and body.get('schemaVersion')==1 and body.get('operation')=='ACCOUNT_DELETION' and body.get('status')=='REQUESTED' and body.get('operationId')==operation_id):report['category']='HTTP_OBSERVATION_UNAVAILABLE';return report
            try:cognito.get_user(AccessToken=token)
            except Exception as err:
                code=getattr(err,'response',{}).get('Error',{}).get('Code')
                if code in ('NotAuthorizedException','UserNotFoundException'):report['cognitoGetUserRejected']=True
                else:report['category']='COGNITO_OBSERVATION_UNAVAILABLE';return report
            if now()>=expiry-5 or monotonic()-start>=max_seconds:
                report['category']='OBSERVATION_BUDGET_EXHAUSTED';return report
            if report['applicationGetDenied'] and report['cognitoGetUserRejected']:
                report['monitorCompleted']=True;report['category']='ACCESS_REJECTED';return report
            sleep(5)
        report['category']='OBSERVATION_BUDGET_EXHAUSTED';return report
    except Exception:
        report['category']='OBSERVATION_UNAVAILABLE';return report

class Harness:
    def __init__(self,plan,journal,*,sts,cognito,lam,ddb,http=http_call,now=lambda:int(time.time())):
        self.p=validate_plan(plan);self.path=Path(journal);self.sts=sts;self.c=cognito;self.l=lam;self.d=ddb;self.http=http;self.now=now
    def write(self,j,*,new=False):
        need(set(j)==JOURNAL_FIELDS)
        temporary=None
        if new:
            fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        else:
            need(not self.path.is_symlink() and self.path.stat().st_mode&0o077==0)
            fd,temporary=tempfile.mkstemp(prefix='.'+self.path.name+'.',dir=self.path.parent)
        try:
            need(os.fstat(fd).st_mode&0o077==0)
            with os.fdopen(fd,'w',closefd=False) as out:
                json.dump(j,out,separators=(',',':'));out.flush();os.fsync(fd)
            if temporary is not None:
                os.replace(temporary,self.path);temporary=None
            directory=os.open(self.path.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:
            os.close(fd)
            if temporary is not None:os.unlink(temporary)
    def journal(self):
        need(not self.path.is_symlink() and self.path.stat().st_mode&0o077==0)
        j=read_json(self.path);p=self.p
        need(set(j)==JOURNAL_FIELDS and j['schemaVersion']==1 and j['runId']==p['runId'] and j['poolId']==p['poolId'] and j['clientId']==p['clientId'])
        need(j['email']=='deletion-qual-'+p['runId']+'@example.invalid' and canonical(j['operationId']) and UUID(j['operationId']).version==4 and j['stage'] in STAGES)
        need(type(j['startedAtEpoch']) is int and 0<j['startedAtEpoch']<=self.now() and (j['subject'] is None or canonical(j['subject'])))
        return j
    def row(self,table,pk,sk):
        return self.d.get_item(TableName=table,Key={'PK':{'S':pk},'SK':{'S':sk}},ConsistentRead=True).get('Item')
    def preflight(self,http=False,subject=None):
        p=self.p;need(self.sts.get_caller_identity()['Account']==ACCOUNT)
        pool=self.c.describe_user_pool(UserPoolId=p['poolId'])['UserPool']
        need(pool['Id']==p['poolId'] and pool['Arn']==f'arn:aws:cognito-idp:{REGION}:{ACCOUNT}:userpool/'+p['poolId'])
        need(pool.get('UsernameAttributes')==['email'] and pool.get('MfaConfiguration')=='OFF')
        required={a['Name'] for a in pool.get('SchemaAttributes',[]) if a.get('Required')}
        need(required <= {'sub','email','given_name','family_name'})
        need(pool.get('LambdaConfig')=={'PostConfirmation':f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:'+WRITER})
        need(digest(pool_settings(pool))==p['poolSettingsSha256'])
        client=self.c.describe_user_pool_client(UserPoolId=p['poolId'],ClientId=p['clientId'])['UserPoolClient']
        need(client['UserPoolId']==p['poolId'] and client['ClientId']==p['clientId'] and not client.get('ClientSecret') and 'ALLOW_USER_PASSWORD_AUTH' in client.get('ExplicitAuthFlows',[]))
        need(digest(client_settings(client))==p['clientSettingsSha256'])
        for name in (USERS,LEDGER):
            table=self.d.describe_table(TableName=name)['Table']
            need(table['TableArn']==f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/'+name and table['TableStatus']=='ACTIVE')
        self.function(WRITER,p['writerCodeSha256'],p['writerRevisionId'])
        if http:
            config=self.function(API,p['apiCodeSha256'],p['apiRevisionId'])
            env=config.get('Environment',{}).get('Variables',{})
            need(env.get('APP_ENVIRONMENT')=='dev' and env.get('COGNITO_USER_POOL_ID')==p['poolId'] and env.get('COGNITO_APP_CLIENT_ID')==p['clientId'])
            need(env.get('ACCOUNT_DELETION_ENABLED')=='true' and env.get('ACCOUNT_IDENTITY_FINALIZER_ENABLED')=='true' and env.get('CAMPAIGN_RECOVERY_WRITES_ENABLED')=='true')
            need(json.loads(env.get('ACCOUNT_DELETION_HTTP_SUBJECTS_JSON','[]'))==[subject])
    def function(self,name,sha,revision):
        c=self.l.get_function_configuration(FunctionName=name)
        need(c['FunctionArn']==f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:'+name and c['CodeSha256']==sha and c['RevisionId']==revision and c['Runtime']=='python3.14' and c['Architectures']==['arm64'] and c['LastUpdateStatus']=='Successful')
        if name==WRITER:
            env=c.get('Environment',{}).get('Variables',{})
            resolved=env.get('USERS_TABLE_NAME') or env.get('TABLE_NAME') or (env.get('USERS_TABLE_ARN','').rsplit('/',1)[-1])
            need(resolved==USERS and env.get('DELETION_LEDGER_TABLE_NAME')==LEDGER)
            if env.get('USERS_TABLE_ARN'):need(env['USERS_TABLE_ARN']==f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/'+USERS)
        return c
    def user(self,j):
        need(canonical(j['subject']))
        u=self.c.admin_get_user(UserPoolId=j['poolId'],Username=j['subject']);a=attributes(u)
        need(u['Username']==j['subject']==a.get('sub') and a.get('email')==j['email'] and u.get('Enabled') is True)
        need(u.get('UserStatus') in {'FORCE_CHANGE_PASSWORD','CONFIRMED'})
        need(a.get('given_name')=='Deletion' and a.get('family_name')=='Qualification')
        return u,a
    def verify_profile(self,j):
        row=self.row(USERS,'USER#'+j['subject'],'PROFILE');need(row is not None)
        # Projection is genuine post-confirmation output, never operator-written.
        need(row.get('sub')=={'S':j['subject']} and row.get('email')=={'S':j['email']} and row.get('status')=={'S':'PENDING_AGE_GATE'} and row.get('ageVerified')=={'BOOL':False})
        return row
    def prepare(self):
        self.preflight();p=self.p
        need(not self.path.exists()) # Ambiguous create/profile attempts require manual read-only reconciliation.
        email='deletion-qual-'+p['runId']+'@example.invalid'
        try:self.c.admin_get_user(UserPoolId=p['poolId'],Username=email)
        except self.c.exceptions.UserNotFoundException:pass
        else:raise Refused('QUALIFICATION_UNVERIFIED')
        j={'schemaVersion':1,'runId':p['runId'],'poolId':p['poolId'],'clientId':p['clientId'],'email':email,'operationId':str(uuid4()),'startedAtEpoch':self.now(),'subject':None,'stage':'INTENT'}
        self.write(j,new=True)
        result=self.c.admin_create_user(UserPoolId=p['poolId'],Username=email,MessageAction='SUPPRESS',ForceAliasCreation=False,UserAttributes=[{'Name':'email','Value':email},{'Name':'given_name','Value':'Deletion'},{'Name':'family_name','Value':'Qualification'}])['User']
        a=attributes(result);need(canonical(a.get('sub')) and result.get('Username')==a['sub'])
        j['subject']=a['sub'];j['stage']='CREATED';self.write(j)
        _,a=self.user(j)
        need(self.row(USERS,'USER#'+j['subject'],'PROFILE') is None and self.row(LEDGER,'ACCOUNT#'+j['subject'],'ACCOUNT_DELETION') is None)
        self.function(WRITER,p['writerCodeSha256'],p['writerRevisionId'])
        event={'version':'1','region':REGION,'userPoolId':p['poolId'],'userName':j['subject'],'triggerSource':'PostConfirmation_ConfirmSignUp','request':{'userAttributes':{k:a[k] for k in ('sub','email','given_name','family_name')}},'response':{}}
        response=self.l.invoke(FunctionName=WRITER,InvocationType='RequestResponse',LogType='None',Payload=json.dumps(event).encode())
        need(response.get('StatusCode')==200 and not response.get('FunctionError'))
        stream=response['Payload']
        try:need(len(stream.read(65537))<=65536)
        finally:stream.close()
        self.function(WRITER,p['writerCodeSha256'],p['writerRevisionId']);self.user(j);self.verify_profile(j)
        j['stage']='PROFILE_READY';self.write(j)
        return {'stage':'PROFILE_READY','syntheticOnly':True,'nativeSignupTriggerQualified':False}
    def authenticate_http(self):
        j=self.journal();need(j['stage'] in {'PROFILE_READY','POST_ATTEMPTED','ACCEPTED'})
        if j['stage']=='ACCEPTED':return {'stage':'ACCEPTED','replayedJournal':True,'erasureVerified':False}
        self.preflight(http=True,subject=j['subject']);self.user(j)
        # Do not reset a password after deletion was submitted: observe via root-owned read-only reconciliation.
        need(j['stage']=='PROFILE_READY')
        self.verify_profile(j);need(self.row(LEDGER,'ACCOUNT#'+j['subject'],'ACCOUNT_DELETION') is None)
        password='Aa1!'+secrets.token_urlsafe(36)
        try:
            self.c.admin_set_user_password(UserPoolId=j['poolId'],Username=j['subject'],Password=password,Permanent=True)
            result=self.c.initiate_auth(ClientId=j['clientId'],AuthFlow='USER_PASSWORD_AUTH',AuthParameters={'USERNAME':j['email'],'PASSWORD':password})
        finally:password=None
        need(not result.get('ChallengeName') and 'AuthenticationResult' in result)
        token=result['AuthenticationResult'].get('AccessToken');need(isinstance(token,str) and len(token)<=16384)
        result=None
        try:
            who=self.c.get_user(AccessToken=token);need(who['Username']==j['subject'] and attributes(who).get('sub')==j['subject'])
            self.preflight(http=True,subject=j['subject'])
            status,body=self.http('GET',token)
            need(status==200 and body.get('status')=='NOT_REQUESTED')
            j['stage']='POST_ATTEMPTED';self.write(j)
            status,body=self.http('POST',token,{'schemaVersion':1,'action':'DELETE_ACCOUNT','operationId':j['operationId']})
            need(status==202 and body.get('schemaVersion')==1 and body.get('operation')=='ACCOUNT_DELETION' and body.get('operationId')==j['operationId'] and body.get('status')=='REQUESTED')
            j['stage']='ACCEPTED';self.write(j)
            print(json.dumps({'stage':'ACCEPTED','httpPost':202,'monitoring':True}),flush=True)
            observed=monitor_revocation(self.c,self.http,token,operation_id=j["operationId"])
            return {'stage':'ACCEPTED','httpGet':200,'httpPost':202,'erasureVerified':False,'nativeSignupTriggerQualified':False,**observed}
        finally:token=None

def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['prepare','authenticate-http']);parser.add_argument('--plan',required=True);parser.add_argument('--plan-sha256',required=True);parser.add_argument('--journal',required=True);parser.add_argument('--execute',action='store_true');a=parser.parse_args()
    raw=Path(a.plan).read_bytes();need(hashlib.sha256(raw).hexdigest()==a.plan_sha256);p=validate_plan(read_json(a.plan))
    if not a.execute:print(json.dumps({'validatedLocally':True,'awsCalls':0,'stage':a.stage}));return
    import boto3
    from botocore.config import Config
    session=boto3.Session(profile_name='trustcheckradar',region_name=REGION);cfg=Config(connect_timeout=3,read_timeout=15,retries={'total_max_attempts':1})
    clients={n:session.client(service,config=cfg) for n,service in [('sts','sts'),('cognito','cognito-idp'),('lam','lambda'),('ddb','dynamodb')]}
    try:
        harness=Harness(p,a.journal,**clients);out=harness.prepare() if a.stage=='prepare' else harness.authenticate_http();print(json.dumps(out))
    finally:
        for c in clients.values():c.close()
if __name__=='__main__':
    try:main()
    except Exception:print(json.dumps({'failed':True,'code':'QUALIFICATION_UNVERIFIED','reconcileJournalBeforeRetry':True}));raise SystemExit(2) from None
