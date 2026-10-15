import os
from pathlib import Path
import subprocess
import sys
import zipfile
import pytest
ROOT=Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('function',['url_consumer','url_assessment','url_redirect_resolver','url_qualification_cleanup'])
def test_scoped_packages_import_offline_and_have_closed_defaults(tmp_path,function):
    subprocess.run(['bash',str(ROOT/'scripts/build_lambda_zip.sh'),'--function',function,'--skip-dependencies','--output-dir',str(tmp_path)],check=True,capture_output=True)
    dest=tmp_path/function;dest.mkdir()
    with zipfile.ZipFile(tmp_path/(function+'.zip')) as archive:
        assert archive.testzip() is None and len(archive.namelist())==len(set(archive.namelist()))
        assert 'shared_url_qualification/__init__.py' in archive.namelist()
        if function!='url_qualification_cleanup':assert 'shared_lookup_freshness/__init__.py' in archive.namelist()
        for name in archive.namelist():
            if name.endswith('.py'):compile(archive.read(name),name,'exec')
        archive.extractall(dest)
    code='import app\nfrom shared_url_qualification import mode\nassert not mode()\n'
    if function=='url_qualification_cleanup':code+='assert app.lambda_handler({},None)=={"enabled":False}\n'
    result=subprocess.run([sys.executable,'-c',code],cwd=dest,capture_output=True,text=True,
                          env={'PATH':str(Path(sys.executable).parent),'PYTHONPATH':str(dest)})
    assert result.returncode==0,result.stderr
