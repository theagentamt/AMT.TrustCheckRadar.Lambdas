# Dev History and Recognition Acceptance

This runbook separates immutable-artifact acceptance from live Dev evidence.
Artifact checks prove package identity and local behavior; only an approved run
against deployed resources proves routing, IAM, authorizers, transactions,
indexes, lifecycle scheduling, alarms, and physical erasure.

## Safety boundary

`scripts/history_dev_smoke.py` is inert unless `--execute` is supplied. Execution
is restricted to `environment=dev` and requires:

- an owner approval reference;
- two distinct, explicitly supplied disposable Cognito subjects;
- the exact confirmation phrase printed by `--help`;
- one access token and active device fingerprint per subject, each in a local
  owner-only (`0600`) file;
- the expected AWS account, release SHA, function names, and table names.

The script verifies each JWT's local `sub` before sending a request and verifies
the active AWS account before invoking or reading AWS resources. It never creates
or deletes Cognito accounts, never exercises full-account deletion or export,
and never targets UAT or production. Evidence contains step names and bounded
counts only; it excludes subjects, request IDs, operation IDs, tokens,
fingerprints, assessments, table rows, and input content.

The fixed analysis fixture intentionally matches the production instruction-style
abuse detector. The script imports that detector and stops before any request if
the fixture no longer selects the safe local response. Campaign consent is false.
Consequently the smoke run exercises the real analysis reservation, completion,
quota, History, replay and badge path with zero paid model calls and no campaign
publication.

Print the non-mutating plan:

```bash
python3 scripts/history_dev_smoke.py
```

After the owner approves two disposable accounts, use private files and explicit
Dev resource names. Do not place tokens or fingerprints on the command line:

```bash
python3 scripts/history_dev_smoke.py --execute \
  --approval-reference '<approval-ticket-or-message-id>' \
  --confirm-disposable-subjects I_CONFIRM_BOTH_SUBJECTS_ARE_DISPOSABLE_DEV_TEST_ACCOUNTS \
  --expected-account-id '<12-digit-dev-account>' \
  --expected-release-id '<40-character-lambda-release-sha>' \
  --api-base-url 'https://<dev-api-host>' \
  --primary-subject '<disposable-sub-1>' \
  --secondary-subject '<disposable-sub-2>' \
  --primary-token-file /private/path/primary.token \
  --secondary-token-file /private/path/secondary.token \
  --primary-fingerprint-file /private/path/primary.fingerprint \
  --secondary-fingerprint-file /private/path/secondary.fingerprint \
  --conversation-function '<dev-conversation-function>' \
  --history-read-function '<dev-history-read-function>' \
  --history-mutation-function '<dev-history-mutation-function>' \
  --history-lifecycle-function '<dev-history-lifecycle-function>' \
  --history-content-table '<dev-history-content-table>' \
  --history-control-table '<dev-history-control-table>' \
  --analysis-abuse-table '<dev-analysis-abuse-table>' \
  --evidence-file /private/path/history-dev-evidence.json
```

The run covers bootstrap, list, detail, History-only export, progress, one safe
completion, the `checks_1` badge, same-ID replay, cross-subject denial,
cross-device denial, idempotent delete-one, clear, reset, direct lifecycle
invocation, strongly consistent physical-content counts, and analysis-replay
redaction. Both disposable subjects' generated History content is cleared before
success is reported.

## Empty-Dev lifecycle baseline

Use the following conservative minimum for the empty Dev History tables:

- Python 3.13, ARM64, 256 MB memory, 60-second timeout;
- reserved concurrency `1` to serialize checkpoint and erasure progress;
- five-minute EventBridge schedule with the exact
  `{"schemaVersion":1,"operation":"sweep"}` input;
- `HISTORY_LIFECYCLE_MAX_ITEMS_PER_SWEEP=100`;
- `HISTORY_LIFECYCLE_MAX_BUCKET_QUERIES_PER_SWEEP=64`;
- `HISTORY_ERASURE_BATCH_SIZE=25`;
- `HISTORY_EXPIRATION_RECONCILIATION_HOURS=168`;
- `HISTORY_COMPLETION_STUCK_SECONDS=300`;
- `HISTORY_COMPLETION_RECHECK_SECONDS=60`.

Set `HISTORY_LIFECYCLE_START_EPOCH_HOUR` to the UTC-hour-aligned table creation
or controlled activation hour. For an independently proven empty table, using
one hour before activation is a small safety margin and avoids an unbounded
historical scan. Do not reuse that value for a restored or non-empty table; a
restore must start at the earliest restored expiry.

Do not manually seed checkpoints. Before enabling History writes, enable only
the lifecycle worker and invoke one successful sweep. The worker conditionally
creates the exact four content-free checkpoints:

- `EXPIRATION#HISTORY`
- `EXPIRATION#CONTROL`
- `RECONCILIATION#HISTORY`
- `RECONCILIATION#CONTROL`

under `PK=LIFECYCLE#dev`. Verify `LifecycleSweepSuccess=1`, no truncation, and
no Lambda error before enabling writes. Keep lifecycle enabled after any write
is accepted, even if the user-facing History kill switch is later turned off.

The 64-query cap permits all 16 current-hour shards for each table plus bounded
backlog/reconciliation progress. The 100-item cap and 25-item erasure page are
large enough for synthetic Dev acceptance without creating an unbounded task.
Increase them only from measured backlog and duration evidence.

## Acceptance classification

Local acceptance consists of the test suite, deterministic package build,
manifest validation, and exact `CodeSha256` manifest. It does not prove a live
feature.

Live acceptance requires all smoke steps to pass against the pinned Dev release,
the scheduled lifecycle heartbeat and alarms to be observed, and the evidence
file to remain content-free. Full-account deletion/export remain separate and
must stay unavailable until their independent inventory, retention, identity,
finalizer, and delivery contracts are complete.
