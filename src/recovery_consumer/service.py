"""One admission/evaluation/settlement; reconciliation never resends content."""
import json
import re
import time
from pathlib import Path
from decimal import Decimal
from jsonschema import Draft202012Validator, FormatChecker
from shared_check_authority.core import AuthorityError, TrustedWorkerContext
from shared_recovery_contract.constants import VERSION, POLICY, PLAYBOOK, RecoveryError
from shared_recovery_contract.validation import validate_intent, validate_outcome, outcome as limited_outcome
from shared_recovery_contract.usage import validate_usage
from shared_message_contract.runtime import unique_pairs, url_mapper
from url_consumer.service import Consumer as UrlConsumer, timestamp, plain

ROUTES={'POST /v1/recovery-clarifications/prepare','POST /v1/recovery-clarifications','POST /v1/recovery-clarifications/reconcile'}
CHECK=re.compile(r'[A-Za-z0-9_-]{1,64}')
PROOF=re.compile(r'v1_[A-Za-z0-9]{1,8}_[0-9a-f]{8}_[0-9a-f]{16}_[0-9a-f]{24}')


def schema_path(name):
    path=Path(__file__).parent/'contract'/name
    if not path.exists():path=Path(__file__).resolve().parents[2]/'contracts/recovery-consumer/1.0.0-candidate.1'/name
    return path


def validate_envelope(body):
    Draft202012Validator(json.loads(schema_path('response.schema.json').read_text()),format_checker=FormatChecker()).validate(body)
    if body['usage'] is not None:
        validate_usage(body['usage'],body['checkId'])
        if body['accounting']['chargedChecks']==1 and body['usage']['processingOutcome']!='complete':raise ValueError()
    if body['outcome'] is not None:
        validate_outcome(body['outcome'],body['checkId'],body['usage']['processingOutcome'])
    return body


def unavailable_envelope(code='SERVICE_UNAVAILABLE'):
    return validate_envelope({'transportVersion':VERSION,'checkId':None,'operationProof':None,'state':'unknown',
        'access':{'state':'temporarily_unavailable','basis':'unknown','externalChecksAllowed':False,'builtInChecksAllowed':False,
                  'reconciliationAllowed':False,'remainingChecks':None,'resetsAt':None,'unlimited':False,'observedAt':timestamp(time.time())},
        'accounting':{'state':'unknown','chargedChecks':None,'receiptId':None,'requiresReconciliation':True},
        'outcome':None,'usage':None,'resultAvailability':'none','errorCode':code,'retryAfterSeconds':None,'expiresAt':None})


