"""Single-count/single-generation adapter with durable dispatch admission."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from message_evaluator import ai_provider
from ..profile import INPUT_CAP, OUTPUT_CAP, canonical, digest, require, EvaluationError
from ..ledger import private_directory
from . import protocol
from .authority import private, validate_authorization


def credential_loader(authority):
    def load():
        # The engine has already reserved and revalidated this dispatch. Recheck
        # expiry/revocation again immediately before touching credential bytes.
        authority.dispatch_gate()
        path = authority.directory / 'provider-key.txt'
        private(path)
        with path.open('rb') as handle:
            raw = handle.read(514)
        require(16 <= len(raw) <= 512 and hashlib.sha256(raw).hexdigest() == authority.authorization['credentialSha256'], 'CREDENTIAL_IDENTITY_MISMATCH')
        value = raw.decode('ascii')
        require(re.fullmatch(r'[!-~]{16,512}', value), 'CREDENTIAL_UNAVAILABLE')
        return value
    return load


def _result(outcome, *, count=None, usage=None, assessment=None, reasons=(), received=False):
    return {'outcome': outcome, 'inputTokens': count, 'usage': usage, 'assessment': assessment,
            'reasonCodes': sorted(reasons), 'responseReceived': received}


def execute_case(authority, case, model, transport, *, clock=time.monotonic):
    require(transport.mode == authority.authorization['executionMode'], 'TRANSPORT_MODE_MISMATCH')
    require(model in authority.authorization['profiles'] and any(case == x for x in authority.manifest['cases']), 'CASE_NOT_AUTHORIZED')
    require(case['split'] in ('smoke', 'development'), 'SPLIT_NOT_AUTHORIZED')
    # This adapter assesses eligible incoming text only. It is not the app's
    # policy/Google/billing pipeline and does not claim its completion metrics.
    if case['intent']['target']['speakerRole'] != 'other':
        return 'ineligible_speaker'
    generation, count, binding = protocol.request_binding(case['intent'], model)
    generation_bytes, count_bytes = canonical(generation).encode(), canonical(count).encode()
    casekey = digest({'case': case['caseId'], 'model': model})
    deadline = clock() + 18

    def dispatch(phase, payload):
        require(clock() < deadline, 'EVALUATION_DEADLINE')
        authority.dispatch_gate()
        return transport.post(phase, payload, min(deadline, clock() + 8))

    existing = authority.get(casekey, 'count')
    if existing is None:
        if not authority.reserve(casekey, 'count', model, case['intent']['language'], case['split'], binding):
            return 'already_reserved'
        try:
            raw = dispatch('count', count_bytes)
        except Exception:
            authority.finish(casekey, 'count', _result('transport_failure'))
            return 'count_transport_failure'
        try:
            number = protocol.count_result(raw)
            outcome = 'counted' if number <= INPUT_CAP else 'input_limit'
            authority.finish(casekey, 'count', _result(outcome, count=number, received=True))
        except EvaluationError:
            authority.finish(casekey, 'count', _result('count_invalid', received=True))
            return 'count_invalid'
        existing = authority.get(casekey, 'count')
    require(existing['binding'] == binding, 'ATTEMPT_IDENTITY_MISMATCH')
    if existing['state'] != 'finished':
        return 'count_pending_unknown'
    counted = json.loads(existing['result'])
    if counted['outcome'] != 'counted':
        return counted['outcome']
    if not authority.reserve(casekey, 'generation', model, case['intent']['language'], case['split'], binding):
        return 'already_reserved'
    try:
        raw = dispatch('generation', generation_bytes)
    except Exception:
        authority.finish(casekey, 'generation', _result('transport_failure'))
        return 'generation_transport_failure'
    usage = protocol.usage(raw, model)
    try:
        envelope = protocol.parse_json(raw)
    except EvaluationError:
        envelope = {}
    mismatch = (envelope.get('service_tier') != 'default' or
                (usage is not None and (usage['inputTokens'] != counted['inputTokens'] or
                                       usage['inputTokens'] > INPUT_CAP or usage['outputTokens'] > OUTPUT_CAP)))
    if mismatch:
        # Price-tier drift or count disagreement invalidates further reservations
        # globally, even if this particular model output was semantically usable.
        authority.finish(casekey, 'generation', _result('usage_mismatch', usage=usage, received=True), halt=True)
        return 'experiment_halted'
    try:
        parsed = ai_provider.parse(raw, case['intent']['target']['sanitizedText'], expected_model=model)
        result = _result('valid', usage=usage, assessment=parsed['assessment'],
                         reasons=[r['code'] for r in parsed['reasons']], received=True)
    except Exception:
        result = _result('assessment_rejected', usage=usage, received=True)
    authority.finish(casekey, 'generation', result)
    return result['outcome']


def _aggregate(authority, rows, planned):
    fault = authority.authorization['executionMode'] == 'fault_test'
    phases, outcomes, assessments = Counter(), Counter(), Counter()
    usage_totals = Counter(); unknown_usage = known_usage = confirmed = pending = 0
    for row in rows:
        phases[row['phase']] += 1
        if row['state'] == 'reserved':
            pending += 1
            outcomes[row['phase'] + ':pending_unknown'] += 1
            if row['phase'] == 'generation': unknown_usage += 1
            continue
        value = json.loads(row['result'])
        outcomes[row['phase'] + ':' + value['outcome']] += 1
        confirmed += int(value['responseReceived'])
        if value['assessment'] is not None:
            assessments[value['assessment']] += 1
        if row['phase'] == 'generation':
            if value['usage'] is None:
                unknown_usage += 1
            else:
                known_usage += 1
                usage_totals.update(value['usage'])
    eligible = sum(case['intent']['target']['speakerRole'] == 'other' for case in planned)
    return {
        'plannedModelCases': len(planned), 'incomingSpeakerEligibleCases': eligible,
        'ineligibleSpeakerCases': len(planned) - eligible,
        'eligibleCasesWithoutCountReservation': eligible - phases['count'],
        'attemptReservationsByPhase': dict(phases), 'outcomes': dict(outcomes),
        'validAiAssessments': dict(assessments), 'pendingUnknownAttempts': pending,
        'actualProviderAttemptReservations': 0 if fault else len(rows),
        'simulatedAttemptReservations': len(rows) if fault else 0,
        'confirmedActualProviderResponses': 0 if fault else confirmed,
        'generationKnownUsageTotals': dict(usage_totals), 'generationAttemptsWithKnownUsage': known_usage,
        'generationAttemptsWithUnknownUsage': unknown_usage,
        'reservedCostNano': sum(row['reserved'] for row in rows),
    }


def report(authority):
    halted, rows = authority.snapshot()
    selected = [case for case in authority.manifest['cases'] if case['split'] in ('smoke', 'development')]
    models = sorted(authority.authorization['profiles'])
    fault = authority.authorization['executionMode'] == 'fault_test'
    return {
        'schemaVersion': 1, 'mode': authority.authorization['executionMode'],
        'authorizationSha256': digest(authority.authorization), 'corpusSha256': digest(authority.manifest),
        'halted': halted, 'authorizationStatus': authority.approval_status(),
        **_aggregate(authority, rows, selected * len(models)),
        'byModelLanguageSplit': [dict(model=model, language=language, split=split,
            **_aggregate(authority, [row for row in rows if (row['model'], row['language'], row['split']) == (model, language, split)],
                         [case for case in selected if case['intent']['language'] == language and case['split'] == split]))
            for model in models for language in ('en', 'es') for split in ('smoke', 'development')],
        'usageProvenance': 'simulated' if fault else 'validated_provider_response_fields',
        'usageTotalsScope': 'known_usage_subset_only_not_total_experiment_cost',
        'actualProviderCostNano': 0 if fault or not rows else None,
        'countActualCost': 'not_in_response_unknown' if any(row['phase'] == 'count' for row in rows) and not fault else 'no_live_cost',
        'tokenCostMeaning': 'uncached_published_rate_upper_calculation_not_invoice',
        'scope': 'model_adapter_only_not_final_app_pipeline', 'billingSettlement': 'not_exercised',
        'productionGuardEvaluation': 'not_exercised_model_adapter_only',
        'latencyQualification': 'not_measured_by_this_report',
        'semanticSpanQuality': 'not_independently_scored',
        'qualityQualification': 'none', 'providerCompatibility': 'unqualified_until_reviewed_live_evidence',
    }


def export_report(authority, directory):
    target = Path(directory).resolve()
    root = authority.directory.resolve()
    require(not target.is_relative_to(root) and not root.is_relative_to(target), 'REPORT_AUTHORITY_OVERLAP')
    directory = private_directory(directory)
    value = report(authority)
    fd, name = tempfile.mkstemp(prefix='.controlled-report-', dir=directory)
    path = Path(name)
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(canonical(value) + '\n'); handle.flush(); os.fsync(handle.fileno())
        os.replace(path, directory / 'controlled-report.json')
    finally:
        path.unlink(missing_ok=True)
    return value
