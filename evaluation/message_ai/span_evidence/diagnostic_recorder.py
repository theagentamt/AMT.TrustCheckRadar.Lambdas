"""Owner-operated ONE-CALL structural diagnostic; never qualification or activation.

No execution is authorized by the plan or by this file. A separately approved,
hash-pinned scope is required. The batch directory is an irreversible local
execution fence: no resume, retry, overwrite or reservation release exists.
"""
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import resource
import stat
import subprocess
import sys
import threading
import time

REQUEST_SHA256 = '9048e86d653ceded89669654867f6737a0a2fb8eac1513500db751bd97d1dd36'
PACKET_ID = 'atcr182-original-dev019en-span-diagnostic-one-call'
MODEL = 'gpt-5.4-mini-2026-03-17'
SECRET_ARN = 'arn:aws:secretsmanager:us-east-1:107827791950:secret:trustcheckradar/dev/openai-uHKddW'
SECRET_VERSION = '3e1b7902-9e58-4214-b933-a2bfe18ff6dc'
AUTHORITY = Path('/Users/theaimachine/Documents/Codex/2026-10-25/task-3/astra-ultra/diagnostic-attempt')
RESERVE = 332_534_400
TOTAL_CAP = 350_000_000
ATTEMPTS = 1
ATTEMPT_SECONDS = 18
BATCH_SECONDS = 300
ai_provider = proposer = usage_report = unique_pairs = None


def require(ok, code):
    if not ok:
        raise ValueError(code)


def utc():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def private(path, directory=False):
    path = Path(path)
    info = path.lstat()
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600),
            'PRIVATE_METADATA_INVALID')
    require(path.is_absolute() and path == path.resolve(), 'PATH_INVALID')
    return path


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save(path, value):
    """Exclusive and durable, including interrupted writes that fence relaunch."""
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_dir(Path(path).parent)


def load_json(path):
    private(path)
    require(Path(path).stat().st_size <= 200_000, 'JSON_BOUNDS')
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, 'DUPLICATE_KEY')
            value[key] = item
        return value
    return json.loads(Path(path).read_bytes(), object_pairs_hook=pairs)


def bind_application(root, plan):
    """Load unchanged reviewed source, not the consumed archived candidate."""
    global ai_provider, proposer, usage_report, unique_pairs
    root = Path(root).resolve(strict=True)
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    require(git('rev-parse', 'HEAD') == plan['backendRevision']
            and git('rev-parse', 'HEAD^{tree}') == plan['sourceTree'], 'APPLICATION_TREE_DRIFT')
    require(not git('status', '--porcelain', '--untracked-files=all'), 'APPLICATION_NOT_CLEAN')
    for name, digest in plan['sourcePins'].items():
        require(sha(root / name) == digest, 'APPLICATION_SOURCE_DRIFT')
    sys.path.insert(0, str(root / 'src'))
    from message_evaluator import ai_provider as provider, proposer as transport
    from message_evaluator.usage import usage_report as usage
    from shared_message_contract.runtime import unique_pairs as pairs
    for module in (provider, transport, sys.modules[usage.__module__], sys.modules[pairs.__module__]):
        require(Path(module.__file__).resolve().is_relative_to(root / 'src'), 'APPLICATION_IMPORT_DRIFT')
    ai_provider, proposer, usage_report, unique_pairs = provider, transport, usage, pairs
    require(provider.PROMPT_SHA256 == plan['promptSha256'] and provider.SCHEMA_SHA256 == plan['schemaSha256'], 'APPLICATION_CONTRACT_DRIFT')
    settings = provider.AISettings(MODEL, SECRET_ARN, 8000, 512, 'low')
    for case in plan['generationCases']:
        body = provider.request_body(case['intent'], settings)
        require(body == case['requestBody'] and hashlib.sha256(json.dumps(body, ensure_ascii=False).encode()).hexdigest() == case['requestBodySha256'], 'REQUEST_DRIFT')


