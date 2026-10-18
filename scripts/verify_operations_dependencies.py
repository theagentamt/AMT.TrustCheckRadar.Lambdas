"""Offline source-inspection ZIP join; never qualifies deployment or reads AWS."""
from hashlib import sha256
from pathlib import Path, PurePosixPath
import argparse
import json
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
AUTHORITY_PACKAGES = frozenset({
    'url_consumer', 'url_lease_recovery', 'v1_entitlements', 'v1_authority_deletion',
    'governed_history', 'v1_play_handoff', 'play_lifecycle_ingress',
    'play_lifecycle_worker', 'play_token_deletion', 'message_consumer',
    'recovery_consumer', 'result_feedback', 'account_export_api',
})
EVALUATOR_PACKAGES = frozenset({'message_evaluator', 'recovery_evaluator'})
PACKAGES = AUTHORITY_PACKAGES | EVALUATOR_PACKAGES
PROTOCOL_FILES = frozenset({
    'shared_check_authority/' + name + '.py' for name in (
        '__init__', 'core', 'deletion', 'engineering', 'entitlements', 'expiry',
        'inventory', 'operations_binding', 'operations_callers', 'operations_compat',
        'operations_ingestion', 'operations_retirement', 'prepare_auth_diagnostics',
        'purchase_usage', 'recovery', 'runtime', 'summary')
}) | frozenset({'shared_operations/__init__.py', 'shared_operations/receipts.py',
               'shared_operations/projection.py'})
CALLERS = frozenset({'url_consumer', 'message_consumer', 'recovery_consumer'})


def _require(value):
    if not value:
        raise ValueError('OPERATIONS_DEPENDENCY_MISMATCH')


def _path(value):
    _require(type(value) is str)
    item = PurePosixPath(value)
    _require(not item.is_absolute() and item.as_posix() == value and '..' not in item.parts)
    return value


def verify(manifest, artifacts, *, source_root=ROOT):
    """Source hashes + critical member closure, including coordinated old workers.

    The reviewed manifest and this checker must come from the same source. ZIPs
    omit dependencies and are explicitly inspection-only, regardless of PASS.
    """
    _require(type(manifest) is dict and type(manifest.get('schemaVersion')) is int and manifest.get('schemaVersion') == 1
        and manifest.get('actionReady') is False and manifest.get('distributionQualified') is False
        and manifest.get('awsReadPerformed') is False)
    entries = manifest.get('artifacts')
    _require(type(entries) is list and len(entries) == len(PACKAGES)
        and {entry.get('function') for entry in entries if type(entry) is dict} == PACKAGES)
    pins = manifest.get('sourcePins')
    _require(type(pins) is dict and pins)
    for group in ('sourcePins', 'contractPins', 'requirementsPins'):
        values = manifest.get(group)
        _require(type(values) is dict and values)
        for source, digest in values.items():
            _path(source)
            _require(type(digest) is str and re.fullmatch('[0-9a-f]{64}', digest)
                     and sha256((source_root / source).read_bytes()).hexdigest() == digest)
    for entry in entries:
        name = entry['function']
        _require(entry.get('file') == name + '.zip' and entry.get('skipDependencies') is True
            and entry.get('distributionQualified') is False)
        data = (artifacts / entry['file']).read_bytes()
        _require(len(data) == entry.get('bytes') and sha256(data).hexdigest() == entry.get('sha256'))
        members = entry.get('sourceMembers')
        contract_members = entry.get('contractMembers')
        _require(type(members) is dict and members and type(contract_members) is dict)
        if name in AUTHORITY_PACKAGES:
            _require(all(members.get(member) == 'src/' + member for member in PROTOCOL_FILES))
        else:
            _require(not any(member.startswith(('shared_check_authority/', 'shared_operations/')) for member in members))
        if name in CALLERS:
            member = 'service.py' if name == 'url_consumer' else name + '/service.py'
            _require(members.get(member) == 'src/' + name + '/service.py')
            _require(members.get('app.py' if name == 'url_consumer' else name + '/app.py') == 'src/' + name + '/app.py')
        with zipfile.ZipFile(artifacts / entry['file']) as archive:
            names = archive.namelist()
            _require(len(names) == len(set(names)) and 'app.py' in names)
            _require(not any(item.startswith('operations_reporting/') for item in names))
            if name in EVALUATOR_PACKAGES:
                _require(not any(item.startswith(('shared_check_authority/', 'shared_operations/')) for item in names))
            for group_members, group_pins in ((members, pins), (contract_members, manifest['contractPins'])):
                for member, source in group_members.items():
                    _path(member); _path(source)
                    _require(source in group_pins and sha256(archive.read(member)).hexdigest() == group_pins[source])
            for member in names:
                if member.endswith('.py') and member not in members and member not in contract_members:
                    _require(member == 'app.py' and archive.read(member) == f'from {name}.app import lambda_handler\n'.encode())
    return len(entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    args = parser.parse_args()
    count = verify(json.loads(args.manifest.read_text()), args.artifacts)
    print(f'{count} source-inspection archives exactly joined; actionReady=false; native/runtime/IAM/cost qualification pending')


if __name__ == '__main__':
    main()