class Consumer:
    def __init__(self,authority,provider,refresh,budget,*,clock=time.monotonic,remaining_ms=lambda:29000):
        self.a=authority;self.provider=provider;self.refresh=refresh;self.budget=budget
        self.clock=clock;self.remaining=remaining_ms;self.mapper=url_mapper()

    def access(self,event):return UrlConsumer.access(self,event)

    def envelope(self,event,client,proof,state,*,row=None,error=None,fresh_outcome=None):
        access=self.access(event)
        accounting={'state':'not_started','chargedChecks':0,'receiptId':None,'requiresReconciliation':False}
        if state in ('unknown','pending'):
            accounting.update(state=state,chargedChecks=None,requiresReconciliation=True)
        elif row and row.get('state')=='SETTLED':
            charge=int(row['chargedChecks'])
            accounting.update(state='charged' if charge else 'not_charged',chargedChecks=charge,receiptId=row['receiptId'])
        usage=None
        if row and row.get('resultSummary'):
            if row.get('recoveryTransportVersion')!=VERSION:raise AuthorityError('CHECK_ID_CONFLICT')
            usage=validate_usage(plain(row['resultSummary']),client)
        availability='pending' if state=='pending' else 'none'
        if state=='settled':
            if usage is None:raise AuthorityError('RESULT_SUMMARY_INVALID')
            availability='available' if fresh_outcome is not None else 'not_retained'
        return validate_envelope({'transportVersion':VERSION,'checkId':client,'operationProof':proof,'state':state,
            'access':access,'accounting':accounting,'outcome':fresh_outcome,'usage':usage,'resultAvailability':availability,'errorCode':error,
            'retryAfterSeconds':self.a.s.attempt_window_seconds if error=='RATE_LIMITED' else (2 if state=='pending' else None),
            'expiresAt':int(row['expiresAt']) if row and row.get('expiresAt') is not None else None})

    def handle(self,event):
        client=proof=None;started=self.clock()
        try:
            if (type(event) is not dict or event.get('version')!='2.0' or event.get('routeKey') not in ROUTES or
                    event.get('requestContext',{}).get('http',{}).get('method')!='POST' or event.get('isBase64Encoded') is True or
                    event.get('rawQueryString') or event.get('queryStringParameters')):raise AuthorityError('INPUT_REJECTED')
            account=self.a._account(event)
            self.a._attempt(account,self.a._partition(account,self.a.s.active_key_id))
            raw=event.get('body')
            if type(raw) is not str or len(raw.encode('utf-8'))>32768:raise AuthorityError('INPUT_REJECTED')
            try:body=json.loads(raw,object_pairs_hook=unique_pairs)
            except Exception:raise AuthorityError('INPUT_REJECTED') from None
            if type(body) is not dict or body.get('transportVersion') != VERSION:raise AuthorityError('CONTRACT_UNSUPPORTED')
            client=body.get('checkId');proof=body.get('operationProof')
            if type(client) is not str or not CHECK.fullmatch(client):raise AuthorityError('INPUT_REJECTED')
            route=event['routeKey']
            if route.endswith('/reconcile'):
                if set(body)!={'transportVersion','checkId','operationProof'}:raise AuthorityError('INPUT_REJECTED')
                row=self.a.reconcile(event,proof,client_check_id=client,expected_scope='recovery_clarification',expected_recovery_version=VERSION)
                if row.get('clientCheckId') not in (None,client):raise AuthorityError('CHECK_ID_CONFLICT')
                if row['state']=='ADMITTED':
                    try:row=self.a.recover_expired(event,proof)
                    except AuthorityError as e:
                        if e.code!='OPERATION_PENDING':raise
                state={'ADMITTED':'pending','SETTLED':'settled','UNKNOWN':'unknown','NOT_STARTED':'rejected'}[row['state']]
                return (202 if state=='pending' else 200),self.envelope(event,client,proof,state,row=row,error='OPERATION_EXPIRED' if state=='rejected' else None)
            required={'transportVersion','checkId','entryPoint','language','target'}
            prepare=route.endswith('/prepare')
            if set(body)!=(required if prepare else required|{'operationProof'}):raise AuthorityError('INPUT_REJECTED')
            intent={k:body[k] for k in ('entryPoint','language','target')}
            validate_intent(intent)
            bound_intent=intent|{'recoveryTransportVersion':VERSION}
            self.refresh(event)
            if prepare:
                proof=self.a.prepare(event,bound_intent,client,client_check_id=client,count_attempt=False)
                row=self.a.reconcile(event,proof,client_check_id=client,expected_scope='recovery_clarification',expected_recovery_version=VERSION)
                if row['state']=='NOT_STARTED':
                    return 200,self.envelope(event,client,proof,'rejected',row=row,error='OPERATION_EXPIRED')
                if row['state'] in ('ADMITTED','SETTLED'):
                    state='pending' if row['state']=='ADMITTED' else 'settled'
                    return (202 if state=='pending' else 200),self.envelope(event,client,proof,state,row=row)
                return 200,self.envelope(event,client,proof,'prepared',row={'expiresAt':self.a._token_parts(proof)[1]})
            admission=self.a.admit(event,bound_intent,proof,client_check_id=client,count_attempt=False)
            if not admission['admitted']:
                row=admission['receipt'];state='settled' if row['state']=='SETTLED' else 'pending'
                return (200 if state=='settled' else 202),self.envelope(event,client,proof,state,row=row)
            result=None;window=None
            budget_ms=min(18000,int((25-(self.clock()-started)-5)*1000),int(self.remaining())-6000)
            try:
                window=self.budget.reserve() if budget_ms>=1000 else None
                if window is None:result=limited_outcome(client,'unavailable','BUDGET_LIMIT')
                else:
                    raw_result=self.provider({'schemaVersion':1,'checkId':client,'policyVersion':POLICY,'playbookVersion':PLAYBOOK,'intent':intent,'executionBudgetMs':budget_ms})
                    result=validate_outcome(raw_result,client)
            except Exception:
                result=limited_outcome(client,'unavailable','PROVIDER_UNAVAILABLE')
            if window is not None and result['reasonCode'] in {'PROVIDER_UNAVAILABLE','PROVIDER_RESPONSE_INVALID'}:
                try:self.budget.failed(window)
                except Exception:pass
            row=self.a.settle(TrustedWorkerContext(account),proof,admission['executionToken'],result['processingOutcome'],result_summary=result)
            return 200,self.envelope(event,client,proof,'settled',row=row,fresh_outcome=result)
        except (AuthorityError,RecoveryError) as error:
            code=error.code
            statuses={'AUTHENTICATION_REQUIRED':401,'ACCOUNT_UNAVAILABLE':403,'ACTIVE_DEVICE_REQUIRED':403,'EXTERNAL_ACCESS_UNAVAILABLE':403,
                      'ALLOWANCE_EXHAUSTED':403,'INPUT_REJECTED':422,'PRIVACY_REVIEW_REQUIRED':422,'CHECK_ID_CONFLICT':409,'OPERATION_EXPIRED':409,
                      'RATE_LIMITED':429,'OPERATION_PENDING':202,'CONTRACT_UNSUPPORTED':422,'SERVICE_NOT_ENABLED':503}
            status=statuses.get(code,503)
            if code not in statuses:code='SERVICE_UNAVAILABLE'
            return status,self.envelope(event,client if type(client) is str and CHECK.fullmatch(client) else None,
                proof if type(proof) is str and PROOF.fullmatch(proof) else None,'unknown',error=code)