def validate_plan(path):
    plan = load_json(path)
    expected = {'packetIdentity': PACKET_ID, 'model': MODEL,
                'generationAttemptsMaximum': 1, 'automaticRetries': 0,
                'countRequests': 0, 'diagnosticOnly': True,
                'fullQualificationMayPass': False,
                'proposedSupervisedAttemptWallLimitMs': ATTEMPT_SECONDS * 1000,
                'proposedBatchWallLimitMs': BATCH_SECONDS * 1000}
    require(all(type(plan.get(k)) is type(v) and plan[k] == v for k, v in expected.items()), 'PLAN_LIMIT_DRIFT')
    profile = plan.get('inferenceProfile')
    require(type(profile) is dict and set(profile) == {'reasoningEffort', 'maxOutputTokens'}
            and type(profile['maxOutputTokens']) is int
            and profile == {'reasoningEffort': 'low', 'maxOutputTokens': 512}, 'INFERENCE_PROFILE_DRIFT')
    cases = plan['generationCases']
    require(type(cases) is list and len(cases) == 1 and cases[0]['caseId'] == 'dev-019-en'
            and cases[0]['requestBodySha256'] == REQUEST_SHA256, 'CASE_DRIFT')
    body_path = private(Path(path).parent / 'request-body.json')
    require(sha(body_path) == REQUEST_SHA256
            and load_json(body_path) == cases[0]['requestBody'], 'ORIGINAL_BYTES_DRIFT')
    require(plan['budgetProposal']['reservationNanoUsd'] == RESERVE
            and plan['budgetProposal']['hardCapNanoUsd'] == TOTAL_CAP, 'BUDGET_LIMIT_DRIFT')
    return plan


def validate_cumulative_budget(plan):
    """Pin the one shared ledger allocation; never release historic reservations."""
    proposal = plan['cumulativeBudget']
    ledger_path = private(proposal['ledgerPath'])
    require(sha(ledger_path) == proposal['ledgerSha256'], 'CUMULATIVE_LEDGER_DRIFT')
    ledger = load_json(ledger_path)
    allocations = ledger['allocations']
    held = [a['amountHeldNanoUsd'] for a in allocations]
    require(ledger['ledgerId'] == proposal['ledgerId']
            and ledger['currency'] == 'USD'
            and ledger['ceilingNanoUsd'] == proposal['sharedCeilingNanoUsd'] == 50_000_000_000
            and len({a['allocationId'] for a in allocations}) == len(allocations)
            and all(type(n) is int and n >= 0 for n in held)
            and sum(held) == ledger['totalHeldNanoUsd'] <= ledger['ceilingNanoUsd']
            and ledger['remainingUnallocatedNanoUsd'] == ledger['ceilingNanoUsd'] - sum(held)
            and ledger['priorReservationReleases'] == 0, 'CUMULATIVE_BUDGET_INVALID')
    matching = [a for a in allocations if a['allocationId'] == proposal['thisAllocationId']]
    require(len(matching) == 1 and proposal['thisAllocationId'] == plan['packetIdentity'], 'CUMULATIVE_ALLOCATION_INVALID')
    allocation = matching[0]
    require(allocation['allocationState'] == 'PREALLOCATED_PENDING_SEPARATE_KEY_USE_APPROVAL'
            and allocation['backendMain'] == plan['backendMain']
            and allocation.get('sourceRevision') == plan['backendRevision']
            and allocation['actualGenerationIntents'] == 0
            and allocation['generationAttemptCap'] == ATTEMPTS
            and allocation['amountHeldNanoUsd'] == proposal['thisBatchReservationNanoUsd'] == ATTEMPTS * RESERVE,
            'CUMULATIVE_ALLOCATION_INVALID')


def load_scope(path, plan_path):
    scope = load_json(path)
    expected = {'planSha256': sha(plan_path), 'runnerSha256': sha(__file__),
                'secretArn': SECRET_ARN, 'secretVersion': SECRET_VERSION,
                'secretUseApproved': True, 'generationAttemptCap': ATTEMPTS,
                'budgetNanoUsd': TOTAL_CAP, 'authorityDirectory': str(AUTHORITY),
                'metadataAndOfficialPriceRequalified': True,
                'sourceAndRecorderReviewApproved': True,
                'reservationNanoUsd': RESERVE}
    require(set(scope) == set(expected) | {'approvedAtUtc', 'prerequisitesCheckedAtUtc'}, 'SCOPE_INVALID')
    require(all(type(scope[k]) is type(v) and scope[k] == v for k, v in expected.items()), 'SCOPE_INVALID')
    now = datetime.now(timezone.utc)
    for field, age in [('approvedAtUtc', 3600), ('prerequisitesCheckedAtUtc', 900)]:
        then = datetime.fromisoformat(scope[field].replace('Z', '+00:00'))
        require(then.tzinfo is not None and 0 <= (now - then).total_seconds() <= age, 'SCOPE_EXPIRED')
    validate_cumulative_budget(load_json(plan_path))
    private(AUTHORITY.parent, directory=True)
    require(sha(plan_path) == scope['planSha256'], 'PLAN_DRIFT')
    return scope


