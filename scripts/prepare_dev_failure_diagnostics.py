#!/usr/bin/env python3
"""Offline diagnostics-only overlay of two exact deployed Dev bundles; never deploy."""
import argparse
import ast
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = {
    'verification': {
        'baselineCodeSha256': 'lRlJaVYOIIOGqpQml/HsF1VyyuCetakXoFdPGtUT9C0=',
        'changed': 'v1_play_handoff/app.py', 'added': 'v1_play_handoff/diagnostics.py',
        'hook': '_record_failure', 'variable': 'diagnostic_stage',
        'baselineEntrySha256': 'cb0ff4f737b6abe051339ed90c15b2c4d759c6f2656d91c1e37c6d41c21f4404',
    },
    'admission': {
        'baselineCodeSha256': 'tWVH72rWwW2pS6K8roZqDzMBwRR0oIWK0VQg710UrNU=',
        'changed': 'v1_entitlements/snapshot_gate.py', 'added': 'v1_entitlements/admission_diagnostics.py',
        'hook': '_record_admission_failure', 'variable': '_diagnostic_branch',
        'baselineEntrySha256': 'c6db8ca152c6b5e3988656249f2f830dc0d2445432facd84c2a20939b277fe8b',
    },
}


def require(condition):
    if not condition:
        raise ValueError('Diagnostic overlay qualification rejected.')


class StripDiagnostics(ast.NodeTransformer):
    def __init__(self, hook, variable):
        self.hook, self.variable = hook, variable

    def visit_FunctionDef(self, node):
        return None if node.name == self.hook else self.generic_visit(node)

    def visit_Assign(self, node):
        if any(isinstance(target, ast.Name) and target.id == self.variable for target in node.targets):
            return None
        return self.generic_visit(node)

    def visit_Expr(self, node):
        if (isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                and node.value.func.id == self.hook):
            return None
        return self.generic_visit(node)


def qualified_source(original, candidate, spec):
    require(hashlib.sha256(original).hexdigest() == spec['baselineEntrySha256'])
    before = ast.dump(ast.parse(original), include_attributes=False)
    stripped = StripDiagnostics(spec['hook'], spec['variable']).visit(ast.parse(candidate))
    require(before == ast.dump(stripped, include_attributes=False))
    compile(candidate, spec['changed'], 'exec')


def build(baseline, component):
    spec = COMPONENTS[component]
    require(len(baseline) <= 40 * 1024 * 1024)
    require(base64.b64encode(hashlib.sha256(baseline).digest()).decode() == spec['baselineCodeSha256'])
    changed = (ROOT / 'src' / spec['changed']).read_bytes()
    added = (ROOT / 'src' / spec['added']).read_bytes()
    compile(added, spec['added'], 'exec')
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(baseline)) as original, zipfile.ZipFile(result, 'w') as candidate:
        names = original.namelist()
        require(len(names) <= 2000 and len(names) == len(set(names)) and spec['added'] not in names)
        require(all(not name.startswith('/') and '..' not in Path(name).parts for name in names))
        require(sum(item.file_size for item in original.infolist()) <= 80 * 1024 * 1024)
        qualified_source(original.read(spec['changed']), changed, spec)
        for item in original.infolist():
            candidate.writestr(item, changed if item.filename == spec['changed'] else original.read(item.filename))
        item = zipfile.ZipInfo(spec['added'], date_time=(2026, 10, 17, 0, 0, 0))
        item.compress_type = zipfile.ZIP_DEFLATED
        item.external_attr = 0o100644 << 16
        candidate.writestr(item, added)
        candidate.comment = original.comment
    data = result.getvalue()
    with zipfile.ZipFile(io.BytesIO(baseline)) as original, zipfile.ZipFile(io.BytesIO(data)) as candidate:
        require(set(candidate.namelist()) == set(original.namelist()) | {spec['added']})
        require(all(candidate.read(name) == original.read(name)
                    for name in original.namelist() if name != spec['changed']))
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component', choices=sorted(COMPONENTS), required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        data = build(args.baseline.read_bytes(), args.component)
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        print(json.dumps({'component': args.component, 'sha256': hashlib.sha256(data).hexdigest(),
                          'bytes': len(data), 'changedOriginalEntries': 1, 'addedEntries': 1,
                          'otherEntriesBytePreserved': True, 'runtimeApplied': False}))
    except Exception:
        raise SystemExit('Diagnostic overlay rejected; private input values suppressed.') from None


if __name__ == '__main__':
    main()
