"""Offline parser + policy simulation. No secret/network transport is available."""
import argparse
from collections import Counter
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

from message_evaluator import ai_provider
from message_evaluator.policy_v2 import evaluate
from shared_message_contract.validation import MessageError
from . import corpus
from .ledger import Ledger, private_directory
from .profile import (ATTEMPT_CAP, BUDGET_NANO, INPUT_CAP, OUTPUT_CAP, MODEL_MS, TOTAL_MS,
                      MODELS, SPLIT_CAPS, EvaluationError, canonical, digest, profile,
                      request_body, require)


def configuration(manifest, models):
    # The shared link contract imports jsonschema. Refuse a partial environment
    # rather than turning a missing local dependency into a simulated vendor error.
    import importlib.util
    require(importlib.util.find_spec('jsonschema') is not None, 'DEPENDENCY_MISSING')
    require(type(models) is list and models and len(models) == len(set(models)), 'INVALID_MODELS')
    return {'formatVersion': 1, 'mode': 'offline_simulation', 'corpusSha256': digest(manifest),
            'profiles': {m: profile(m) for m in sorted(models)},
            'attemptCap': ATTEMPT_CAP, 'budgetNano': BUDGET_NANO, 'splitCaps': SPLIT_CAPS}


def attempt_key(case, model):
    # No untrusted case/source/reviewer ID is retained verbatim in run artifacts.
    return digest({'caseId': case['caseId'], 'model': model})


def simulated_wire(simulation, model, text):
    value = {key: simulation[key] for key in ('assessment', 'context', 'reasons')}
    value = json.loads(canonical(value))
    kind = simulation['kind']
    if kind == 'invalid_span':
        value = {'assessment': 'warning', 'context': 'clear', 'reasons': [
            {'code': 'AI_PRETEXT', 'spans': [{'start': 0, 'end': len(text) + 1}]}]}
    content = [{'type': 'output_text', 'text': canonical(value)}]
    wire = {'model': model, 'status': 'completed', 'output': [
        {'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': content}]}
    if kind == 'malformed':
        return b'not-json'
    if kind == 'duplicate_keys':
        return b'{"status":"completed","status":"incomplete"}'
    if kind == 'refusal':
        wire['output'][0]['content'] = [{'type': 'refusal', 'refusal': 'Simulated refusal'}]
    if kind == 'truncated':
        wire.update(status='incomplete', incomplete_details={'reason': 'max_output_tokens'})
    if kind == 'reasoning_item':
        wire['output'].insert(0, {'type': 'reasoning', 'summary': []})
    if kind == 'wrong_model':
        wire['model'] = 'unqualified-snapshot-2026-01-01'
    return canonical(wire).encode()


def link_lookup(outcome):
    def lookup(event):
        if outcome == 'unavailable':
            raise MessageError('PROVIDER_UNAVAILABLE')
        match = outcome == 'known_match'
        return {'schemaVersion': 1, 'checkId': event['checkId'],
                'verdict': 'high_risk' if match else 'no_known_threat_detected',
                'processingOutcome': 'complete', 'coverage': 'supported_checks_complete',
                'reasonCodes': ['KNOWN_THREAT_MATCH'] if match else ['NO_LIST_MATCH', 'BROWSER_NAVIGATION_NOT_EVALUATED'],
                'transportWarnings': [], 'threatTypes': ['SOCIAL_ENGINEERING'] if match else [],
                'lookupCount': 1, 'providerCallCount': 1, 'observedHopCount': 1,
                'scope': 'HTTP_REDIRECTS_AND_GOOGLE_LOOKUP', 'consumerAccessEnabled': False}
    return lookup