def read_approved_key():
    """Called once in supervised memory-only worker, only after scope validation."""
    import boto3
    from botocore.config import Config
    client = boto3.Session(profile_name='trustcheckradar').client('secretsmanager', region_name='us-east-1',
        config=Config(connect_timeout=2, read_timeout=2, retries={'total_max_attempts': 1}))
    try:
        value = client.get_secret_value(SecretId=SECRET_ARN, VersionId=SECRET_VERSION, VersionStage='AWSCURRENT')
        require(value.get('VersionId') == SECRET_VERSION, 'SECRET_READ_UNQUALIFIED')
        raw = value.get('SecretString')
        require(type(raw) is str and 1 <= len(raw) <= 4096, 'SECRET_READ_UNQUALIFIED')
        try:
            key = json.loads(raw, object_pairs_hook=unique_pairs)
        except json.JSONDecodeError:
            key = raw
        if type(key) is dict:
            require(set(key) == {'apiKey'}, 'SECRET_READ_UNQUALIFIED')
            key = key['apiKey']
        require(type(key) is str and re.fullmatch(r'[!-~]{16,512}', key), 'SECRET_READ_UNQUALIFIED')
        return key
    except Exception:
        raise ValueError('SECRET_READ_UNQUALIFIED') from None
    finally:
        client.close()


def one(case, key):
    """Production transport/usage/parser; retain only closed structural fields."""
    observed = {'httpStatus': None, 'requestId': None}
    captured = {'usage': None, 'usageFailureReason': 'RESPONSE_UNAVAILABLE',
                'parserRejectionStage': None, 'parserRejectionDetail': None,
                'assessment': None, 'adapterValidated': False}
    class Connection(proposer.FixedConnection):
        def getresponse(self):
            response = super().getresponse()
            observed['httpStatus'] = response.status if type(response.status) is int and 100 <= response.status <= 599 else None
            identifier = response.getheader('x-request-id')
            if type(identifier) is str and re.fullmatch(r'req_[A-Za-z0-9]{16,64}', identifier) and key not in identifier:
                observed['requestId'] = identifier
            return response
    def body_builder(intent, settings):
        body = ai_provider.request_body(intent, settings)
        require(body == case['requestBody'] and hashlib.sha256(json.dumps(body, ensure_ascii=False).encode()).hexdigest() == REQUEST_SHA256, 'REQUEST_DRIFT')
        return body
    def rejected(stage):
        captured['parserRejectionStage'] = stage if type(stage) is str and stage in ai_provider.REJECTION_STAGES else 'PARSER_INTERNAL'
    def rejected_detail(detail):
        if (captured['parserRejectionStage'] == 'SPAN_ORDER_OR_OVERLAP'
                and type(detail) is dict and set(detail) == {'reasonCode', 'spanRelation'}
                and type(detail['reasonCode']) is str and detail['reasonCode'] in ai_provider.AI_REASONS
                and type(detail['spanRelation']) is str and detail['spanRelation'] in ai_provider.SPAN_RELATIONS
                and all(key not in value for value in detail.values())):
            captured['parserRejectionDetail'] = dict(detail)
    def parser(raw, text):
        # Check decoded JSON too: escaped key echo must never reach retention.
        require(type(raw) is bytes and key.encode() not in raw, 'UNSAFE_RESPONSE')
        try:
            decoded = json.loads(raw, object_pairs_hook=unique_pairs)
            require(key not in json.dumps(decoded, ensure_ascii=False), 'UNSAFE_RESPONSE')
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ValueError('UNSAFE_OR_INVALID_RESPONSE') from None
        report = usage_report(raw, MODEL, (750, 4500))
        usage = report['usage']
        if usage is not None and (usage['inputTokens'] > 400_000 or usage['outputTokens'] > 512
                                  or usage['tokenCostUpperNano'] > RESERVE):
            report = {'usage': None, 'failureReason': 'COST_BOUND_EXCEEDED'}
        captured.update(usage=report['usage'], usageFailureReason=report['failureReason'])
        require(report['usage'] is not None, 'USAGE_UNQUALIFIED')
        assessment = ai_provider.parse(raw, text, expected_model=MODEL, reasoning_effort='low',
            on_rejection=rejected, on_rejection_detail=rejected_detail)
        require(key not in json.dumps(assessment, ensure_ascii=False), 'UNSAFE_RESPONSE')
        captured.update(assessment=assessment, adapterValidated=True)
        return assessment
    started, tick = utc(), time.monotonic()
    failure = None
    try:
        proposer.propose(case['intent'], ai_provider.AISettings(MODEL, SECRET_ARN, 8000, 512, 'low'), 8000,
            connection_factory=Connection, secret_loader=lambda *_: key,
            body_builder=body_builder, response_parser=parser)
    except Exception:
        failure = 'APPLICATION_ADAPTER_REJECTED_OR_TRANSPORT_UNCERTAIN'
    outcome = 'OTHER_REJECTION_OR_UNCERTAIN'
    if failure is None and captured['adapterValidated']:
        outcome = 'NON_REPRODUCTION_NOT_QUALIFICATION'
    elif captured['parserRejectionStage'] == 'SPAN_ORDER_OR_OVERLAP':
        outcome = 'NEW_SPAN_VIOLATION' if captured['parserRejectionDetail'] else 'DIAGNOSTIC_INCOMPLETE'
    return {'caseId': case['caseId'], 'requestBodySha256': REQUEST_SHA256,
            'startedAtUtc': started, 'finishedAtUtc': utc(),
            'elapsedMs': round((time.monotonic() - tick) * 1000), 'failure': failure,
            **observed, **captured, 'diagnosticOutcome': outcome,
            'qualificationCriteriaMet': False, 'semanticReviewStatus': 'PENDING' if captured['adapterValidated'] else 'NOT_REVIEWABLE',
            'billingCertainty': 'published_rate_upper_estimate_not_invoice' if captured['usage'] else 'unknown_full_reservation_retained'}


