#!/usr/bin/env python3
"""Build a reviewed diagnostic overlay of the exact deployed Dev reader, offline."""
import argparse
import ast
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import zipfile

BASELINE_SOURCE = '8ecc79678fd1f87c575c43f7f4ffb856869db48d'
BASELINE_SHA256 = '90b006ddee49e19b57d2fb5f3d2a9c89137b4593e837ba284cc56958ee7ff531'
EXPECTED_SHA256 = 'b56547ef6ad6c16da94ba2bcae866a0f3301c11474a0858ad15420ef5d14acd5'
HANDLER = 'v1_entitlements.app.lambda_handler'
APP = 'v1_entitlements/app.py'
DIAGNOSTICS = 'v1_entitlements/diagnostics.py'
ROOT = Path(__file__).resolve().parents[1]
HOOK = '''
def _record_get_failure(event, error):
    try:
        from v1_entitlements.diagnostics import record_get_failure
        record_get_failure(event, error)
    except Exception:
        pass  # Diagnostics must never alter the response or trigger a retry.


'''


def require(condition):
    if not condition:
        raise ValueError('Diagnostic package qualification rejected.')


class StripDiagnostics(ast.NodeTransformer):
    def visit_Module(self, node):
        node.body = [value for value in node.body if not (
            isinstance(value, ast.FunctionDef) and value.name == '_record_get_failure')]
        return self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        node.body = [value for value in node.body if not (
            isinstance(value, ast.Expr) and isinstance(value.value, ast.Call)
            and isinstance(value.value.func, ast.Name)
            and value.value.func.id == '_record_get_failure')]
        if isinstance(node.type, ast.Name) and node.type.id == 'Exception':
            node.name = None
        return self.generic_visit(node)


def instrument(original):
    text = original.decode('utf-8')
    authority = '    except AuthorityError as error:\n'
    generic = '    except Exception:\n        return _response(503'
    entry = '\ndef lambda_handler(event, _context):'
    require(text.count(authority) == text.count(generic) == text.count(entry) == 1)
    text = text.replace(authority, authority + '        _record_get_failure(event, error)\n')
    text = text.replace(generic, '    except Exception as error:\n        _record_get_failure(event, error)\n        return _response(503')
    text = text.replace(entry, HOOK + 'def lambda_handler(event, _context):')
    before = ast.dump(ast.parse(original), include_attributes=False)
    after = ast.dump(StripDiagnostics().visit(ast.parse(text)), include_attributes=False)
    require(before == after)
    return text.encode('utf-8')


def build(baseline, diagnostics):
    require(len(baseline) <= 20 * 1024 * 1024
            and hashlib.sha256(baseline).hexdigest() == BASELINE_SHA256)
    compile(diagnostics, DIAGNOSTICS, 'exec')
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(baseline)) as original, zipfile.ZipFile(result, 'w') as candidate:
        names = original.namelist()
        require(len(names) <= 500 and len(names) == len(set(names)) and DIAGNOSTICS not in names)
        require(all(not name.startswith('/') and '..' not in Path(name).parts for name in names))
        require(sum(item.file_size for item in original.infolist()) <= 20 * 1024 * 1024)
        app = instrument(original.read(APP))
        for item in original.infolist():
            candidate.writestr(item, app if item.filename == APP else original.read(item.filename))
        item = zipfile.ZipInfo(DIAGNOSTICS, date_time=(2026, 10, 13, 0, 0, 0))
        item.compress_type = zipfile.ZIP_DEFLATED
        item.external_attr = 0o100644 << 16
        candidate.writestr(item, diagnostics)
    data = result.getvalue()
    require(hashlib.sha256(data).hexdigest() == EXPECTED_SHA256)
    with zipfile.ZipFile(io.BytesIO(baseline)) as original, zipfile.ZipFile(io.BytesIO(data)) as candidate:
        require(set(candidate.namelist()) == set(original.namelist()) | {DIAGNOSTICS})
        require(all(candidate.read(name) == original.read(name) for name in original.namelist() if name != APP))
        require(candidate.read('app.py') == original.read('app.py'))
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        data = build(args.baseline.read_bytes(), (ROOT / 'src' / DIAGNOSTICS).read_bytes())
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        print(json.dumps({'scope': 'dev-access-safe-failure-diagnostics-only',
            'baselineSource': BASELINE_SOURCE, 'baselineSha256': BASELINE_SHA256,
            'candidateSha256': EXPECTED_SHA256,
            'candidateCodeSha256': base64.b64encode(bytes.fromhex(EXPECTED_SHA256)).decode(),
            'bytes': len(data), 'handler': HANDLER, 'changedOriginalEntries': 1,
            'newEntries': 1, 'otherEntriesPreserved': True,
            'published': False, 'runtimeApplied': False}))
    except Exception:
        raise SystemExit('Diagnostic package rejected; no private input values printed.') from None


if __name__ == '__main__':
    main()