def simulate_case(case, model, ledger):
    key = attempt_key(case, model)
    if ledger.contains(key):
        return  # Interrupted reservation is unknown, never automatically retried.
    intent = case['intent']
    state = {'dispatched': False, 'outcome': 'not_dispatched', 'assessment': None,
             'reasons': [], 'fatal': None}

    def callback(request, budget):
        try:
            require(budget > 0, 'INVALID_EVALUATION_BUDGET')
            request_body(request, model)  # Exercise exact projection/schema/profile.
            fresh = ledger.reserve(key, model, intent['language'], case['split'], dispatched=True)
            require(fresh, 'CONCURRENT_ATTEMPT')
        except Exception as exc:
            state['fatal'] = exc
            raise MessageError('PROVIDER_UNAVAILABLE') from None
        state['dispatched'] = True
        kind = case['simulation']['kind']
        if kind in ('timeout', 'provider_failure'):
            state['outcome'] = 'deadline' if kind == 'timeout' else 'provider_failure'
            raise MessageError('BUDGET_LIMIT' if kind == 'timeout' else 'PROVIDER_UNAVAILABLE')
        raw = simulated_wire(case['simulation'], model, request['target']['sanitizedText'])
        state['outcome'] = {'refusal': 'refused', 'truncated': 'truncated'}.get(kind, 'schema_rejected')
        parsed = ai_provider.parse(raw, request['target']['sanitizedText'], expected_model=model)
        state.update(outcome='valid', assessment=parsed['assessment'], reasons=[r['code'] for r in parsed['reasons']])
        return parsed

    result = evaluate('offline-check', intent, ai=callback, budget_ms=TOTAL_MS,
                      lookup=link_lookup(case['simulation']['linkOutcome']))
    if state['fatal'] is not None:
        raise state['fatal']
    if not state['dispatched']:
        require(ledger.reserve(key, model, intent['language'], case['split'], dispatched=False), 'CONCURRENT_ATTEMPT')
    label_match = None
    if state['outcome'] == 'valid':
        label_match = (state['assessment'] == case['expected']['assessment'] and
                       sorted(state['reasons']) == sorted(case['expected']['reasonCodes']))
    ledger.finish(key, {
        'responseOutcome': state['outcome'], 'assessment': state['assessment'],
        'reasonCodes': sorted(state['reasons']), 'labelMatch': label_match,
        'reviewStatus': case['review']['status'], 'verdict': result['verdict'],
        'processingOutcome': result['processingOutcome'], 'coverage': result['coverage'],
        'independentThreatMatchPresent': any(e['outcome'] == 'match' for e in result['evidence']),
    })


def aggregate(rows):
    responses, assessments, reasons, pipeline, comparisons = Counter(), Counter(), Counter(), Counter(), Counter()
    pending = dispatched = skips = independent = 0
    for row in rows:
        dispatched += row['dispatched']
        if row['state'] == 'reserved':
            pending += 1
            responses['pending_unknown' if row['dispatched'] else 'pending_pipeline'] += 1
            continue
        value = json.loads(row['result'])
        responses[value['responseOutcome']] += 1
        if not row['dispatched']:
            skips += 1
        if value['assessment'] is not None:
            assessments[value['assessment']] += 1
            reasons.update(value['reasonCodes'])
            prefix = 'engineering' if value['reviewStatus'] == 'engineering_only' else 'asserted_review'
            comparisons[prefix + ('_label_match' if value['labelMatch'] else '_label_mismatch')] += 1
        pipeline['|'.join(value[k] for k in ('verdict', 'processingOutcome', 'coverage'))] += 1
        independent += int(value['independentThreatMatchPresent'])
    return {'journalEntries': len(rows), 'completedPipelineEvaluations': len(rows) - pending,
            'simulatedDispatches': dispatched, 'actualProviderDispatches': 0,
            'guardSkips': skips, 'pendingUnknownOrInterrupted': pending,
            'simulatedResponseOutcomes': dict(sorted(responses.items())),
            'validAiAssessments': dict(sorted(assessments.items())),
            'validAiReasonCategories': dict(sorted(reasons.items())),
            'pipelineVerdictProcessingCoverage': dict(sorted(pipeline.items())),
            'labelComparisons': dict(sorted(comparisons.items())),
            'independentThreatMatchResultCount': independent,
            'simulatedReservedCostNano': sum(r['reserved'] for r in rows)}


