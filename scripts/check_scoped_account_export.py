#!/usr/bin/env python3
"""Read-only HTTP acceptance. Dry run by default; credentials/cursors stay in memory.

The reviewed plan pins the API endpoint, designated synthetic subject and source.
--execute reads {accessToken, deviceFingerprint} only from stdin. It never creates
an account, writes a receipt, stores a download, refreshes auth, or retries START.
"""
import argparse
import base64
import hashlib
import http.client
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit
from uuid import UUID

ROOT=Path(__file__).resolve().parents[1]
CONTRACT=ROOT/'contracts/account-export/1.0.0-candidate.3'
MAX_BYTES=65536
ERROR_CODES={'SERVICE_NOT_ENABLED','SERVICE_UNAVAILABLE','SOURCE_UNAVAILABLE','SOURCE_CHANGED',
 'AUTHENTICATION_REQUIRED','REAUTHENTICATION_REQUIRED','ACCOUNT_UNAVAILABLE','ACTIVE_DEVICE_REQUIRED',
 'ACCESS_CHANGED','INVALID_REQUEST','INVALID_CURSOR','EXPORT_EXPIRED','EXPORT_LIMIT_EXCEEDED'}
FORBIDDEN={'ciphertext','wrappedDataKey','kmsKeyArn','tokenDigest','payloadHmac','bindingFingerprint',
 'purchaseToken','accessToken','refreshToken','authorization','consentEpoch','PK','SK','GSI1PK','GSI1SK','checkId'}

class Unverified(Exception):pass

def need(value):
    if not value:raise Unverified()

def pairs(items):
    result={}
    for key,value in items:
        need(key not in result);result[key]=value
    return result

def decode(raw):return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(Unverified()))

def plan_check(plan):
    need(type(plan) is dict and set(plan)=={'schemaVersion','endpoint','subject','issuer','clientId','sourceCommit'})
    need(type(plan['schemaVersion']) is int and plan['schemaVersion']==1)
    need(type(plan['subject']) is str and str(UUID(plan['subject']))==plan['subject'])
    need(re.fullmatch(r'https://[a-z0-9]{10}\.execute-api\.us-east-1\.amazonaws\.com/(?:dev/)?v1/users/account-export',plan['endpoint']))
    need(re.fullmatch(r'https://cognito-idp\.us-east-1\.amazonaws\.com/us-east-1_[A-Za-z0-9]+',plan['issuer']))
    need(re.fullmatch('[a-z0-9]{20,32}',plan['clientId']) and re.fullmatch('[0-9a-f]{40}',plan['sourceCommit']))
    return plan

def source_check(plan):
    def git(*args):return subprocess.check_output(['git','-C',str(ROOT),*args],text=True).strip()
    need(git('rev-parse','HEAD')==plan['sourceCommit'] and not git('status','--porcelain'))
    for line in (CONTRACT/'SHA256SUMS').read_text().splitlines():
        digest,name=line.split();need(hashlib.sha256((CONTRACT/name).read_bytes()).hexdigest()==digest)

