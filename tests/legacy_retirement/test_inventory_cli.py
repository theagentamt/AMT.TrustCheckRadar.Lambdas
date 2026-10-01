"""The offline report binds its aggregate output to input, never to an apply path."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import pytest

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/'tools/legacy_access_inventory/plan.py'

def run(raw):
 with tempfile.TemporaryDirectory() as directory:
  path=Path(directory)/'protected.json';path.write_bytes(raw)
  return subprocess.run([sys.executable,str(SCRIPT),'--input',str(path)],capture_output=True,text=True)

def test_synthetic_report_is_exactly_reproducible_and_has_no_apply():
 raw=(ROOT/'tools/legacy_access_inventory/synthetic-input.json').read_bytes()
 result=run(raw)
 assert result.returncode==0 and not result.stderr
 report=json.loads(result.stdout)
 assert report['inputSha256']==hashlib.sha256(raw).hexdigest()
 assert report['classifierVersion']=='legacy-preservation-v1'
 assert report['digestKind']=='source_bytes'
 assert report['classifications']['dispatch_ambiguous_preserve']==2
 assert report['classifications']['result_ready_accounting_unproven_preserve']==1
 assert report['classifications']['unknown_shape']==2
 assert 'purchase_binding' in report['missingRequiredFamilies']
 assert not any(report[name] for name in ('applyAvailable','inventoryComplete','replayQualified','migrationApproved'))
 assert 'private-marker' not in result.stdout
 assert run(raw).stdout==result.stdout

@pytest.mark.parametrize('raw',[
 b'{"schemaVersion":1,"schemaVersion":1,"records":[]}',
 b'{"schemaVersion":true,"records":[]}', b'{"schemaVersion":1,"records":[NaN]}',
 b'private-marker', b' '*(8*1024*1024+1),
 json.dumps({'schemaVersion':1,'records':[{}]*10001}).encode(),
], ids=['duplicate','boolean-schema','nonfinite','malformed','oversized','too-many-records'])
def test_malformed_input_has_fixed_error_without_evidence_echo(raw):
 result=run(raw)
 assert result.returncode==2 and not result.stdout
 assert result.stderr=='INVENTORY_INPUT_INVALID\n'

def test_unknown_nested_shapes_are_preserved_as_unknown():
 raw=json.dumps({'schemaVersion':1,'records':[
 {'family':[], 'item':{'PK':'USER#private-marker','SK':'x'}},
 {'family':'request','item':{'PK':'ANALYSIS#REQUEST#private-marker','SK':'x','status':[]}},
 {'family':'entitlement','item':{'PK':'USER#private-marker','SK':'ENTITLEMENT','entitlementTier':{}}},
 ]}).encode()
 result=run(raw)
 assert result.returncode==0
 assert json.loads(result.stdout)['classifications']=={'unknown_shape':3}
 assert 'private-marker' not in result.stdout
