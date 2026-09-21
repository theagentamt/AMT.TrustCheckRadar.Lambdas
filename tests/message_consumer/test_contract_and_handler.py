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