def worker(channel, plan, directory):
    """One child owns the key; parent receives only finite progress events."""
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    null = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(null, fd)
    os.close(null)
    key = None
    try:
        for case in plan['generationCases']:
            save(directory / (case['caseId'] + '-intent.json'), {
                'atUtc': utc(), 'caseId': case['caseId'], 'requestBodySha256': case['requestBodySha256'],
                'reservationNanoUsd': RESERVE, 'dispatchOutcome': 'UNCERTAIN_UNTIL_RESULT', 'retries': 0})
            key = read_approved_key()
            channel.send_bytes(b'START')
            # Supervisor starts wall timer before allowing this single attempt.
            require(channel.recv_bytes(8) == b'GO', 'SUPERVISOR_STOP')
            result = one(case, key)
            save(directory / (case['caseId'] + '-result.json'), result)
            channel.send_bytes(b'DONE')
            require(channel.recv_bytes(8) == b'ACK', 'SUPERVISOR_STOP')
            if not result['adapterValidated']:
                break
        channel.send_bytes(b'FINISHED')
    except BaseException:
        try:
            channel.send_bytes(b'FAILED')
        except (OSError, EOFError):
            pass
    finally:
        key = None  # No forensic memory-erasure guarantee in Python.
        channel.close()


def worker_entry(channel, unused_parent_end, plan, directory, target):
    # Without closing the inherited peer FD, an orphan could wait forever for
    # its own duplicate descriptor instead of observing supervisor EOF.
    unused_parent_end.close()
    target(channel, plan, directory)


def supervise(plan, directory, *, worker_target=worker, attempt_seconds=ATTEMPT_SECONDS, batch_seconds=BATCH_SECONDS):
    """External kill bounds DNS, secret retrieval and transport without resending.

    A timeout/parent interruption consumes the reservation. It does not establish
    whether OpenAI received a request, and is never grounds for a retry.
    """
    require(threading.active_count() == 1, 'SUPERVISOR_REQUIRES_SINGLE_THREAD')
    context = multiprocessing.get_context('fork')  # Key never enters argv/env/IPC.
    parent, child = context.Pipe(duplex=True)
    process = context.Process(target=worker_entry, args=(child, parent, plan, directory, worker_target))
    batch_deadline = time.monotonic() + batch_seconds
    attempt_deadline = None
    count = completed = 0
    reason = 'WORKER_EXIT_UNCERTAIN'
    try:
        process.start()
        child.close()
        while True:
            now = time.monotonic()
            deadline = min(batch_deadline, attempt_deadline or batch_deadline)
            if now >= deadline:
                reason = 'ATTEMPT_WALL_TIMEOUT' if attempt_deadline and attempt_deadline <= batch_deadline else 'BATCH_WALL_TIMEOUT'
                break
            if parent.poll(min(.05, deadline - now)):
                event = parent.recv_bytes(16)
                if time.monotonic() >= deadline:
                    reason = 'ATTEMPT_WALL_TIMEOUT' if attempt_deadline and attempt_deadline <= batch_deadline else 'BATCH_WALL_TIMEOUT'
                    break
                if event == b'START':
                    require(attempt_deadline is None and count == completed and count < ATTEMPTS, 'SUPERVISOR_PROTOCOL')
                    require((directory / (plan['generationCases'][count]['caseId'] + '-intent.json')).is_file(), 'INTENT_MISSING')
                    count += 1
                    attempt_deadline = time.monotonic() + attempt_seconds
                    parent.send_bytes(b'GO')
                elif event == b'DONE':
                    require(attempt_deadline is not None, 'SUPERVISOR_PROTOCOL')
                    result_path = directory / (plan['generationCases'][completed]['caseId'] + '-result.json')
                    require(result_path.is_file(), 'RESULT_MISSING')
                    completed += 1
                    attempt_deadline = None
                    parent.send_bytes(b'ACK')
                elif event == b'FINISHED':
                    require(attempt_deadline is None and count == completed, 'SUPERVISOR_PROTOCOL')
                    reason = 'FINISHED'
                    break
                elif event == b'FAILED':
                    reason = 'WORKER_FAILED_NO_RELAUNCH'
                    break
                else:
                    raise ValueError('SUPERVISOR_PROTOCOL')
            elif not process.is_alive():
                break
    except BaseException:
        reason = 'SUPERVISOR_INTERRUPTED_OR_PROTOCOL_FAILURE'
    finally:
        if process.pid is not None:
            process.join(timeout=.1 if reason == 'FINISHED' else 0)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            require(not process.is_alive(), 'WORKER_STOP_UNCONFIRMED_NO_RELAUNCH')
        parent.close()
        child.close()
    return reason


