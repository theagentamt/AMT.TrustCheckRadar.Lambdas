#!/usr/bin/env python3
"""Offline fixed prepare-auth diagnostics overlay; exact live8 baseline, never deploy."""
import argparse
import ast
import base64
import hashlib
import io
import os
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASELINE_CODE = 'riLQ3bwsUr95HPJyjCviZCqPfmloyoG515TjucsmjAY='
SECURITY = 'shared_history/security.py'
CORE = 'shared_check_authority/core.py'
ADDED = 'shared_check_authority/prepare_auth_diagnostics.py'
BASELINE_ENTRIES = {
    SECURITY: '71d562ee364c7a91bba0a10694316cfb1f70a552861554f19bed0a613492c5d8',
    CORE: '883b6ece99bb476e952645e4922f4ebc95bb3533556a5ac723b400de176a6324',
}


def require(value):
    if not value:
        raise ValueError('Prepare authentication diagnostic overlay rejected.')


def signature(node):
    return ast.dump(node, include_attributes=False)


class NormalizeSecurity(ast.NodeTransformer):
    def visit_Raise(self, node):
        if isinstance(node.exc, ast.Call) and isinstance(node.exc.func, ast.Name) and node.exc.func.id == '_jwt_rejection':
            require(len(node.exc.args) == 1 and isinstance(node.exc.args[0], ast.Constant)
                    and type(node.exc.args[0].value) is str and not node.exc.keywords and node.cause is None)
            return ast.Raise(exc=ast.Call(func=ast.Name(id='HistoryError', ctx=ast.Load()), args=[
                ast.Constant('UNAUTHORIZED'), ast.Constant('A verified Cognito access token is required.')], keywords=[]), cause=None)
        return self.generic_visit(node)

    def visit_BoolOp(self, node):
        node = self.generic_visit(node)
        if isinstance(node.op, ast.Or):
            node.values = [value for child in node.values for value in
                           (child.values if isinstance(child, ast.BoolOp) and isinstance(child.op, ast.Or) else [child])]
        return node

    def visit_FunctionDef(self, node):
        if node.name == '_jwt_rejection':
            return None
        node = self.generic_visit(node)
        if node.name != 'jwt_subject':
            return node
        # Consecutive rejects with identical raise semantics retain OR order and
        # short-circuit evaluation, including the sole conditional clock sample.
        result = []
        for statement in node.body:
            if (result and isinstance(statement, ast.If) and not statement.orelse
                    and isinstance(result[-1], ast.If) and not result[-1].orelse
                    and len(statement.body) == len(result[-1].body) == 1
                    and isinstance(statement.body[0], ast.Raise)
                    and signature(statement.body[0]) == signature(result[-1].body[0])):
                prior = result[-1]
                prior.test = self.visit_BoolOp(ast.BoolOp(op=ast.Or(), values=[prior.test, statement.test]))
            else:
                result.append(statement)
        node.body = result
        return node


class StripCoreDiagnostic(ast.NodeTransformer):
    def __init__(self):
        self.function = None
        self.hooks = 0

    def visit_FunctionDef(self, node):
        if node.name == '_record_prepare_auth_failure':
            return None
        previous, self.function = self.function, node.name
        node = self.generic_visit(node)
        self.function = previous
        return node

    def visit_ExceptHandler(self, node):
        kept = [statement for statement in node.body if not (
            isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Name)
            and statement.value.func.id == '_record_prepare_auth_failure')]
        if len(kept) != len(node.body):
            removed = [statement for statement in node.body if statement not in kept]
            expected = ast.parse('_record_prepare_auth_failure(event, error)').body[0]
            require(self.function == '_account' and node.name == 'error' and len(removed) == 1
                    and signature(removed[0]) == signature(expected))
            self.hooks += 1
            node.name = None
            node.body = kept
        return self.generic_visit(node)


