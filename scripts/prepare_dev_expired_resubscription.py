#!/usr/bin/env python3
"""Offline six-entry PR131 overlay on exact Dev Handoff 9; never deploy."""
import argparse
import ast
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE_REVISION = '034e566ac3f5a79b9034d9b50a5cbf145b11ac41'
BASE_SHA = '4c9f18d5c3d64c596487a35d7e1f1287cf711507ac87f7ff6756466c793bb4f1'
WRITER = 'shared_check_authority/entitlements.py'
SOURCE_PINS = {
    WRITER: '0683d8abaa4cd8e16cb9c32db92d63be7767eb5b7a947235e4d5e9ee68311bcb',
    'shared_play_verification/proof.py': '81fb9bfccd200b97e51a639b706cf2cfa0d01d773c6f92a462ada334e6697e3b',
    'shared_purchase_ownership/service.py': 'c133a769ae101b3330e484ac75acc4b58786161f1acaca81e49f2ec322686bf1',
    'v1_play_handoff/app.py': '7fe01208f4d22c59eaaee6eb74e8e72840a8a8b9ca90835ca6ade65ef7720fcf',
    'v1_play_handoff/diagnostics.py': 'c68fa64d1cb94224b57f76f84bf54ab7ab8636e7b9189286a14531feaa5fa80f',
    'v1_play_handoff/service.py': '4d134557dca8a8c54dcb962bed51cb3890088ba6ae97808317ca3643b39bb57a',
}
BASE_PINS = {
    WRITER: 'd56b519eb333a57b819a285ea34477ef255143b2249c25d0492c836f85757177',
    'shared_play_verification/proof.py': '3437a7ec176f4c375b0175ba4077be712bd8728efbf3923b31186e5fd5b577bb',
    'shared_purchase_ownership/service.py': '584b03633278ee7a775831b290a1c6f65819efc7f519ee452f9149cbc4f5b2c5',
    'v1_play_handoff/app.py': 'b9a8769ce71ced58e07d81d222d4f0983fefcb9c333ade30519e3ef7f7ca8482',
    'v1_play_handoff/diagnostics.py': '586433f53166de9d25553ea8666215384fbd86118dd06f723e9d3a248bfefe13',
    'v1_play_handoff/service.py': '8cc95103246de5c8451efaf5e7370ce59ebe8595ca65874f38c7e8da051f7eaf',
}
NEW_METHODS = {'commit_verified_resubscription', '_commit_foreground_paid'}
REPLACED_METHODS = {'commit_verified_paid', '_commit_paid'}


def require(value):
    if not value:
        raise ValueError('Exact resubscription overlay rejected.')


def sha(value):
    return hashlib.sha256(value).hexdigest()


def shape(node):
    return ast.dump(node, include_attributes=False)


def definition(source, name):
    matches = [n for n in ast.walk(ast.parse(source))
               if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name]
    require(len(matches) == 1)
    node = matches[0]
    first = min([node.lineno] + [d.lineno for d in node.decorator_list])
    lines = source.decode().splitlines(keepends=True)
    return node, ''.join(lines[first - 1:node.end_lineno]).encode()


def replace_definition(source, name, replacement):
    node, _ = definition(source, name)
    first = min([node.lineno] + [d.lineno for d in node.decorator_list])
    lines = source.decode().splitlines(keepends=True)
    return b''.join([''.join(lines[:first - 1]).encode(), replacement,
                     ''.join(lines[node.end_lineno:]).encode()])


def merge_writer(original, candidate, base):
    """Keep original retention code; qualify every changed AST against PR131."""
    require(sha(original) == BASE_PINS[WRITER] and sha(candidate) == SOURCE_PINS[WRITER])
    for name in REPLACED_METHODS:
        require(shape(definition(original, name)[0]) == shape(definition(base, name)[0]))
    result = original
    for name in sorted(REPLACED_METHODS):
        result = replace_definition(result, name, definition(candidate, name)[1])
    _, old = definition(result, 'commit_verified_paid')
    result = replace_definition(result, 'commit_verified_paid', old + b'\n\n' + b'\n\n'.join(
        definition(candidate, name)[1] for name in ('commit_verified_resubscription', '_commit_foreground_paid')))
    _, capability = definition(candidate, '_ExpiredPaidRootMigration')
    require(result.count(b'@dataclass(frozen=True)\nclass TrustedLifecycleWorker:') == 1)
    result = result.replace(b'@dataclass(frozen=True)\nclass TrustedLifecycleWorker:',
        capability + b'\n\n\n@dataclass(frozen=True)\nclass TrustedLifecycleWorker:', 1)
    # Retain the deployed _commit signature/audit-expiry blocks, adding only CAS.
    signature = b'request_digest=None, audit_expires_at=None):'
    require(result.count(signature) == 1)
    result = result.replace(signature, b'request_digest=None, audit_expires_at=None, exact_access=False):', 1)
    before = b'        items += list(extras) + [self._put(row, condition, values), self._put(audit)]\n'
    after = b'''        access_put = self._put(row, condition, values)
        if exact_access:
            from .purchase_usage import exact_condition
            if old is None:
                raise AuthorityError('STORE_OBSERVATION_CHANGED')
            access_put = {'Put': {'TableName': self.a.s.authority_table, 'Item': row, **exact_condition(old)}}
        items += list(extras) + [access_put, self._put(audit)]
'''
    require(result.count(before) == 1)
    result = result.replace(before, after, 1)
    compile(result, WRITER, 'exec')
    for name in REPLACED_METHODS | NEW_METHODS | {'_ExpiredPaidRootMigration'}:
        require(shape(definition(result, name)[0]) == shape(definition(candidate, name)[0]))
    # Restore exactly the approved deltas, proving all prior retention AST stays.
    normalized = result.replace(after, before, 1).replace(
        b'request_digest=None, audit_expires_at=None, exact_access=False):', signature, 1)
    for name in REPLACED_METHODS:
        normalized = replace_definition(normalized, name, definition(original, name)[1])
    for name in NEW_METHODS | {'_ExpiredPaidRootMigration'}:
        normalized = replace_definition(normalized, name, b'')
    require(shape(ast.parse(normalized)) == shape(ast.parse(original)))
    return result