def claims_check(plan,credentials,now):
    need(type(credentials) is dict and set(credentials)=={'accessToken','deviceFingerprint'})
    token=credentials['accessToken'];fingerprint=credentials['deviceFingerprint']
    need(type(token) is str and len(token)<=16384 and re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',token))
    need(type(fingerprint) is str and re.fullmatch('[A-Za-z0-9_-]{16,128}',fingerprint))
    middle=token.split('.')[1];claims=decode(base64.urlsafe_b64decode(middle+'='*(-len(middle)%4)))
    # Local routing checks only. API Gateway performs cryptographic JWT validation.
    need(claims.get('sub')==plan['subject'] and claims.get('iss')==plan['issuer']
         and claims.get('client_id')==plan['clientId'] and claims.get('token_use')=='access'
         and 'aws.cognito.signin.user.admin' in claims.get('scope','').split())
    for name in ('auth_time','iat','exp'):need(type(claims.get(name)) is int)
    need(0<claims['auth_time']<=claims['iat']<=now+60 and now-300<=claims['auth_time']<=now+60 and claims['exp']>now+5)

def send(endpoint,credentials,body):
    url=urlsplit(endpoint);connection=http.client.HTTPSConnection(url.hostname,timeout=5)
    try:
        connection.request('POST',url.path,body=json.dumps(body,separators=(',',':')),
            headers={'Authorization':'Bearer '+credentials['accessToken'],
                     'x-device-binding-fingerprint':credentials['deviceFingerprint'],'Content-Type':'application/json'})
        response=connection.getresponse()
        return response.status,{k.lower():v for k,v in response.getheaders()},response.read(MAX_BYTES+1)
    finally:connection.close()

def safe_keys(value):
    if type(value) is dict:
        need(not (set(value)&FORBIDDEN))
        for child in value.values():safe_keys(child)
    elif type(value) is list:
        for child in value:safe_keys(child)

def public_item(item,family,fields,observed):
    need(type(item) is dict)
    allowed=fields['play_verification'].get(item.get('kind')) if family=='play_verification' else fields['families'][family]
    need(allowed is not None and set(item)<=set(allowed));safe_keys(item)
    if family=='play_verification':
        need(set(item)==set(allowed) and item['platform']=='google_play')
        if item['kind']=='google_play_preparation':
            need(type(item['preparedAtEpoch']) is int and 0<item['preparedAtEpoch']<=observed)
        else:
            need(type(item['verifiedAccessUntilEpoch']) is int and item['verifiedAccessUntilEpoch']>0
                 and type(item['expiresAtEpoch']) is int and item['expiresAtEpoch']==item['verifiedAccessUntilEpoch']+604800
                 and observed<item['expiresAtEpoch'] and item['acknowledgment'] in ('pending','acknowledged','not_applicable'))
        return
    for key,value in item.items():
        if value is None:continue
        if key=='sources':
            need(type(value) is dict and set(value)<={'trial','paid','complimentary'})
            for source in value.values():
                need(type(source) is dict and 'sources' not in source);public_item(source,'access',fields,observed)
        elif key=='feedback':
            need(type(value) is dict and set(value)=={'category','receivedAt'}
                 and value['category'] in ('looks_legitimate','looks_like_scam','unclear','unhelpful')
                 and type(value['receivedAt']) is int and 0<=value['receivedAt']<=observed)
        elif key in ('resultSummary','assessment'):
            # Exact nested producer contracts remain separately qualified. Do not
            # accept arbitrary nested objects in ordinary scalar public fields.
            need(type(value) is dict)
        elif key in fields['integerFields']:
            need(type(value) is int and value>=0)
        elif key in fields['booleanFields']:
            need(type(value) is bool)
        elif key in ('awardedBadgeIds','signalIds','indicatorIds'):
            need(type(value) is list and len(value)<=100 and all(type(v) is str and len(v.encode())<=4096 for v in value)
                 and len(set(value))==len(value))
        elif key=='confidence':need(type(value) in (int,float) and 0<=value<=1)
        else:need(type(value) is str and len(value.encode())<=4096)

def run(plan,credentials,*,transport=send,now=time.time,monotonic=time.monotonic):
    from jsonschema import Draft202012Validator
    plan_check(plan)
    schema=Draft202012Validator(decode((CONTRACT/'response.schema.json').read_text()))
    expected_scope=decode((CONTRACT/'manifest.json').read_text())
    fields=decode((CONTRACT/'public-fields.json').read_text())
    # Manifest contains the exact public scope separately from transport metadata.
    expected_scope=expected_scope.get('scope',expected_scope)
    report={'schemaVersion':1,'outcome':'OBSERVATION_UNAVAILABLE','pages':0,'itemsByFamily':{},
            'lastHttpStatus':None,'errorCode':None,'credentialsPersisted':False,'downloadPersisted':False}
    started=monotonic();identity=None;total=0;cursor=None
    try:
        for page_number in range(256):
            need(monotonic()-started<240);claims_check(plan,credentials,int(now()))
            body={'schemaVersion':1,'action':'START_EXPORT'} if cursor is None else {'schemaVersion':1,'action':'CONTINUE_EXPORT','cursor':cursor}
            status,headers,raw=transport(plan['endpoint'],credentials,body)
            need(type(status) is int and 100<=status<=599);report['lastHttpStatus']=status
            need(monotonic()-started<240);claims_check(plan,credentials,int(now()))
            need(len(raw)<=MAX_BYTES);total+=len(raw);need(total<=8*1024*1024)
            value=decode(raw)
            if status!=200:
                code=value.get('error',{}).get('code') if type(value) is dict and type(value.get('error')) is dict else None
                report['errorCode']=code if code in ERROR_CODES else None
                report['outcome']='REJECTED' if code in ERROR_CODES and status<500 else 'OBSERVATION_UNAVAILABLE'
                return report
            need('no-store' in headers.get('cache-control','').lower());schema.validate(value)
            need(value['scope']==expected_scope and value['pageNumber']==page_number)
            current=(value['operationId'],value['startedAtEpoch'],value['expiresAtEpoch'])
            if identity is None:identity=current
            need(current==identity and current[2]==current[1]+900 and current[1]<=value['observedAtEpoch']<current[2] and now()<current[2])
            family=value['family']
            for item in value['items']:
                public_item(item,family,fields,value['observedAtEpoch'])
            report['pages']+=1
            report['itemsByFamily'][family]=report['itemsByFamily'].get(family,0)+len(value['items'])
            cursor=value['nextCursor']
            if value['status']=='COMPLETE':
                need(cursor is None and set(report['itemsByFamily'])==set(expected_scope['included']))
                report['outcome']='EXPORT_COMPLETE';return report
            need(type(cursor) is str and bool(cursor))
    except Exception:
        return report
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True);parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    try:
        plan=plan_check(decode(args.plan.read_text()));source_check(plan)
        if not args.execute:
            print(json.dumps({'schemaVersion':1,'dryRun':True,'planValid':True,'networkCalls':0}));return 0
        credentials=decode(sys.stdin.read(20001));need(len(json.dumps(credentials))<=20000)
        result=run(plan,credentials);print(json.dumps(result,sort_keys=True))
        return 0 if result['outcome']=='EXPORT_COMPLETE' else 2
    except Exception:
        print('{"schemaVersion":1,"outcome":"QUALIFICATION_UNVERIFIED"}');return 2

if __name__=='__main__':raise SystemExit(main())
