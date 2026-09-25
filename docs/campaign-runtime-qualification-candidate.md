# SEC207 isolated completion runtime qualification

The original PR57 increment hardened positive completion replay and supplied a
separate bounded synthetic AWS runner without changing the production handler.
The subsequent campaign-completion-worker-candidate.md integrates that primitive
behind default-false stream/completion gates. The qualification builder continues
to preserve the exact committed production app.py; it does not replace the handler
with the runner. No production inventory writer or backfill is added.

## Positive replay

An unexpired exact receipt/audit is necessary but insufficient. Positive replay now
freshly reads every qualified retained period, requires its exact enabled key and
idle tombstone to be the sole strong partition result, then makes a check-only
transaction guarding markers, keys, tombstones, original/terminal command, original
receipt/audit, control and own-job absence. The transaction changes no records.
Integral monotonic clock checks immediately before and after its SDK return refuse
logical expiry during verification. A lost verification response fails closed.
A lost mutation response may reconcile only through this fresh replay proof; it
cannot cause another mutation attempt. Original audit/receipt deadlines are never
renewed or recreated. Full-account terminal suppression remains non-completing
acknowledgment, including after receipt retirement.

The proof has a transaction linearization point, not a permanent guarantee against
later privileged restore. Writer tombstone fences prevent qualified concurrent
insertion. Injecting a legacy locator before replay prevents positive replay.
Reinstating an entire internally consistent historical state under unchanged
external manifest pins is not distinguishable from that historical state by these
records alone. Every restore must invalidate qualification, keep writers closed,
and undergo reconciliation/reapproval before new completion claims.

## Isolated fixture ABI and containment

Run ID is exactly twelve lowercase hexadecimal characters. Resource prefix is
`amt-campaign-completion-qual-<runid>`. Dedicated tables end in `-pipeline`, `-ledger`,
and `-users`, with String PK/SK. The ledger additionally requires the exact ACTIVE
CampaignRecoveryDueIndex (campaignRecoveryPartition String / nextAttemptAtEpoch
Number, KEYS_ONLY); other fixture tables have no indexes. Function ends in `-runner`.
Region/account are fixed to `us-east-1` / `107827791950`. Each table and HMAC key must
have exactly the four reviewed tags: Purpose `campaign-completion-qualification`,
QualificationRunId the run ID, Environment `dev`, Project `trustcheckradar`.

Required environment fields are `QUALIFICATION_RUN_ID`,
`QUALIFICATION_PIPELINE_TABLE`, `QUALIFICATION_LEDGER_TABLE`,
`QUALIFICATION_USERS_TABLE`, `QUALIFICATION_KEY_ARN`,
`QUALIFICATION_FUNCTION_NAME`, and `QUALIFICATION_SOURCE_SHA`. AWS_REGION is checked.
The exact unqualified function ARN/name from Lambda context must agree. Key metadata
must prove the exact same-account/Region HMAC_256 enabled GENERATE_VERIFY_MAC key.
Table names, ARNs, key schema and tags are checked before any fixture cleanup/seed.
The same dedicated synthetic key represents both fixture periods; this does not
qualify real historical key retirement. SDK timeouts are connect2/read3 seconds, one total attempt; every explicit runner
SDK call and completion proof call checks at least six seconds remaining.

Handler is `campaign_qualification.lambda_handler`. Input is exactly:

```json
{"schemaVersion":1,"operation":"qualify-campaign-completion","runId":"012345abcdef","case":"account_complete_replay"}
```

Unknown/extra fields and non-allowlisted cases fail without seeding. The fixed case
allowlist is `CASES` in `scripts/qualification/campaign_qualification.py`; each case
runs separately with concurrency1 and a bounded timeout (60 seconds recommended).
The runner resets only its dedicated tagged tables, scanning at most two pages of
100 rows per table, proving traversal exhaustion before deleting any old fixture
row. No live/customer data is accepted. Assertions use explicit exceptions and
remain effective under Python optimization. Fixed result fields disclose only case,
source SHA and pass/fail; SDK bodies, events, keys, identifiers and row values are
never printed or returned. Exceptions map to fixed QUALIFICATION_FAILED plus allowlisted failureStage and
failureCategory values, never exception strings. Root owns
resource provisioning, explicit invocation and cleanup; the runner creates no AWS
resources or policies.

