"""Bounded authenticated prepare/submit/reconcile. No background provider replay."""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
import json
import re
import time
from shared_check_authority.core import AuthorityError, TrustedWorkerContext
from shared_check_authority.recovery import Recovery

VERSION='1.0.0-candidate.1'
ROUTES={'POST /v1/url-checks/prepare','POST /v1/url-checks','POST /v1/url-checks/reconcile'}


def plain(value):
    if isinstance(value,Decimal):
        if not value.is_finite() or value!=value.to_integral_value():raise ValueError()
        return int(value)
    if isinstance(value,dict):return {k:plain(v) for k,v in value.items()}
    if isinstance(value,list):return [plain(v) for v in value]
    return value

def validate_envelope(body):
    path=Path(__file__).parent/'transport_contract'/'response.schema.json'
    if not path.exists():path=Path(__file__).resolve().parents[2]/'contracts/url-consumer/1.0.0-candidate.1/response.schema.json'
    Draft202012Validator(json.loads(path.read_text()),format_checker=FormatChecker()).validate(body)
    outcome=body.get('outcome')
    if outcome is not None and (outcome['checkId']!=body['checkId'] or outcome['access']!=body['access'] or outcome['accounting']!=body['accounting']):raise ValueError('OUTCOME_IDENTITY_MISMATCH')
    return body

def unavailable_envelope():
    return validate_envelope({'transportVersion':VERSION,'checkId':None,'operationProof':None,'state':'unknown',
      'access':{'state':'temporarily_unavailable','basis':'unknown','externalChecksAllowed':False,'builtInChecksAllowed':False,'reconciliationAllowed':False,'remainingChecks':None,'resetsAt':None,'unlimited':False,'observedAt':timestamp(time.time())},
      'accounting':{'state':'unknown','chargedChecks':None,'receiptId':None,'requiresReconciliation':True},'outcome':None,'errorCode':'SERVICE_UNAVAILABLE','retryAfterSeconds':None,'expiresAt':None})

def timestamp(epoch): return datetime.fromtimestamp(epoch,timezone.utc).isoformat().replace('+00:00','Z')

def unique_pairs(pairs):
    result={}
    for k,v in pairs:
        if k in result: raise ValueError()
        result[k]=v
    return result


