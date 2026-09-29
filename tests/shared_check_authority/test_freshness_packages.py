"""Actual source archive imports and versioned readers in isolated package paths."""
import os,subprocess,sys,zipfile
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]

@pytest.mark.parametrize('function',['url_assessment','url_consumer','message_evaluator','message_consumer','account_export_api','v1_entitlements','result_feedback'])
def test_affected_source_archive_isolated_import_and_contract_readers(tmp_path,function):
    subprocess.run(['bash',str(ROOT/'scripts/build_lambda_zip.sh'),'--function',function,'--skip-dependencies','--output-dir',str(tmp_path)],check=True,capture_output=True)
    dest=tmp_path/function;dest.mkdir()
    with zipfile.ZipFile(tmp_path/(function+'.zip')) as archive:
        assert len(archive.namelist())==len(set(archive.namelist())) and archive.testzip() is None
        assert 'shared_lookup_freshness/__init__.py' in archive.namelist()
        for name in archive.namelist():
            if name.endswith('.py'):compile(archive.read(name),name,'exec')
        archive.extractall(dest)
    code='import app\nfrom shared_lookup_freshness import current\nassert current("2027-01-15T08:00:00Z","2027-01-15T08:00:00.1Z",1800000000)\nassert not current("2027-01-15T08:00:00Z","2027-01-15T08:00:00.1Z",1800000000.9)\n'
    if function=='url_consumer':code+='from service import fresh_mapper\nassert fresh_mapper().VERSION=="0.3.0-candidate.1"\n'
    if function in ('message_consumer','message_evaluator','result_feedback'):
        code+='from shared_message_contract.runtime import fresh_url_mapper\nassert fresh_url_mapper().VERSION=="0.3.0-candidate.1"\n'
    if function=='message_consumer':code+='from message_consumer.service import unavailable_envelope\nassert unavailable_envelope(version="1.0.0-message-candidate.3")["transportVersion"]=="1.0.0-message-candidate.3"\n'
    result=subprocess.run([sys.executable,'-c',code],cwd=dest,capture_output=True,text=True,env={'PATH':str(Path(sys.executable).parent),'PYTHONPATH':str(dest)})
    assert result.returncode==0,result.stderr