IAM needed by this fixture only: exact-table DescribeTable/ListTagsOfResource,
GetItem/Query/Scan/PutItem/UpdateItem/DeleteItem/ConditionCheckItem, Query on the exact
ledger CampaignRecoveryDueIndex ARN, and exact-key
DescribeKey/ListResourceTags/GenerateMac. Transactions use constituent permissions.
No Lambda, STS, Cognito, provider, S3, backup, key-management or logging API calls.
The HMAC fixture key is scheduled for deletion with the approved seven-day minimum
by infrastructure cleanup. These permissions are never attached to production.

## Build and evidence

```sh
python scripts/build_campaign_qualification.py --source-sha <exact-clean-head> --output-dir <outside-repo-directory>
```

The builder requires clean HEAD and inclusion of the fetched release, runs the full
bridge build for Python3.14 ARM64, verifies every production Python member against
that commit, leaves app.py untouched, and appends only the committed runner plus a
member-hash/source manifest. It rejects unexpected dependencies rather than calling
a source-only ZIP a qualified dependency build. Current bridge has no additional
requirements; boto3/botocore come from Lambda's managed runtime. Output manifest
records production ZIP digest, qualification ZIP digest/size, handler/runtime/arch
and member hashes. No upload or runtime change is part of this command.

Local real-SDK/Moto tests exercise the exact runner implementation with a fixed
synthetic KMS response. Actual AWS Python3.14 ARM64 execution must independently
confirm platform loading, real KMS HMAC, transaction behavior and scoped permissions.
Local imports/compile are not evidence those cloud calls ran.

## Restore and historical limits

The synthetic matrix includes account and withdrawal completion/replay, overlap and
concurrent completion, lost acknowledgement, stale restored users/ledger/pipeline,
SEALED control with a surviving job, stale manifests/job/control, delayed backfill,
retired/missing prior keys, newly injected legacy locator after completion, logical
expiry, proof races, fenced producer insertion, runtime budget and wrong resources.
Fixture snapshot re-insertion is application-level restore simulation; it is not an
AWS native/PITR restore test and cannot establish historical production absence.
Synthetic marker fixtures explicitly assume all required inventory invariants; they
never approve real coverage. Root's current backup/key/registry audits are separate
observations, not substitutes for prior-period erasure/restore/key evidence. The
owner's report of no known manual copies does not certify unknown historical copies.

Existing candidate lifecycle tests retain rejection of legacy `manage_keys`,
`finalize_periods` and `expire_transient` operations. Those legacy internal methods
are not enabled or invoked by this runner. Missing/retired real retained keys,
publication/orphan/history qualification and reviewed completion inventory remain
explicit SEC207 live acceptance dependencies. This increment does not mark SEC207
Done or authorize deployment/activation of completion.

## Local validation before source freeze

- 212 combined SDK/Moto cases: completion, runner, retained traversal, shared
  recovery and finalizer. This includes 28 fixed runtime case implementations.
- 17 separate lifecycle SDK cases, including rejection of legacy mutation operations.
- 49 focused runner/containment SDK cases passed again after the fixed failure-stage
  response was added. Six ordinary bridge tests plus nine subtests passed.
- Final package checks are recorded with the frozen source/artifact handoff; no
  cloud result is implied by these local counts.

## Subsequent actual-handler integration fixture cases

The runner now includes seven actual stream/scheduled handler cases in addition to
the original 28 primitive/containment cases. After strict resource preflight it
sets only in-process app settings to these fixture tables/keys and restores them
after each case; no AWS Lambda environment or production gate is changed. Real
source `app.lambda_handler` and `Worker` execute. Stream records are synthetic
DynamoDB stream-shaped events, not proof of ESM delivery configuration. Scheduled
setup allows at most eight bounded index-visibility queries before invoking the
real recovery handler. An eventual query is used solely for discovery; completion
still requires the strong transactional proof. Cases cover stream completion and
replay, scheduled completion, disabled stream/completion, missing marker and stale
cleanup after concurrent sealing. Total fixed cloud runner cases:35.