def summarize(plan, directory, reason):
    result_path = directory / 'dev-019-en-result.json'
    result = load_json(result_path) if result_path.exists() else None
    return {'atUtc': utc(), 'supervisorOutcome': reason,
            'generationIntents': int((directory / 'dev-019-en-intent.json').exists()),
            'retainedResults': int(result is not None),
            'sourceRevision': plan['backendRevision'], 'sourceTree': plan['sourceTree'],
            'requestBodySha256': REQUEST_SHA256,
            'diagnosticOutcome': result['diagnosticOutcome'] if reason == 'FINISHED' and result else 'INCONCLUSIVE_NO_RELAUNCH',
            'reservedGenerationAttempts': 1, 'reservedNanoUsd': RESERVE, 'budgetNanoUsd': TOTAL_CAP,
            'knownTokenCostUpperNano': result['usage']['tokenCostUpperNano'] if result and result['usage'] else None,
            'unknownBilling': reason != 'FINISHED' or not result or result['usage'] is None,
            'qualificationCriteriaMet': False, 'modelQualificationAccepted': False,
            'automaticRetries': 0, 'countRequests': 0, 'relaunchAllowed': False,
            'applicationActivated': False, 'reservationReleased': False}


def execute(plan_path, scope_path, backend_root):
    plan = validate_plan(plan_path)
    bind_application(backend_root, plan)
    load_scope(scope_path, plan_path)
    require(threading.active_count() == 1, 'SUPERVISOR_REQUIRES_SINGLE_THREAD')
    AUTHORITY.mkdir(mode=0o700)  # Existing/interrupted batch permanently blocks another launch.
    sync_dir(AUTHORITY.parent)
    save(AUTHORITY / 'reservation.json', {'atUtc': utc(), 'planSha256': sha(plan_path),
         'runnerSha256': sha(__file__), 'scopeSha256': sha(scope_path), 'reservedGenerationAttempts': ATTEMPTS,
         'reservationNanoUsd': ATTEMPTS * RESERVE, 'budgetNanoUsd': TOTAL_CAP,
         'secretReadMaximum': 1, 'relaunchAllowed': False, 'automaticRetries': 0, 'countRequests': 0})
    save(AUTHORITY / 'plan.json', plan)
    reason = supervise(plan, AUTHORITY)
    summary = summarize(plan, AUTHORITY, reason)
    save(AUTHORITY / 'terminal.json', summary)
    return summary


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--backend-root', type=Path, required=True)
    parser.add_argument('--approved-scope', type=Path)
    args = parser.parse_args()
    try:
        if args.approved_scope is None:
            value = validate_plan(args.plan)
            bind_application(args.backend_root, value)
            print(json.dumps({'offlinePreflight': 'PASS', 'providerCalls': 0, 'secretReads': 0}))
        else:
            print(json.dumps(execute(args.plan, args.approved_scope, args.backend_root)))
    except BaseException:
        print(json.dumps({'status': 'STOPPED_NO_AUTOMATIC_RETRY', 'detailsSuppressed': True}))
        sys.exit(1)
