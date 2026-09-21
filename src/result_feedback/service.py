"""One result-scoped private opinion, conditionally attached to an existing receipt."""
import json
from datetime import datetime,timezone
from decimal import Decimal
from shared_check_authority.core import AuthorityError,OWNER_POLICY,integral
from shared_message_contract.runtime import unique_pairs,url_mapper
from .validation import *


def plain(value):
    if isinstance(value,Decimal):
        require(value.is_finite() and value==value.to_integral_value(),'RESULT_UNAVAILABLE');return int(value)
    if type(value) is dict:return {k:plain(v) for k,v in value.items()}
    if type(value) is list:return [plain(v) for v in value]
    return value

class Feedback:
    def __init__(self,authority):self.a=authority

    def _receipt(self,account,ref):
        try:
            keyid,*_=self.a._token_parts(ref['operationProof'])
            key={'PK':self.a._partition(account,keyid),'SK':'CHECK#'+ref['operationProof']}
            row=self.a._get(self.a.s.authority_table,key)
            require(type(row) is dict and row.get('recordType')=='V1_CHECK_RECEIPT' and row.get('state')=='SETTLED'
                and row.get('policyVersion')==OWNER_POLICY and row.get('checkId')==ref['operationProof']
                and row.get('clientCheckId')==ref['checkId'] and row.get('receiptId')==ref['receiptId'],'RESULT_UNAVAILABLE')
            expiry=integral(row.get('retentionDeadlineEpoch'))
            require(expiry is not None and expiry>self.a.now() and integral(row.get('expiresAt'))==expiry,'RESULT_UNAVAILABLE')
            require(row.get('processingOutcome') in ELIGIBLE and row.get('resultSummary') is not None,'RESULT_UNAVAILABLE')
            assessed=integral(row.get('assessmentEpoch'));charged=integral(row.get('chargedChecks'))
            require(assessed is not None and 0<=assessed<=self.a.now() and assessed<expiry
                and row.get('basis') in ('paid','trial','complimentary')
                and charged==int(row['processingOutcome']=='complete' and row['basis']!='complimentary')
                and row.get('GSI1PK')=='V1_EXPIRING' and row.get('GSI1SK')==f'{expiry:012d}#{key["PK"]}#{key["SK"]}','RESULT_UNAVAILABLE')
            self.a._verify_token(account,ref['operationProof'],row['payloadHmac'],allow_expired=True)
            summary=plain(row['resultSummary']);scope=row.get('projectionScope');version=ref['assessmentTransportVersion']
            if scope=='sanitized_message':
                from shared_message_contract import VERSION as V1,validate_summary as validate_v1
                from shared_message_contract.validation_v2 import VERSION as V2,validate_summary as validate_v2
                require(version in (V1,V2) and row.get('messageTransportVersion',V1)==version and 'recoveryTransportVersion' not in row,'RESULT_UNAVAILABLE')
                (validate_v2 if version==V2 else validate_v1)(summary,ref['checkId'],row['processingOutcome'])
            else:
                from shared_check_authority.summary import validate_summary
                require(scope in ('full_url','origin_only') and version=='1.0.0-candidate.1'
                    and 'messageTransportVersion' not in row and 'recoveryTransportVersion' not in row,'RESULT_UNAVAILABLE')
                validate_summary(summary,ref['checkId'],row['processingOutcome'])
                # Reuse actual public URL rendering semantics, with a deliberately
                # unavailable access snapshot used only for internal validation.
                # No grant lookup and no fabricated access is returned to clients.
                stamp=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat().replace('+00:00','Z')
                access={'state':'temporarily_unavailable','basis':'unknown','externalChecksAllowed':False,
                    'builtInChecksAllowed':False,'reconciliationAllowed':False,'remainingChecks':None,
                    'resetsAt':None,'unlimited':False,'observedAt':stamp(self.a.now())}
                accounting={'state':'charged' if charged else 'not_charged','chargedChecks':charged,
                    'receiptId':row['receiptId'],'requiresReconciliation':False}
                mapped=url_mapper().map_private(summary,expected_check_id=ref['checkId'],request_scope=scope,
                    assessed_at=stamp(assessed),access=access,accounting=accounting,same_check_replay_authorized=False)
                require(mapped['kind']=='assessment_result' and mapped['processingOutcome'] in ELIGIBLE,'RESULT_UNAVAILABLE')
            return key,row
        except Exception:raise FeedbackError('RESULT_UNAVAILABLE') from None

    def _guards(self,account,device):
        fingerprint,version=device
        return self.a._account_conditions(account)+[
            self.a._check(self.a.s.devices_table,{'PK':'USER#'+account,'SK':'ACTIVE_BINDING'},
                'recordType = :type AND stateVersion = :version AND bindingFingerprint = :fingerprint',
                {':type':'ACTIVE_BINDING_POINTER',':version':version,':fingerprint':fingerprint}),
            self.a._check(self.a.s.devices_table,{'PK':'USER#'+account,'SK':'DEVICE#'+fingerprint},
                '#state = :active AND accountId = :account AND bindingFingerprint = :fingerprint',
                {':active':'ACTIVE',':account':account,':fingerprint':fingerprint},{'#state':'status'})]

    def _receipt_condition(self,key,row):
        condition={'TableName':self.a.s.authority_table,'Key':key,
            'ConditionExpression':'recordType = :type AND #state = :settled AND checkId = :proof AND clientCheckId = :client AND receiptId = :receipt AND payloadHmac = :digest AND policyVersion = :policy AND projectionScope = :scope AND processingOutcome = :outcome AND resultSummary = :summary AND retentionDeadlineEpoch = :expiry AND expiresAt = :expiry AND expiresAt > :now',
            'ExpressionAttributeNames':{'#state':'state','#feedback':'feedback'},
            'ExpressionAttributeValues':{':type':'V1_CHECK_RECEIPT',':settled':'SETTLED',':proof':row['checkId'],':client':row['clientCheckId'],':receipt':row['receiptId'],':digest':row['payloadHmac'],':policy':OWNER_POLICY,':scope':row['projectionScope'],':outcome':row['processingOutcome'],':summary':row['resultSummary'],':expiry':row['retentionDeadlineEpoch'],':now':self.a.now()}}
        for field in ('messageTransportVersion','recoveryTransportVersion','chargedChecks','assessmentEpoch','basis','GSI1PK','GSI1SK'):
            if field in row:
                condition['ConditionExpression']+=' AND '+field+' = :'+field
                condition['ExpressionAttributeValues'][':'+field]=row[field]
            else:condition['ConditionExpression']+=' AND attribute_not_exists('+field+')'
        return condition

    def _existing(self,value,expiry,assessed):
        value=plain(value)
        require(type(value) is dict and set(value)=={'feedbackId','category','receivedAt','policyVersion'}
            and matches(IDENTITY,value['feedbackId']) and type(value['category']) is str and value['category'] in CATEGORIES and value['policyVersion']==POLICY
            and type(value['receivedAt']) is int and assessed<=value['receivedAt']<=self.a.now() and value['receivedAt']<expiry,'SERVICE_UNAVAILABLE')
        return value

    def send(self,event,body,*,account=None):
        if account is None:
            account=self.a._account(event)
            self.a._attempt(account,self.a._partition(account,self.a.s.active_key_id))
        request(body);device=self.a._device(event,account);ref=body['result']
        key,row=self._receipt(account,ref);expiry=int(row['retentionDeadlineEpoch'])
        prior=row.get('feedback');existing=prior is not None
        stored=self._existing(prior,expiry,int(row['assessmentEpoch'])) if existing else {'feedbackId':body['feedbackId'],'category':body['category'],'receivedAt':self.a.now(),'policyVersion':POLICY}
        conflict=existing and stored['feedbackId']==body['feedbackId'] and stored['category']!=body['category']
        guards=self._guards(account,device)
        condition=self._receipt_condition(key,row)
        if existing:
            condition['ConditionExpression']+=' AND #feedback = :feedback'
            condition['ExpressionAttributeValues'][':feedback']=prior
            mutation={'ConditionCheck':condition}
        else:
            condition['ConditionExpression']+=' AND attribute_not_exists(#feedback)'
            condition['UpdateExpression']='SET #feedback = :feedback'
            condition['ExpressionAttributeValues'][':feedback']=stored
            mutation={'Update':condition}
        try:self.a._transact(guards+[mutation])
        except AuthorityError:
            # No stale-read ACK after a racing write/deletion/device switch.
            # Caller explicitly retries same scoped ID; confirmation then runs
            # the same transaction fences, without creating another opinion.
            raise FeedbackError('SERVICE_UNAVAILABLE') from None
        require(expiry>self.a.now(),'RESULT_UNAVAILABLE')
        if conflict:raise FeedbackError('FEEDBACK_ID_CONFLICT')
        status='already_received' if existing and stored['feedbackId']!=body['feedbackId'] else 'accepted'
        return response(status,feedback_id=body['feedbackId'],check_id=ref['checkId'],reason='FEEDBACK_ALREADY_RECEIVED' if status=='already_received' else None,received=stored['receivedAt'],expires=expiry)

    def handle(self,event):
        fid=check=None
        try:
            require(type(event) is dict and event.get('version')=='2.0' and event.get('routeKey')=='POST /v1/result-feedback'
                and event.get('requestContext',{}).get('http',{}).get('method')=='POST' and event.get('isBase64Encoded') is not True
                and not event.get('rawQueryString') and not event.get('queryStringParameters'))
            account=self.a._account(event)
            self.a._attempt(account,self.a._partition(account,self.a.s.active_key_id))
            raw=event.get('body');require(type(raw) is str and len(raw.encode('utf-8'))<=4096)
            body=json.loads(raw,object_pairs_hook=unique_pairs)
            if type(body) is dict:
                fid=body.get('feedbackId');ref=body.get('result');check=ref.get('checkId') if type(ref) is dict else None
            return 200,self.send(event,body,account=account)
        except (FeedbackError,AuthorityError) as error:code=error.code
        except (ValueError,TypeError,UnicodeError,RecursionError,AttributeError):code='INPUT_REJECTED'
        except Exception:code='SERVICE_UNAVAILABLE'
        if code not in REASONS:code='SERVICE_UNAVAILABLE'
        statuses={'AUTHENTICATION_REQUIRED':401,'ACCOUNT_UNAVAILABLE':403,'ACTIVE_DEVICE_REQUIRED':403,'INPUT_REJECTED':422,'CONTRACT_UNSUPPORTED':422,'RESULT_UNAVAILABLE':404,'FEEDBACK_ID_CONFLICT':409,'RATE_LIMITED':429}
        http=statuses.get(code,503);retry=code in ('SERVICE_UNAVAILABLE','RATE_LIMITED')
        return http,response('unknown' if http==503 else 'rejected',feedback_id=fid if matches(IDENTITY,fid) else None,check_id=check if matches(IDENTITY,check) else None,reason=code,retryable=retry,retry_after=self.a.s.attempt_window_seconds if code=='RATE_LIMITED' else None)