class Consumer:
    def __init__(self,authority,mapper,provider,refresh,*,clock=time.monotonic):
        self.a,self.mapper,self.provider,self.refresh,self.clock=authority,mapper,provider,refresh,clock

    def access(self,event):
        observed=timestamp(self.a.now())
        snapshot={'state':'temporarily_unavailable','basis':'unknown','externalChecksAllowed':False,'builtInChecksAllowed':False,
                  'reconciliationAllowed':False,'remainingChecks':None,'resetsAt':None,'unlimited':False,'observedAt':observed}
        try:
            account=self.a._account(event)
            snapshot.update(builtInChecksAllowed=True,reconciliationAllowed=True)
            try:self.a._device(event,account)
            except AuthorityError:
                snapshot.update(state='device_action_required');return snapshot
            grant,period=self.a._grant(self.a._partition(account,self.a.s.active_key_id),allow_exhausted=True)
            if period:
                remaining=max(0,int(period['limit']-period['usedChecks']-period['reservedChecks']))
                snapshot.update(state='allowed' if remaining else 'allowance_exhausted',basis=grant['basis'],externalChecksAllowed=remaining>0,
                                remainingChecks=remaining,resetsAt=timestamp(int(period['endEpoch'])))
            else:snapshot.update(state='allowed',basis='complimentary',externalChecksAllowed=True,unlimited=True)
        except AuthorityError as error:
            if error.code=='AUTHENTICATION_REQUIRED':snapshot.update(state='sign_in_required',basis='none')
            elif error.code=='EXTERNAL_ACCESS_UNAVAILABLE':snapshot.update(state='subscription_required',basis='none')
        self.mapper.validate('access.schema.json',snapshot)
        return snapshot

    def envelope(self,event,client,proof,state,*,row=None,error=None,outcome=None):
        access=self.access(event)
        accounting={'state':'not_started','chargedChecks':0,'receiptId':None,'requiresReconciliation':False}
        if state in ('pending','unknown'):
            accounting.update(state=state if state=='pending' else 'unknown',chargedChecks=None,requiresReconciliation=True)
        elif row and row.get('state')=='SETTLED':
            charged=int(row['chargedChecks'])
            accounting.update(state='charged' if charged else 'not_charged',chargedChecks=charged,receiptId=row['receiptId'])
        if row and row.get('resultSummary'):
            outcome=self.mapper.map_private(plain(row['resultSummary']),expected_check_id=client,request_scope=row['projectionScope'],
                    assessed_at=timestamp(int(row['assessmentEpoch'])),access=access,accounting=accounting,same_check_replay_authorized=False)
        if row and row.get('state')=='SETTLED' and not row.get('resultSummary') and error is None:
            error='SERVICE_UNAVAILABLE'
        return validate_envelope({'transportVersion':VERSION,'checkId':client,'operationProof':proof,'state':state,'access':access,'accounting':accounting,
                'outcome':outcome,'errorCode':error,'retryAfterSeconds':self.a.s.attempt_window_seconds if error=='RATE_LIMITED' else (2 if state=='pending' else None),
                'expiresAt':int(row['expiresAt']) if row and row.get('expiresAt') is not None else None})

    def handle(self,event):
        started=self.clock();client=proof=None;admission_uncertain=False
        try:
            if not isinstance(event,dict) or event.get('version')!='2.0' or event.get('routeKey') not in ROUTES or event.get('requestContext',{}).get('http',{}).get('method')!='POST' or event.get('isBase64Encoded') is True or event.get('rawQueryString') or event.get('queryStringParameters'):
                raise AuthorityError('INPUT_REJECTED')
            route=event['routeKey'];account=self.a._account(event)
            self.a._attempt(account,self.a._partition(account,self.a.s.active_key_id))
            raw=event.get('body')
            if not isinstance(raw,str) or len(raw.encode('utf-8'))>8192:raise AuthorityError('INPUT_REJECTED')
            try:body=json.loads(raw,object_pairs_hook=unique_pairs)
            except (ValueError,TypeError):raise AuthorityError('INPUT_REJECTED') from None
            if not isinstance(body,dict) or body.get('transportVersion')!=VERSION:raise AuthorityError('CONTRACT_UNSUPPORTED')
            client=body.get('checkId');proof=body.get('operationProof')
            if not isinstance(client,str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}',client):raise AuthorityError('INPUT_REJECTED')
            if route.endswith('/reconcile'):
                if set(body)!={'transportVersion','checkId','operationProof'}:raise AuthorityError('INPUT_REJECTED')
                row=self.a.reconcile(event,proof)
                if row.get('clientCheckId') not in (None,client):raise AuthorityError('CHECK_ID_CONFLICT')
                if row['state']=='ADMITTED':
                    try:row=self.a.recover_expired(event,proof)
                    except AuthorityError as error:
                        if error.code!='OPERATION_PENDING':raise
                state={'ADMITTED':'pending','SETTLED':'settled','UNKNOWN':'unknown'}[row['state']]
                return (202 if state=='pending' else 200),self.envelope(event,client,proof,state,row=row)
            request={k:v for k,v in body.items() if k not in ('transportVersion','operationProof')}
            try:self.mapper.validate('request.schema.json',request)
            except Exception:raise AuthorityError('INPUT_REJECTED') from None
            intent={k:request[k] for k in ('entryPoint','language','target')}
            self.refresh(event)
            if route.endswith('/prepare'):
                if proof is not None or 'operationProof' in body:raise AuthorityError('INPUT_REJECTED')
                proof=self.a.prepare(event,intent,client,client_check_id=client,count_attempt=False)
                prior=self.a.reconcile(event,proof)
                if prior['state'] in ('ADMITTED','SETTLED'):
                    state='pending' if prior['state']=='ADMITTED' else 'settled'
                    return (202 if state=='pending' else 200),self.envelope(event,client,proof,state,row=prior)
                result=self.envelope(event,client,proof,'prepared',row={'expiresAt':self.a._token_parts(proof)[1]})
                return 200,result
            admission_uncertain=True
            admitted=self.a.admit(event,intent,proof,client_check_id=client,count_attempt=False)
            if not admitted['admitted']:
                row=admitted['receipt'];state='settled' if row['state']=='SETTLED' else 'pending'
                return (200 if state=='settled' else 202),self.envelope(event,client,proof,state,row=row)
            budget_ms=min(18000,int((25-(self.clock()-started)-5)*1000))
            result=None
            if budget_ms>=14000:
                try:result=self.provider({'schemaVersion':1,'checkId':client,'url':intent['target']['url'],'scope':intent['target']['scope'],'executionBudgetMs':budget_ms})
                except Exception:pass
            outcome='failed'
            if self.mapper.supported_private(result,client,intent['target']['scope']):outcome=result['processingOutcome']
            else:result=None
            row=self.a.settle(TrustedWorkerContext(account),proof,admitted['executionToken'],outcome,result_summary=result)
            return 200,self.envelope(event,client,proof,'settled',row=row,error='SERVICE_UNAVAILABLE' if result is None else None)
        except AuthorityError as error:
            allowed={'AUTHENTICATION_REQUIRED','ACCOUNT_UNAVAILABLE','ACTIVE_DEVICE_REQUIRED','EXTERNAL_ACCESS_UNAVAILABLE','ALLOWANCE_EXHAUSTED','INPUT_REJECTED','CHECK_ID_CONFLICT','OPERATION_EXPIRED','RATE_LIMITED','OPERATION_PENDING','CONTRACT_UNSUPPORTED'}
            code=error.code if error.code in allowed else 'SERVICE_UNAVAILABLE'
            status={'AUTHENTICATION_REQUIRED':401,'ACCOUNT_UNAVAILABLE':403,'ACTIVE_DEVICE_REQUIRED':403,'EXTERNAL_ACCESS_UNAVAILABLE':403,'ALLOWANCE_EXHAUSTED':403,'INPUT_REJECTED':422,'CHECK_ID_CONFLICT':409,'OPERATION_EXPIRED':409,'RATE_LIMITED':429,'OPERATION_PENDING':202,'CONTRACT_UNSUPPORTED':422}.get(code,503)
            state='unknown'  # An error never proves a prior logical check was uncharged.
            return status,self.envelope(event,client if isinstance(client,str) and re.fullmatch('[A-Za-z0-9_-]{1,64}',client) else None,proof if isinstance(proof,str) and re.fullmatch('v1_[A-Za-z0-9]{1,8}_[0-9a-f]{8}_[0-9a-f]{16}_[0-9a-f]{24}',proof) else None,state,error=code)
