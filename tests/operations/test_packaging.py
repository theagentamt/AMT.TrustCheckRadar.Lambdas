"""Package source/schema closure; final Linux runtime checks are separate evidence."""
from pathlib import Path
import subprocess
import zipfile

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
