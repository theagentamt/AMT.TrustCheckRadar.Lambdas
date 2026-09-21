"""One admission/evaluation/settlement; reconciliation never resends content."""
import json
import re
import time
from pathlib import Path
from decimal import Decimal
from jsonschema import Draft202012Validator, FormatChecker
from shared_check_authority.core import AuthorityError, TrustedWorkerContext
from shared_message_contract import VERSION, POLICY, validate_intent, validate_summary
from shared_message_contract.validation import MessageError
from shared_message_contract.runtime import unique_pairs, url_mapper
from message_evaluator.policy import result as limited_result
from url_consumer.service import Consumer as UrlConsumer, timestamp, plain

ROUTES={'POST /v1/message-checks/prepare','POST /v1/message-checks','POST /v1/message-checks/reconcile'}
CHECK=re.compile(r'[A-Za-z0-9_-]{1,64}')
PROOF=re.compile(r'v1_[A-Za-z0-9]{1,8}_[0-9a-f]{8}_[0-9a-f]{16}_[0-9a-f]{24}')


def schema_path(name):
    path=Path(__file__).parent/'contract'/name
    if not path.exists():path=Path(__file__).resolve().parents[2]/'contracts/message-consumer/1.0.0-candidate.1'/name
    return path


def validate_envelope(body):
    Draft202012Validator(json.loads(schema_path('response.schema.json').read_text()),format_checker=FormatChecker()).validate(body)
    if body['outcome'] is not None:
        validate_summary(body['outcome'],body['checkId'])
        if body['state']!='settled':raise ValueError()
        if body['accounting']['chargedChecks']==1 and body['outcome']['processingOutcome']!='complete':raise ValueError()
    return body


def unavailable_envelope(code='SERVICE_UNAVAILABLE'):
    return validate_envelope({'transportVersion':VERSION,'checkId':None,'operationProof':None,'state':'unknown',
        'access':{'state':'temporarily_unavailable','basis':'unknown','externalChecksAllowed':False,'builtInChecksAllowed':False,
                  'reconciliationAllowed':False,'remainingChecks':None,'resetsAt':None,'unlimited':False,'observedAt':timestamp(time.time())},
        'accounting':{'state':'unknown','chargedChecks':None,'receiptId':None,'requiresReconciliation':True},
        'outcome':None,'errorCode':code,'retryAfterSeconds':None,'expiresAt':None})


class Consumer:
    def __init__(self,authority,provider,refresh,budget,*,clock=time.monotonic,remaining_ms=lambda:29000):
        self.a=authority;self.provider=provider;self.refresh=refresh;self.budget=budget
        self.clock=clock;self.remaining=remaining_ms;self.mapper=url_mapper()

    def access(self,event):return UrlConsumer.access(self,event)

    def envelope(self,event,client,proof,state,*,row=None,error=None):
        access=self.access(event)
        accounting={'state':'not_started','chargedChecks':0,'receiptId':None,'requiresReconciliation':False}
        if state in ('unknown','pending'):
            accounting.update(state=state,chargedChecks=None,requiresReconciliation=True)
        elif row and row.get('state')=='SETTLED':
            charge=int(row['chargedChecks'])
            accounting.update(state='charged' if charge else 'not_charged',chargedChecks=charge,receiptId=row['receiptId'])
        outcome=None
        if row and row.get('resultSummary'):
            outcome=validate_summary(plain(row['resultSummary']),client)
        if row and row.get('state')=='SETTLED' and outcome is None and error is None:error='SERVICE_UNAVAILABLE'
        return validate_envelope({'transportVersion':VERSION,'checkId':client,'operationProof':proof,'state':state,
            'access':access,'accounting':accounting,'outcome':outcome,'errorCode':error,
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
            if type(body) is not dict or body.get('transportVersion')!=VERSION:raise AuthorityError('CONTRACT_UNSUPPORTED')
            client=body.get('checkId');proof=body.get('operationProof')
            if type(client) is not str or not CHECK.fullmatch(client):raise AuthorityError('INPUT_REJECTED')
            route=event['routeKey']
            if route.endswith('/reconcile'):
                if set(body)!={'transportVersion','checkId','operationProof'}:raise AuthorityError('INPUT_REJECTED')
                row=self.a.reconcile(event,proof,client_check_id=client,expected_scope='sanitized_message')
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
            self.refresh(event)
            if prepare:
                proof=self.a.prepare(event,intent,client,client_check_id=client,count_attempt=False)
                row=self.a.reconcile(event,proof,client_check_id=client,expected_scope='sanitized_message')
                if row['state']=='NOT_STARTED':
                    return 200,self.envelope(event,client,proof,'rejected',row=row,error='OPERATION_EXPIRED')
                if row['state'] in ('ADMITTED','SETTLED'):
                    state='pending' if row['state']=='ADMITTED' else 'settled'
                    return (202 if state=='pending' else 200),self.envelope(event,client,proof,state,row=row)
                return 200,self.envelope(event,client,proof,'prepared',row={'expiresAt':self.a._token_parts(proof)[1]})
            admission=self.a.admit(event,intent,proof,client_check_id=client,count_attempt=False)
            if not admission['admitted']:
                row=admission['receipt'];state='settled' if row['state']=='SETTLED' else 'pending'
                return (200 if state=='settled' else 202),self.envelope(event,client,proof,state,row=row)
            outcome=None;window=None
            budget_ms=min(18000,int((25-(self.clock()-started)-5)*1000),int(self.remaining())-6000)
            try:
                window=self.budget.reserve() if budget_ms>=1000 else None
                if window is None:outcome=limited_result(client,limits=['BUDGET_LIMIT'])
                else:
                    raw_result=self.provider({'schemaVersion':1,'checkId':client,'policyVersion':POLICY,'intent':intent,'executionBudgetMs':budget_ms})
                    outcome=validate_summary(raw_result,client)
            except Exception:
                outcome=limited_result(client,limits=['PROVIDER_UNAVAILABLE'])
            if window is not None and set(outcome['limitationCodes'])&{'PROVIDER_UNAVAILABLE','PROVIDER_RESPONSE_INVALID'}:
                try:self.budget.failed(window)
                except Exception:pass  # Attempt cap remains consumed even if failure accounting is unavailable.
            row=self.a.settle(TrustedWorkerContext(account),proof,admission['executionToken'],outcome['processingOutcome'],result_summary=outcome)
            return 200,self.envelope(event,client,proof,'settled',row=row)
        except (AuthorityError,MessageError) as error:
            code=error.code
            statuses={'AUTHENTICATION_REQUIRED':401,'ACCOUNT_UNAVAILABLE':403,'ACTIVE_DEVICE_REQUIRED':403,'EXTERNAL_ACCESS_UNAVAILABLE':403,
                      'ALLOWANCE_EXHAUSTED':403,'INPUT_REJECTED':422,'PRIVACY_REVIEW_REQUIRED':422,'CHECK_ID_CONFLICT':409,'OPERATION_EXPIRED':409,
                      'RATE_LIMITED':429,'OPERATION_PENDING':202,'CONTRACT_UNSUPPORTED':422}
            status=statuses.get(code,503)
            if code not in statuses:code='SERVICE_UNAVAILABLE'
            return status,self.envelope(event,client if type(client) is str and CHECK.fullmatch(client) else None,
                proof if type(proof) is str and PROOF.fullmatch(proof) else None,'unknown',error=code)
