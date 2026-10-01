"""Import disabled candidate handlers without network, each in an isolated process."""
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
for name in ('url_consumer','url_lease_recovery','v1_entitlements','v1_authority_deletion'):
    with tempfile.TemporaryDirectory() as temp:
        with zipfile.ZipFile(ROOT/'dist'/f'{name}.zip') as archive:archive.extractall(temp)
        module=name+'.app' if name in ('v1_entitlements','v1_authority_deletion') else 'app'
        code=f'''
import socket,json,sys
# Host dependency preloaded because ARM64 wheels cannot execute on CI x86_64/macOS.
# Packaged application/resource imports still resolve from the isolated archive.
import jsonschema
sys.path.insert(0,{temp!r})
socket.create_connection=lambda *a,**kw: (_ for _ in ()).throw(AssertionError("network forbidden"))
from {module} import lambda_handler
class Context:
    def get_remaining_time_in_millis(self):return 29000
result=lambda_handler({{}},Context())
assert result.get('statusCode')==503 or result.get('enabled') is False,result
'''
        subprocess.run([sys.executable,'-c',code],cwd=ROOT,env={'PATH':'/usr/bin:/bin','STAGE':'dev','AWS_EC2_METADATA_DISABLED':'true'},check=True)
print('4 disabled V1 package handlers import from isolated archive layouts; host jsonschema used, no network; ARM64 runtime smoke remains required')
