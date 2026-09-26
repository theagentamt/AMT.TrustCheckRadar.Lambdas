"""Separate disposable-pool identity fixture. Never imported by production handlers."""
import os
import re
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import campaign_qualification as Q

CASES = ('identity_complete', 'identity_delete_lost_ack')
HANDLER = 'cognito_qualification.lambda_handler'


def configuration(env, context, event):
    Q.require(type(event) is dict and set(event)=={'schemaVersion','operation','runId','case'})
    Q.require(event['operation']=='qualify-account-deletion-identity' and event['case'] in CASES)
    config=Q.configuration(env,context,event|{'operation':'qualify-campaign-completion','case':'handler_all_components'})
    pool=env.get('QUALIFICATION_COGNITO_POOL_ID','')
    subject=env.get('QUALIFICATION_COGNITO_SUBJECT','')
    Q.require(re.fullmatch(Q.REGION+r'_[A-Za-z0-9]+',pool) is not None)
    try: valid=str(UUID(subject))==subject and UUID(subject).version==4
    except (ValueError,TypeError,AttributeError):valid=False
    Q.require(valid)
    return config|{'pool':pool,'subject':subject,'handler':HANDLER}


class Identity:
    """Pool IAM is broader than a user: enforce exact designated identity per call."""
    def __init__(self, config, client, call, *, lose_delete=False):
        self.config,self.client,self.call=config,client,call
        self.calls=[]
        self.lose_delete=lose_delete

    def _check(self, kw):
        Q.require(kw=={'UserPoolId':self.config['pool'],'Username':self.config['subject']})

    def admin_user_global_sign_out(self, **kw):
        self._check(kw);self.calls.append('signout')
        return self.call(self.client.admin_user_global_sign_out,**kw)

    def admin_get_user(self, **kw):
        self._check(kw);self.calls.append('get')
        return self.call(self.client.admin_get_user,**kw)

    def admin_delete_user(self, **kw):
        self._check(kw);self.calls.append('delete')
        result=self.call(self.client.admin_delete_user,**kw)
        if self.lose_delete:
            self.lose_delete=False
            raise RuntimeError('SYNTHETIC_IDENTITY_ACK_LOSS')
        return result

    def preflight(self):
        c=self.config
        Q.require(self.client.meta.region_name==Q.REGION)
        pool=self.call(self.client.describe_user_pool,UserPoolId=c['pool'])['UserPool']
        Q.require(pool.get('Id')==c['pool'] and pool.get('Arn')==f"arn:aws:cognito-idp:{Q.REGION}:{Q.ACCOUNT}:userpool/{c['pool']}"
            and pool.get('Name')=='amt-campaign-completion-qual-'+c['run']+'-cognito'
            and pool.get('UserPoolTags')==c['tags'] and pool.get('UsernameAttributes')==['email']
            and not pool.get('LambdaConfig'))
        user=self.call(self.client.admin_get_user,UserPoolId=c['pool'],Username=c['subject'])
        attrs=user.get('UserAttributes')
        Q.require(user.get('Username')==c['subject'] and user.get('Enabled') is True
            and user.get('UserStatus') in ('CONFIRMED','FORCE_CHANGE_PASSWORD') and isinstance(attrs,list) and len(attrs)<=100
            and [a.get('Value') for a in attrs if isinstance(a,dict) and a.get('Name')=='sub']==[c['subject']])

    def verify_absent(self):
        try:self.call(self.client.admin_get_user,UserPoolId=self.config['pool'],Username=self.config['subject'])
        except ClientError as exc:
            Q.require(exc.response.get('Error',{}).get('Code')=='UserNotFoundException')
        else:raise Q.QualificationFailure('IDENTITY_STILL_PRESENT')


def execute(config, context, ddb, kms, cognito, case):
    runner=Q.Runner(config,ddb,kms,context)
    runner.subject=config['subject']
    runner.cmd=runner.cmd|{'PK':'ACCOUNT#'+runner.subject,'accountId':runner.subject}
    identity=Identity(config,cognito,runner.call,lose_delete=case=='identity_delete_lost_ack')
    # Identity and every storage resource are checked before fixture reset/seed.
    runner.preflight();identity.preflight()
    runner.identity_options={'identity':identity,'pool':config['pool'],'delete_ack_loss':case=='identity_delete_lost_ack'}
    runner.run('handler_all_components')
    identity.verify_absent()
    return {'schemaVersion':1,'case':case,'passed':True,'sourceSha':config['source'],
            'syntheticOnly':True,'actualIdentityDeleted':True,'historicalCoverageApproved':False,'productionActivation':False}


def lambda_handler(event, context):
    stage='configuration'
    clients=[]
    try:
        config=configuration(os.environ,context,event)
        stage='package';Q.verify_package(config)
        sdk=Config(connect_timeout=2,read_timeout=3,retries={'total_max_attempts':1})
        for name in ('dynamodb','kms','cognito-idp'):
            clients.append(boto3.client(name,region_name=Q.REGION,config=sdk))
        stage='identity_fixture'
        return execute(config,context,*clients,event['case'])
    except Exception as exc:
        category=('SDK_ACCESS_DENIED' if isinstance(exc,ClientError) and exc.response.get('Error',{}).get('Code') in
            ('AccessDenied','AccessDeniedException','UnauthorizedOperation','NotAuthorizedException') else
            'SDK_FAILURE' if isinstance(exc,ClientError) else 'FIXTURE_UNVERIFIED')
        # No provider exception, event, pool, subject, attribute or record output.
        return {'schemaVersion':1,'passed':False,'code':'QUALIFICATION_FAILED','failureStage':stage,'failureCategory':category,
                'syntheticOnly':True,'historicalCoverageApproved':False,'productionActivation':False}
    finally:
        for client in clients:
            try:client.close()
            except Exception:pass