def qualified_security(original, candidate):
    require(hashlib.sha256(original).hexdigest() == BASELINE_ENTRIES[SECURITY])
    helper, _ = function_lines(candidate, '_jwt_rejection')
    expected = ast.parse('''def _jwt_rejection(predicate):
    error = HistoryError("UNAUTHORIZED", "A verified Cognito access token is required.")
    error._jwt_auth_predicate = predicate
    return error
''').body[0]
    require(signature(helper) == signature(expected))
    before = NormalizeSecurity().visit(ast.parse(original))
    after = NormalizeSecurity().visit(ast.parse(candidate))
    require(signature(before) == signature(after))
    compile(candidate, SECURITY, 'exec')


def function_lines(source, name):
    tree = ast.parse(source)
    nodes = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name]
    require(len(nodes) == 1)
    node = nodes[0]
    return node, ''.join(source.decode().splitlines(keepends=True)[node.lineno - 1:node.end_lineno])


def overlay_core(original, candidate):
    require(hashlib.sha256(original).hexdigest() == BASELINE_ENTRIES[CORE])
    old, _ = function_lines(original, '_account')
    _, replacement = function_lines(candidate, '_account')
    helper, hook = function_lines(candidate, '_record_prepare_auth_failure')
    expected = ast.parse('''def _record_prepare_auth_failure(event, error):
    try:
        from .prepare_auth_diagnostics import record_failure
        record_failure(event, getattr(error, '_jwt_auth_predicate', None))
    except Exception:
        pass
''').body[0]
    require(signature(helper) == signature(expected))
    lines = original.decode().splitlines(keepends=True)
    source = ''.join(lines[:old.lineno - 1]) + replacement + ''.join(lines[old.end_lineno:])
    require(source.count('\nOWNER_POLICY =') == 1)
    source = source.replace('\nOWNER_POLICY =', '\n' + hook + '\nOWNER_POLICY =', 1).encode()
    stripper = StripCoreDiagnostic()
    stripped = stripper.visit(ast.parse(source))
    require(stripper.hooks == 1 and signature(ast.parse(original)) == signature(stripped))
    compile(source, CORE, 'exec')
    return source


def build(baseline):
    require(len(baseline) <= 40 * 1024 * 1024)
    require(base64.b64encode(hashlib.sha256(baseline).digest()).decode() == BASELINE_CODE)
    security = (ROOT / 'src' / SECURITY).read_bytes()
    core = (ROOT / 'src' / CORE).read_bytes()
    added = (ROOT / 'src' / ADDED).read_bytes()
    compile(added, ADDED, 'exec')
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(baseline)) as original, zipfile.ZipFile(output, 'w') as target:
        names = original.namelist()
        require(len(names) <= 2000 and len(names) == len(set(names)) and ADDED not in names
                and all(not name.startswith('/') and '..' not in Path(name).parts for name in names)
                and sum(info.file_size for info in original.infolist()) <= 80 * 1024 * 1024)
        qualified_security(original.read(SECURITY), security)
        replacements = {SECURITY: security, CORE: overlay_core(original.read(CORE), core)}
        for info in original.infolist():
            target.writestr(info, replacements.get(info.filename, original.read(info.filename)))
        info = zipfile.ZipInfo(ADDED, date_time=(2026, 10, 17, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        target.writestr(info, added)
        target.comment = original.comment
    result = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(baseline)) as original, zipfile.ZipFile(io.BytesIO(result)) as target:
        require(set(target.namelist()) == set(original.namelist()) | {ADDED})
        require(all(target.read(name) == original.read(name) for name in original.namelist()
                    if name not in replacements))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        data = build(args.baseline.read_bytes())
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as target:
            target.write(data);target.flush();os.fsync(target.fileno())
        import json
        print(json.dumps({'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                          'changedOriginalEntries': 2, 'addedEntries': 1,
                          'unrelatedDeployedBytesPreserved': True, 'runtimeApplied': False}))
    except Exception:
        raise SystemExit('Prepare authentication overlay rejected; input values suppressed.') from None


if __name__ == '__main__':
    main()