def report(ledger):
    rows = ledger.snapshot()
    return {
        'schemaVersion': 1, 'mode': 'offline_simulation', 'qualification': 'none',
        'runIdentitySha256': digest(ledger.config),
        'corpusSha256': ledger.config['corpusSha256'],
        'profileSha256': {m: digest(p) for m, p in ledger.config['profiles'].items()},
        'actualProviderCostNano': 0, 'actualTokenUsage': None,
        'inputTokenAdmission': 'unqualified_live_blocked',
        'deadlineMeasurements': 'not_measured_simulation',
        'cohortQualityGates': 'not_implemented_or_evaluated',
        'billingSettlement': 'not_exercised', 'spanSemanticCorrectness': 'not_independently_scored',
        'reviewEvidence': 'manifest_attestations_not_independently_verified',
        'labelComparisonDefinition': 'exact_assessment_and_reason_set',
        'independentThreatMetric': 'result_match_count_not_retention_qualification',
        'costMeaning': 'conservative_simulated_reservations_not_vendor_spend',
        'caps': {'generationAttempts': ATTEMPT_CAP, 'proposedBudgetNano': BUDGET_NANO,
                 'inputTokensNotYetQualified': INPUT_CAP, 'outputTokens': OUTPUT_CAP,
                 'modelDeadlineMs': MODEL_MS, 'totalDeadlineMs': TOTAL_MS},
        'totals': aggregate(rows),
        'byModelLanguageSplit': [dict(model=model, language=lang, split=split,
            **aggregate([r for r in rows if (r['model'], r['language'], r['split']) == (model, lang, split)]))
            for model in sorted(ledger.config['profiles']) for lang in ('en', 'es') for split in SPLIT_CAPS],
    }


def export_report(ledger, stop_reason='complete'):
    value = report(ledger)
    value['stopReason'] = stop_reason
    # Unique private staging files cannot block resume after a process crash.
    fd, name = tempfile.mkstemp(prefix='.report-', dir=ledger.directory)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(canonical(value) + '\n')
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, ledger.directory / 'report.json')
    finally:
        temporary.unlink(missing_ok=True)
    return value


def run(manifest, directory, models, *, report_only=False):
    corpus.validate(manifest)
    config = configuration(manifest, models)
    require(not report_only or (Path(directory) / 'journal.sqlite3').is_file(), 'REPORT_REQUIRES_EXISTING_RUN')
    directory = private_directory(directory)
    lock_path = directory / 'run.lock'
    require(not lock_path.is_symlink(), 'PRIVATE_LOCK_REQUIRED')
    lock = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    ledger = None
    try:
        require(os.fstat(lock).st_mode & 0o777 == 0o600, 'PRIVATE_LOCK_REQUIRED')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = Ledger(directory, config)
        if report_only:
            return export_report(ledger, 'report_only')
        try:
            for model in sorted(models):
                for case in manifest['cases']:
                    if case['split'] not in SPLIT_CAPS:
                        continue  # Held-out/operational cases are validation-only here.
                    simulate_case(case, model, ledger)
        except EvaluationError as exc:
            if str(exc) != 'EXPERIMENT_CAP_REACHED':
                raise
            return export_report(ledger, 'experiment_cap_reached')
        return export_report(ledger)
    finally:
        if ledger is not None:
            ledger.close()
        os.close(lock)


def main(argv=None):
    class PrivateArgumentParser(argparse.ArgumentParser):
        def error(self, message):
            # argparse otherwise echoes arbitrary argument values/paths.
            raise EvaluationError('INVALID_ARGUMENTS')

    parser = PrivateArgumentParser(description='Offline-only message AI parser/policy simulation; no paid/live path.')
    parser.add_argument('--corpus', required=True)
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--model', action='append', choices=sorted(MODELS))
    parser.add_argument('--report-only', action='store_true')
    try:
        args = parser.parse_args(argv)
        manifest = corpus.load(args.corpus)
        result = run(manifest, args.run_dir, args.model or list(MODELS), report_only=args.report_only)
        # Only closed metadata. Never print paths, case identifiers or exceptions.
        print(canonical({'mode': result['mode'], 'runIdentitySha256': result['runIdentitySha256'],
                         'simulatedDispatches': result['totals']['simulatedDispatches'],
                         'actualProviderDispatches': 0, 'stopReason': result['stopReason']}))
        return 0
    except EvaluationError as exc:
        print(canonical({'error': str(exc)}), file=sys.stderr)
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, RecursionError):
        print(canonical({'error': 'OFFLINE_EVALUATION_FAILED'}), file=sys.stderr)
    return 2
