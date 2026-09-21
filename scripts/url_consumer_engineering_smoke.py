#!/usr/bin/env python3
"""Explicit synthetic Dev HTTP qualification; never prints tokens/proofs/raw bodies."""
import argparse
import http.client
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid
from urllib.parse import urlsplit

TRANSPORT='1.0.0-candidate.1'
CONTRACT='0.2.0-candidate.1'


class SmokeFailure(Exception):pass


def load_token(path=None):
    if path:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o600 or info.st_uid!=os.getuid() or info.st_size>16384:raise SmokeFailure('TOKEN_FILE_INVALID')
            value=os.read(fd,16385).decode().strip()
        finally:os.close(fd)
    else:value=os.environ.get('AMT_ENGINEERING_ACCESS_TOKEN','')
    if not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',value) or len(value)>16384:raise SmokeFailure('TOKEN_INVALID')
    return value


class Client:
    def __init__(self,base,token,fingerprint):
        parsed=urlsplit(base)
        host=parsed.hostname or ''
        if (parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443)
                or parsed.query or parsed.fragment or parsed.path not in ('','/')
                or not (host=='api-dev.andmorethings.net' or re.fullmatch(r'[a-z0-9]+\.execute-api\.us-east-1\.amazonaws\.com',host))):raise SmokeFailure('BASE_INVALID')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,255}',fingerprint):raise SmokeFailure('DEVICE_INVALID')
        self.host,self.token,self.fingerprint=host,token,fingerprint

    def call(self,method,path,payload=None,*,authenticated=True):
        headers={'Accept':'application/json','Content-Type':'application/json','x-device-binding-fingerprint':self.fingerprint}
        if authenticated:headers['Authorization']='Bearer '+self.token
        connection=http.client.HTTPSConnection(self.host,timeout=29)
        try:
            connection.request(method,path,body=json.dumps(payload) if payload is not None else None,headers=headers)
            response=connection.getresponse();raw=response.read(65537)
            if len(raw)>65536:raise SmokeFailure('RESPONSE_TOO_LARGE')
            # No redirects, automatic retry or raw error-body output.
            return response.status,json.loads(raw)
        except SmokeFailure:raise
        except Exception:raise SmokeFailure('HTTP_REQUEST_FAILED') from None
        finally:connection.close()


def expect(condition,code):
    if not condition:raise SmokeFailure(code)


def save_receipt(path,body):
    minimized={k:body[k] for k in ('checkId','operationProof','expiresAt')}
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:os.write(fd,json.dumps(minimized,separators=(',',':')).encode())
    finally:os.close(fd)


def run(client,receipt_file):
    checks=[]
    status,_=client.call('GET','/v1/access',authenticated=False)
    expect(status in (401,403),'UNAUTHENTICATED_ROUTE_NOT_REJECTED');checks.append('unauthenticated_rejected')
    status,snapshot=client.call('GET','/v1/access')
    expect(status==200 and snapshot.get('schemaVersion')==1 and snapshot.get('activeDevice') is True,'ACCESS_NOT_READY')
    expect(snapshot.get('access',{}).get('basis') in ('none','trial'),'SYNTHETIC_TRIAL_ONLY')
    status,trial=client.call('POST','/v1/access/trial',{'schemaVersion':1,'activate':True})
    expect(status==200 and trial.get('access',{}).get('basis')=='trial','TRIAL_ACTIVATION_FAILED')
    before=trial.get('allowance',{}).get('completedUsed')
    expect(type(before) is int and trial.get('allowance',{}).get('limit')==10,'TRIAL_ALLOWANCE_INVALID');checks.append('explicit_trial')
    check='engineering-'+uuid.uuid4().hex
    request={'transportVersion':TRANSPORT,'contractVersion':CONTRACT,'checkId':check,'entryPoint':'standalone_url','language':'en','target':{'url':'https://example.com/','scope':'full_url','withheldComponents':[]}}
    status,prepared=client.call('POST','/v1/url-checks/prepare',request)
    expect(status==200 and prepared.get('state')=='prepared' and prepared.get('checkId')==check and isinstance(prepared.get('operationProof'),str),'PREPARATION_FAILED')
    save_receipt(receipt_file,prepared);checks.append('prepared')
    submit=request|{'operationProof':prepared['operationProof']}
    status,result=client.call('POST','/v1/url-checks',submit)
    expect(status==200 and result.get('state')=='settled' and result.get('checkId')==check,'ANALYSIS_NOT_SETTLED')
    outcome=result.get('outcome') or {};accounting=result.get('accounting') or {}
    charge=accounting.get('chargedChecks')
    expect(type(charge) is int and charge==(1 if outcome.get('processingOutcome')=='complete' else 0),'COMPLETE_ONLY_ACCOUNTING_FAILED')
    expect(outcome.get('processingOutcome') in ('complete','partial','failed','blocked','unavailable','unsupported','invalid_input'),'OUTCOME_INVALID')
    checks.append('analysis_settled')
    reconcile={'transportVersion':TRANSPORT,'checkId':check,'operationProof':prepared['operationProof']}
    status,reconciled=client.call('POST','/v1/url-checks/reconcile',reconcile)
    expect(status==200 and reconciled.get('state')=='settled' and reconciled.get('accounting')==accounting,'RECONCILIATION_MISMATCH');checks.append('url_free_reconciliation')
    status,duplicate=client.call('POST','/v1/url-checks',submit)
    expect(status==200 and duplicate.get('state')=='settled' and duplicate.get('accounting')==accounting,'DUPLICATE_MISMATCH');checks.append('same_proof_duplicate')
    status,after=client.call('GET','/v1/access')
    expect(status==200 and after.get('allowance',{}).get('completedUsed')==before+charge and after.get('allowance',{}).get('reserved')==0,'ALLOWANCE_DELTA_INVALID');checks.append('single_allowance_delta')
    return {'passed':True,'checks':checks,'processingOutcome':outcome['processingOutcome'],'chargedChecks':charge}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',required=True);parser.add_argument('--fingerprint',required=True)
    parser.add_argument('--token-file');parser.add_argument('--receipt-file',required=True)
    parser.add_argument('--activate-trial',action='store_true');parser.add_argument('--public-https',action='store_true')
    args=parser.parse_args()
    if not args.activate_trial or not args.public_https:parser.error('Explicit --activate-trial and --public-https are required')
    try:
        result=run(Client(args.base_url,load_token(args.token_file),args.fingerprint),args.receipt_file)
    except Exception as error:
        code=str(error) if isinstance(error,SmokeFailure) else 'SMOKE_FAILED'
        print(json.dumps({'passed':False,'reason':code}));return 1
    print(json.dumps(result));return 0


if __name__=='__main__':sys.exit(main())
