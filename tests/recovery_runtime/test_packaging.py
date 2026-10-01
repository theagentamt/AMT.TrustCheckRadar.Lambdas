"""Source-only archive boundary checks; not ARM runtime qualification."""
import subprocess,sys,json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]


def test_exact_canonical_handlers_and_legacy_lease_dependency(tmp_path):
    for function in ('recovery_consumer','recovery_evaluator','url_lease_recovery','url_consumer','v1_entitlements','v1_authority_deletion','message_consumer'):
        subprocess.run(['bash',str(ROOT/'scripts/build_lambda_zip.sh'),'--function',function,'--skip-dependencies','--output-dir',str(tmp_path)],cwd=ROOT,check=True,capture_output=True)
        extracted=tmp_path/function
        with zipfile.ZipFile(tmp_path/(function+'.zip')) as archive:archive.extractall(extracted)
        script='''import sys,json,os
os.environ.clear()
os.environ.update({'STAGE':'dev','RECOVERY_CONSUMER_ENABLED':'false','AUTHORITY_ENABLED':'false','RECOVERY_PROVIDER_CIRCUIT_OPEN':'true','RECOVERY_EVALUATOR_ENABLED':'false','RECOVERY_AI_ENABLED':'false','RECOVERY_AI_QUALIFIED':'false','RECOVERY_POLICY_VERSION':'recovery-clarification-2026-09-21-v1','RECOVERY_POLICY_APPROVAL_SHA256':'173132b8d5a16d3d5a8ccdbc7355a631f8384634945772993200a3559c89da72','RECOVERY_PLAYBOOK_VERSION':'recovery-playbook-1.0'})
sys.path.insert(0,sys.argv[1])
import boto3,socket
boto3.client=lambda *a,**k:(_ for _ in ()).throw(AssertionError('AWS forbidden'))
socket.create_connection=lambda *a,**k:(_ for _ in ()).throw(AssertionError('network forbidden'))
'''
        if function.startswith('recovery_'):
            script+=f'from {function}.app import lambda_handler\nr=lambda_handler({{}},None)\n'
            script+="assert r['statusCode']==503\n" if function=='recovery_consumer' else "assert r=={'enabled':False}\n"
        else:
            script+='''from shared_recovery_contract.usage import usage
from shared_check_authority.core import Authority
assert usage('case','failed')['contentDisposition']=='not_retained'
assert 'shared_recovery_contract.validation' not in sys.modules
assert 'recovery_evaluator' not in sys.modules
'''
        subprocess.run([sys.executable,'-I','-c',script,str(extracted)],cwd=tmp_path,check=True,capture_output=True)
