import importlib.util
import json
import os
from pathlib import Path
import stat
import pytest
spec=importlib.util.spec_from_file_location('engineering_smoke',Path(__file__).parents[2]/'scripts/url_consumer_engineering_smoke.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Fake:
    def __init__(self,outcome='complete'):
        self.calls=[];self.outcome=outcome;self.charge=int(outcome=='complete');self.submits=0
    def call(self,method,path,payload=None,*,authenticated=True):
        self.calls.append((method,path,payload,authenticated))
        if not authenticated:return 401,{'message':'Unauthorized'}
        if path=='/v1/access':return 200,{'schemaVersion':1,'activeDevice':True,'access':{'basis':'trial'},'allowance':{'completedUsed':int(self.submits>0)*self.charge,'reserved':0}}
        if path=='/v1/access/trial':return 200,{'access':{'basis':'trial'},'allowance':{'completedUsed':0,'limit':10}}
        if path.endswith('/prepare'):
            self.check=payload['checkId']
            return 200,{'state':'prepared','checkId':self.check,'operationProof':'synthetic-private-proof','expiresAt':1900000000}
        if path=='/v1/url-checks':self.submits+=1
        if path.endswith('/reconcile'):assert set(payload)=={'transportVersion','checkId','operationProof'}
        return 200,{'state':'settled','checkId':self.check,'outcome':{'processingOutcome':self.outcome},'accounting':{'chargedChecks':self.charge,'receiptId':'synthetic'}}

@pytest.mark.parametrize('outcome',['complete','partial','failed'])
def test_explicit_sequence_preserves_receipt_and_has_no_technical_retry(tmp_path,outcome):
    client=Fake(outcome);path=tmp_path/'receipt.json'
    result=m.run(client,path)
    assert result['passed'] and result['chargedChecks']==int(outcome=='complete')
    assert client.submits==2
    stored=json.loads(path.read_text())
    assert set(stored)=={'checkId','operationProof','expiresAt'}
    assert stat.S_IMODE(path.stat().st_mode)==0o600
    assert 'https://' not in path.read_text() and 'synthetic-private-proof' not in json.dumps(result)


def test_existing_receipt_never_overwritten_or_resubmitted(tmp_path):
    path=tmp_path/'receipt.json';path.write_text('existing')
    client=Fake()
    with pytest.raises(FileExistsError):m.run(client,path)
    assert path.read_text()=='existing' and client.submits==0


def test_unsafe_token_destination_rejected():
    for host in ('https://attacker.invalid','http://api-dev.andmorethings.net','https://api-dev.andmorethings.net@attacker.invalid','https://api-dev.andmorethings.net/?x=1'):
        with pytest.raises(m.SmokeFailure):m.Client(host,'a.b.c','synthetic-device')


def test_token_file_requires_private_permissions_and_never_symlink(tmp_path):
    path=tmp_path/'token';path.write_text('a.b.c');path.chmod(0o644)
    with pytest.raises(m.SmokeFailure):m.load_token(path)
    path.chmod(0o600);assert m.load_token(path)=='a.b.c'
    link=tmp_path/'link';link.symlink_to(path)
    with pytest.raises(OSError):m.load_token(link)
