"""Redacted lifecycle CLI. An empty reviewed approval registry blocks all runs."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .. import corpus
from ..profile import canonical, EvaluationError, require
from .authority import (Authority, bounded_json, private, provision_authority,
                        validate_authorization)
from .engine import credential_loader, execute_case, export_report
from .transport import OfficialTransport


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise EvaluationError('INVALID_ARGUMENTS')
    parser = Parser(description='Controlled evaluation lifecycle; no approved live experiment is shipped.')
    parser.add_argument('action', choices=['init', 'preflight', 'report', 'run'])
    parser.add_argument('--corpus', required=True)
    parser.add_argument('--authorization', required=True)
    parser.add_argument('--authority-dir', required=True)
    parser.add_argument('--report-dir')
    store = None
    try:
        args = parser.parse_args(argv)
        require(args.action not in ('report', 'run') or args.report_dir is not None, 'REPORT_DIRECTORY_REQUIRED')
        private(args.authorization)
        authorization = bounded_json(args.authorization)
        manifest = corpus.load(args.corpus)
        validate_authorization(authorization, manifest, args.authority_dir, execution=args.action != 'report', audit=args.action == 'report')
        if args.report_dir is not None:
            output = Path(args.report_dir).resolve(); authority = Path(args.authority_dir).resolve()
            require(not output.is_relative_to(authority) and not authority.is_relative_to(output), 'REPORT_AUTHORITY_OVERLAP')
        if args.action == 'init':
            provision_authority(args.authority_dir, authorization, manifest)
        else:
            store = Authority(args.authority_dir, authorization, manifest, audit=args.action == 'report')
            if args.action == 'run':
                require(authorization['executionMode'] == 'controlled_live', 'LIVE_MODE_REQUIRED')
                transport = OfficialTransport(credential_loader(store))
                for model in sorted(authorization['profiles']):
                    for case in manifest['cases']:
                        if case['split'] not in ('smoke', 'development'):
                            continue
                        try:
                            outcome = execute_case(store, case, model, transport)
                        except EvaluationError as exc:
                            if str(exc) not in ('EXPERIMENT_CAP_REACHED', 'EXPERIMENT_HALTED', 'AUTHORIZATION_EXPIRED'):
                                raise
                            outcome = 'experiment_stopped'
                        if outcome in ('experiment_halted', 'experiment_stopped'):
                            export_report(store, args.report_dir)
                            print(canonical({'status': 'experiment_stopped'}))
                            return 0
            if args.action in ('run', 'report'):
                export_report(store, args.report_dir)
        print(canonical({'status': 'ok', 'action': args.action}))
        return 0
    except EvaluationError as exc:
        # Only our closed error codes; external transport/credential errors are
        # normalized before reaching this boundary.
        print(canonical({'error': str(exc)}), file=sys.stderr)
    except (OSError, ValueError, TypeError, KeyError, RecursionError, sqlite3.Error):
        print(canonical({'error': 'CONTROLLED_EVALUATION_FAILED'}), file=sys.stderr)
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass
    return 2
