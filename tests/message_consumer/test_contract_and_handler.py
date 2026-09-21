import hashlib
import json
from pathlib import Path
import subprocess
import sys
import pytest
from jsonschema import Draft202012Validator, FormatChecker
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from message_consumer import app
from message_consumer.service import validate_envelope
CONTRACT=ROOT/'contracts/message-consumer/1.0.0-candidate.1'

def test_published_contract_integrity_and_full_transport_fixtures():
    for line in (CONTRACT/'SHA256SUMS').read_text().splitlines():
        digest,name=line.split('  ')
        assert hashlib.sha256((CONTRACT/name).read_bytes()).hexdigest()==digest
    assert (CONTRACT/'reference_validation.py').read_bytes()==(ROOT/'src/shared_message_contract/validation.py').read_bytes()
    for item in json.loads((CONTRACT/'response-fixtures.json').read_text()):validate_envelope(item['body'])
    for item in json.loads((CONTRACT/'request-fixtures.json').read_text()):
        Draft202012Validator(json.loads((CONTRACT/(('reconcile' if item['route'].endswith('/reconcile') else 'prepare' if item['route'].endswith('/prepare') else 'submit')+'.schema.json')).read_text()),format_checker=FormatChecker()).validate(item['body'])

def test_disabled_public_handler_has_unknown_charge_and_no_content_log(monkeypatch,capsys):
    monkeypatch.delenv('MESSAGE_CONSUMER_ENABLED',raising=False)
    monkeypatch.setattr(app,'provider',lambda _:pytest.fail('provider called'))
    raw=app.lambda_handler({'body':'secret input'},None)
    body=json.loads(raw['body'])
    assert raw['statusCode']==503 and body['errorCode']=='SERVICE_NOT_ENABLED'
    assert body['accounting']['chargedChecks'] is None and body['outcome'] is None
    assert not capsys.readouterr().out

def test_actual_built_archives_import_in_isolation(tmp_path):
    import zipfile
    for function in ('message_consumer','message_evaluator'):
        subprocess.run(['bash',str(ROOT/'scripts/build_lambda_zip.sh'),'--function',function,'--skip-dependencies','--output-dir',str(tmp_path)],check=True,capture_output=True)
        dest=tmp_path/function;dest.mkdir()
        with zipfile.ZipFile(tmp_path/(function+'.zip')) as z:z.extractall(dest)
        code='import app; print(app.lambda_handler({}, None)); import sys; '+("assert 'shared_check_authority.core' not in sys.modules and 'message_consumer.service' not in sys.modules" if function=='message_evaluator' else "assert True")
        subprocess.run([sys.executable,'-c',code],cwd=dest,check=True,capture_output=True,env={'PATH':str(Path(sys.executable).parent),'PYTHONPATH':str(dest)})


@pytest.mark.parametrize('function',['url_consumer','url_lease_recovery'])
def test_url_archives_settle_and_recover_without_message_package(tmp_path,function):
    """Exercise real archived authority code, not only import a disabled handler."""
    import zipfile
    subprocess.run(['bash',str(ROOT/'scripts/build_lambda_zip.sh'),'--function',function,'--skip-dependencies','--output-dir',str(tmp_path)],check=True,capture_output=True)
    dest=tmp_path/function;dest.mkdir()
    with zipfile.ZipFile(tmp_path/(function+'.zip')) as archive:
        assert not any(name.startswith('shared_message_contract/') for name in archive.namelist())
        archive.extractall(dest)
    code='''
from shared_check_authority.core import Authority,TrustedWorkerContext
from types import SimpleNamespace
import sys
for expired in (False,True):
    a=Authority.__new__(Authority);a.s=SimpleNamespace(authority_table='authority');a.now=lambda:100
    row={'state':'ADMITTED','retentionDeadlineEpoch':200,'payloadHmac':'digest','executionToken':'owner',
         'basis':'complimentary','periodSK':None,'settleByEpoch':50 if expired else 150,'projectionScope':'full_url'}
    a._assert_account=lambda account:None;a._token_parts=lambda proof:('key',200,'nonce','tag')
    a._partition=lambda account,key:'partition';a._verify_token=lambda *args,**kwargs:None
    a._get=lambda *args:row;a._account_conditions=lambda account:[];a._mac=lambda *args:'a'*64
    def transact(items):
        value=items[-1]['Update']['ExpressionAttributeValues']
        assert value[':charge']==0 and value[':outcome']=='failed'
        row.update(state='SETTLED',chargedChecks=0,processingOutcome='failed')
    a._transact=transact
    result=a._settle(TrustedWorkerContext('account'),'proof','owner','failed',expired_recovery=expired)
    assert result['state']=='SETTLED' and result['chargedChecks']==0
assert not any(name.startswith('shared_message_contract') for name in sys.modules)
'''
    subprocess.run([sys.executable,'-c',code],cwd=dest,check=True,capture_output=True,
                   env={'PATH':str(Path(sys.executable).parent),'PYTHONPATH':str(dest)})
