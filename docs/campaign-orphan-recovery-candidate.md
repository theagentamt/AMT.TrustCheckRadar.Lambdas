# Bounded expired orphan candidate recovery (SECUR4ALL-207)

This increment adds an operator-only lifecycle path for expired contribution
pairs and a repair checkpoint left after SUMMARY disappearance. No account data,
anonymous aggregate, tombstone, approved deadline, marker or KMS state is created
or rewritten. The source changes neither live gates nor infrastructure schedules.

## Event and storage contract

```json
{"schemaVersion":1,"environment":"dev","operation":"recover_expired_orphan","candidateId":"33e21f1c-6a88-45c7-bbcb-1e0f281dcb34","periodId":8}
```

The UUID is an example, not a real approved target. Invocation requires the
existing candidate gate and exact period-admission generation/account/region,
locator manifest and revision. The registry must be ENABLED/CLOSING and its
recovery window ended. Missing/retired keys and foreign or stale generations fail
closed. No new IAM is required beyond pipeline GetItem/strong Query, existing
transactional Delete and exact PERIOD/INVENTORY/CANDIDATE/CONTRIB ConditionChecks.
No ledger, users, intelligence-table or KMS call is added to this operation.

New `CANDIDATE#<UUID>/DELETION_RECOMPUTE` checkpoints have the existing exact
metadata/count/cursor/clock fields plus `repairSchemaVersion:2` and `periodId`.
The bridge copies period identity from an actual SUMMARY under exact observed-row
transaction guards. Page updates and final publication compare exact checkpoint
and summary evidence. Existing version-1 checkpoints remain readable only with
an actual matching SUMMARY; their original cutoff, expiry and cursor are not
refreshed. Legacy orphan checkpoints cannot infer period from a locator or clock.
All checkpoint readers ship together through shared source and bridge/lifecycle
packages. No live migration or initializer is included.

## Bounded recovery and evidence

Before any deletion, the worker strongly reads the full candidate base partition:
at most four pages of 25 rows (100 rows including checkpoint). A remaining,
malformed or repeated pagination key fails unchanged. Every contribution must
have the exact supported current producer shape, matching period/ownership,
valid metadata/vector/language and an original deadline already due. Its owned
locator must match. Unknown, mixed-period, live, partial-schema or mismatched
records are preserved; a legacy checkpoint remains a reconciliation dependency.

A step deletes at most ten exact observed contribution/locator pairs. Each
transaction checks the same current CLOSING registry, locator inventory, absent
SUMMARY and exact checkpoint (or checkpoint absence). Changed replacement
content is preserved. Missing targets may be reconciled only with the still-owned
exact locator. A separate later invocation strongly proves the bounded partition
contains only the exact checkpoint or is empty, then conditionally removes the
checkpoint. Contribution creation is excluded by the qualified all-writer OPEN
fence; this is not safe against an unqualified writer or restored generation.

The six-second remaining-time threshold is checked before every SDK call.
Underlying bounded SDK timeouts still matter; this is not a cancellation guarantee
for an already running request. A lost acknowledgment is retried through fresh
reads, never compensated by reconstructing a checkpoint. Existing cleanup can
win a transaction race; exact mismatches remain unmodified and retryable.

Results contain only `expiredPairs`, `checkpointDeleted`,
`candidateEmptyObserved`, `scopeComplete:false` and `retirementEligible:false`.
Deleting the last observed pair does not claim empty; the later strong-read step
is required. Neither candidate emptiness nor a successful invocation certifies
account/whole-period erasure, historical inventory, aggregate anonymity or safe
key retirement. More than 100 candidate rows are deliberately unresolved; no
unbounded fallback, GSI absence proof or durable cursor is invented.

## Qualification and handoff

Local validation: the affected bridge suite passed 342 tests plus nine subtests;
the separate lifecycle suite passed 63 tests, including 27 new orphan cases.
The four new actual-handler fixture cases also passed individually before the
combined bridge run. Compilation and whitespace checks passed. These are local
SDK/Moto results, not live AWS execution.

Local real-SDK/Moto suites cover paired cleanup, checkpoint compatibility,
exact-row races, lost acknowledgments, unknown/live/mixed evidence, pagination
bounds and per-call time cutoffs. Run affected bridge/lifecycle suites in separate
processes because legacy flat `service`/`config` module names otherwise collide:

```bash
AMT_AUTHORITY_INTEGRATION=1 AWS_ACCESS_KEY_ID=testing AWS_SECRET_ACCESS_KEY=testing python -m pytest tests/campaign_deletion_bridge -q
AMT_AUTHORITY_INTEGRATION=1 AWS_ACCESS_KEY_ID=testing AWS_SECRET_ACCESS_KEY=testing python -m pytest tests/campaign_lifecycle -q
```

The existing clean-source builder produces four coherent production archives and
an isolated qualification archive:

```bash
python scripts/build_campaign_qualification.py --source-sha <exact-clean-commit> --output-dir <outside-checkout-directory>
```

Root-owned synthetic AWS qualification may select exactly these four new cases:
`orphan_handler_drain`, `orphan_handler_legacy_refusal`,
`orphan_handler_live_refusal`, `orphan_handler_lost_ack`. They call the actual
lifecycle handler against three dedicated fixture tables after the existing
account/region/run-tag preflight. Fixture inventory, clocks and key metadata are
synthetic; results do not qualify live inventories, deployed-role policy unions,
real discovery or scheduling. Production archives contain no fixture handler.
No AWS outcome is claimed until separately recorded.

This is an orphan prerequisite, not full SECUR4ALL-207 completion. The
[runbook](campaign-lifecycle-runbook.md) records remaining Dev dependencies and
links the later UAT follow-up. In particular, existing now+21-day contribution
expiry can exceed period-end+7-day recovery. No key-retirement action is safe to
infer from this increment and no deadline is changed to conceal that mismatch.
