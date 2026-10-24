"""Package source/schema closure; final Linux runtime checks are separate evidence."""
from pathlib import Path
import subprocess
import zipfile
import json
import os
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('function',['message_consumer','message_evaluator','url_consumer','url_assessment',
    'recovery_consumer','recovery_evaluator','url_lease_recovery','v1_authority_deletion'])
def test_each_operations_caller_contains_exact_module_and_schema(function,tmp_path):
    result=subprocess.run(['bash',str(ROOT/'scripts/build_lambda_zip.sh'),'--function',function,
        '--skip-dependencies','--output-dir',str(tmp_path)],cwd=ROOT,check=True,capture_output=True,text=True)
    assert 'Python 3.14, arm64' in result.stdout
    with zipfile.ZipFile(tmp_path/(function+'.zip')) as package:
        for file in (ROOT/'src/shared_operations').rglob('*'):
            if file.is_file() and '__pycache__' not in file.parts and file.suffix!='.pyc':
                assert package.read('shared_operations/'+str(file.relative_to(ROOT/'src/shared_operations')))==file.read_bytes()
    # Match the existing runtime pin, rather than silently upgrading it to the
    # newer development-environment validator.
    assert 'jsonschema==4.25.1' in (ROOT/'src'/function/'requirements.txt').read_text()


def test_evaluator_package_imports_default_off_and_rules_only_without_authority_secrets(tmp_path):
    subprocess.run(['bash',str(ROOT/'scripts/build_lambda_zip.sh'),'--function','message_evaluator',
        '--skip-dependencies','--output-dir',str(tmp_path)],cwd=ROOT,check=True,capture_output=True)
    from shared_message_contract.validation_v2 import POLICY, APPROVAL_SHA
    package=tmp_path/'message_evaluator.zip'
    with zipfile.ZipFile(package) as archive:
        archive.extractall(tmp_path/'unpacked')
        assert archive.read('shared_check_authority/core.py')==(ROOT/'src/shared_check_authority/core.py').read_bytes()
        assert archive.read('shared_history/security.py')==(ROOT/'src/shared_history/security.py').read_bytes()
    request={'schemaVersion':3,'checkId':'package-check','policyVersion':POLICY,'runtimeMode':'rules_only',
        'executionBudgetMs':1000,'intent':{'entryPoint':'message','language':'en','target':{
            'scope':'sanitized_message','sourceType':'pasted_text','sanitizedText':'Send me your account password and the login code.',
            'speakerRole':'other','entities':[],'withheldLinks':False,'reviewedLinks':[]}}}
    script="""
import json,sys
sys.path.insert(0,sys.argv[1])
import boto3
boto3.client=lambda *a,**k:(_ for _ in ()).throw(AssertionError('unexpected AWS client'))
boto3.resource=lambda *a,**k:(_ for _ in ()).throw(AssertionError('unexpected AWS resource'))
import app
class Context:
    def get_remaining_time_in_millis(self):return 2000
result=app.lambda_handler(json.loads(sys.argv[2]),Context())
assert result['schemaVersion']==3 and result['aiAssessmentStatus']=='not_assessed',result
print('PACKAGE_DEFAULT_OFF_RULES_ONLY_PASS')
"""
    env={k:v for k,v in os.environ.items() if not k.startswith(('AWS_','OPERATIONS_','MESSAGE_','AUTHORITY_'))}
    env.update(STAGE='dev',OPERATIONS_ENABLED='false',MESSAGE_EVALUATOR_ENABLED='true',MESSAGE_CANDIDATE3_RULES_ONLY_ENABLED='true',
        MESSAGE_AI_POLICY_VERSION=POLICY,MESSAGE_AI_POLICY_APPROVAL_SHA256=APPROVAL_SHA,AWS_EC2_METADATA_DISABLED='true',PYTHONPATH='')
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path/'unpacked'),json.dumps(request)],cwd=tmp_path,
        env=env,check=True,capture_output=True,text=True)
    assert result.stdout.strip()=='PACKAGE_DEFAULT_OFF_RULES_ONLY_PASS'
