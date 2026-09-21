"""Host interpreter archive/import checks, never target ARM execution."""
import json,subprocess,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]


def test_exact_handler_disabled_minimal_env_and_packaged_validators(tmp_path):
    subprocess.run(['bash',str(ROOT/'scripts/build_lambda_zip.sh'),'--function','result_feedback','--skip-dependencies','--output-dir',str(tmp_path)],cwd=ROOT,check=True,capture_output=True)
    folder=tmp_path/'unpacked'
    with zipfile.ZipFile(tmp_path/'result_feedback.zip') as archive:
        assert archive.testzip() is None
        names=archive.namelist();assert len(names)==len(set(names))
        assert not any(n.startswith(('evaluation/','message_evaluator/','recovery_evaluator/')) for n in names)
        archive.extractall(folder)
    script='''import sys,os,json,socket
os.environ.clear()
os.environ.update({'STAGE':'dev','RESULT_FEEDBACK_ENABLED':'false','RESULT_FEEDBACK_POLICY_VERSION':'private-result-feedback-2026-09-21-v1','RESULT_FEEDBACK_POLICY_APPROVAL_SHA256':'9e3485588daeae698b139cb070b4de16bb9298348135271e7d9adafd9968bee6'})
sys.path.insert(0,sys.argv[1])
import boto3
for name in ('client','resource'):setattr(boto3,name,lambda *a,**k:(_ for _ in ()).throw(AssertionError('AWS forbidden')))
socket.create_connection=lambda *a,**k:(_ for _ in ()).throw(AssertionError('network forbidden'))
from result_feedback.app import lambda_handler
from app import lambda_handler as shim
assert shim is lambda_handler
r=lambda_handler({},None)
assert r['statusCode']==503 and json.loads(r['body'])['reasonCode']=='SERVICE_NOT_ENABLED'
assert 'result_feedback.runtime' not in sys.modules
from result_feedback.service import Feedback
from shared_message_contract.runtime import url_mapper
from shared_message_contract.validation_v2 import validate_summary
mapper=url_mapper()
assert mapper.supported_private({'unexpected':'field'},'case','full_url') is False
from shared_recovery_contract.usage import usage
assert usage('case','failed')['contentDisposition']=='not_retained'
from pathlib import Path
root=Path(sys.argv[1])
import jsonschema
for f in ('request.schema.json','response.schema.json'):
 jsonschema.Draft202012Validator.check_schema(json.loads((root/'result_feedback/contract'/f).read_text()))
assert (root/'shared_message_contract/url_contract/url-assessment.schema.json').exists() or list((root/'shared_message_contract/url_contract').glob('*schema.json'))
'''
    result=subprocess.run([sys.executable,'-I','-c',script,str(folder)],cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