def compressed_payload(data, info):
    start = info.header_offset
    require(data[start:start + 4] == b'PK\x03\x04')
    names, extras = struct.unpack_from('<HH', data, start + 26)
    start += 30 + names + extras
    return data[start:start + info.compress_size]


def build(baseline, sources=None):
    require(__debug__)  # Normal Python only; no optimized execution.
    require(len(baseline) <= 40 * 1024 * 1024 and sha(baseline) == BASE_SHA)
    sources = sources if sources is not None else {name: (ROOT / 'src' / name).read_bytes() for name in SOURCE_PINS}
    require(set(sources) == set(SOURCE_PINS) and all(sha(sources[n]) == h for n, h in SOURCE_PINS.items()))
    base = subprocess.check_output(['git', '-C', str(ROOT), 'show', BASE_REVISION + ':src/' + WRITER])
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(baseline)) as old, zipfile.ZipFile(output, 'w') as new:
        names = old.namelist()
        require(len(names) <= 2000 and len(names) == len(set(names)) and set(SOURCE_PINS) <= set(names)
                and all(not n.startswith('/') and '..' not in Path(n).parts for n in names)
                and sum(i.file_size for i in old.infolist()) <= 80 * 1024 * 1024)
        require(all(sha(old.read(n)) == h for n, h in BASE_PINS.items()))
        replacements = dict(sources)
        replacements[WRITER] = merge_writer(old.read(WRITER), sources[WRITER], base)
        for info in old.infolist():
            new.writestr(copy.copy(info), replacements.get(info.filename, old.read(info.filename)))
        new.comment = old.comment
    artifact = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(baseline)) as old, zipfile.ZipFile(io.BytesIO(artifact)) as new:
        require(old.namelist() == new.namelist() and old.comment == new.comment)
        changed = {n for n in old.namelist() if old.read(n) != new.read(n)}
        require(changed == set(SOURCE_PINS))
        for name in set(old.namelist()) - changed:
            before, after = old.getinfo(name), new.getinfo(name)
            require(old.read(name) == new.read(name)
                    and compressed_payload(baseline, before) == compressed_payload(artifact, after))
            require(all(getattr(before, f) == getattr(after, f) for f in (
                'filename', 'date_time', 'compress_type', 'comment', 'extra', 'create_system',
                'create_version', 'extract_version', 'flag_bits', 'volume', 'internal_attr',
                'external_attr', 'CRC', 'compress_size', 'file_size')))
        return artifact, {'schemaVersion': 1, 'baselineSha256': sha(baseline),
            'artifactSha256': sha(artifact), 'artifactBytes': len(artifact),
            'archiveEntries': len(old.namelist()), 'changedEntries': sorted(changed),
            'addedOrRemovedEntries': 0, 'unrelatedEntriesByteAndCompressedPayloadExact': len(old.namelist()) - len(changed),
            'unrelatedMetadataExactExceptNecessaryArchiveOffsets': True,
            'retentionAstPreservedOutsideApprovedMigrationDelta': True,
            'entrySha256': {n: sha(new.read(n)) for n in sorted(changed)}, 'runtimeApplied': False}


def exclusive(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())


def main():
    if not __debug__:
        raise SystemExit('Optimized Python is not supported.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    try:
        artifact, evidence = build(args.baseline.read_bytes())
        require(not os.path.lexists(args.output) and not os.path.lexists(args.manifest))
        exclusive(args.output, artifact)
        exclusive(args.manifest, (json.dumps(evidence, indent=2) + '\n').encode())
        print(json.dumps(evidence))
    except Exception:
        raise SystemExit('Exact resubscription overlay rejected; details suppressed.') from None


if __name__ == '__main__':
    main()
