# Bounded retained-period cleanup candidate

SECUR4ALL-207: the deletion bridge handler now calls the retained-period helper.
It covers `inventory.minimumPeriodId` through the immutable request-time period,
instead of only the current/previous periods. Reversed ranges or ranges larger
than the configured call bound (default 8, hard maximum 32) fail closed. The existing
read-only assessor remains unwired and never authorizes completion.

The selected inventory, command and registry records must match strictly. Missing,
retired or malformed keys are never interpreted as proof that their data vanished.
The key ARN must match the runtime account/Region; GenerateMac must return that
same ARN, HMAC_SHA_256 and 32 bytes. Every mutation checks the exact selected key
registry alongside the existing inventory and command guards. Registry replacement
or retirement after derivation cancels the transaction. The runtime derives its
account/Region from the Lambda invocation ARN, not request data.

## Fair progress without a new retention period

The request-period TOMBSTONE is the anchor. Its optional additions are:

- `retainedPeriodSweepRevision`: positive integral optimistic concurrency version.
- `retainedPeriodSweep`: exact fields `schemaVersion:1`, `operationId`,
  `inventoryRevision`, `minimumPeriodId`, `maximumPeriodId`, `nextPeriodId`.

No account identifier, extra token, key family or expiry is added. Existing
schema 2 tombstones without these fields can initialize the cursor. Unknown,
malformed or differently bound inventory/range state is preserved and rejected.
The existing locator cleanup fields and original tombstone logical deadline remain
unchanged; no independent TTL or retirement permission is introduced.

Each invocation first advances the cursor under exact anchor-tombstone,
anchor-key, inventory and durable-command conditions, then attempts one period.
Its existing locator SEEK/DELETE/RECOMPUTE state resumes independently. A lost
advance acknowledgment may skip an attempt until the next wrap, but cannot mark
it complete or lose its locator progress. Missing selected keys and poison
locators leave that period unverified while permitting later periods to progress.
A missing/invalid anchor key remains a hard addressing dependency. Repeated
invocation/backlog delivery remains an activation prerequisite; this cursor alone
is not a scheduler or durable whole-command backlog.

A concrete ownership exception permits an existing cursor to be adopted without
resetting its position when a different **currently stored, exact owned command**
uses the same inventory and period range. This supports simultaneous PENDING
withdrawal and REQUESTED account deletion records targeting the same derived
partition. Each write still guards its own exact ledger command. Different
subjects derive different anchor partitions. No arbitrary operation change,
retention reset, range change or inventory migration is inferred from adoption.

## Bounds and failure semantics

At most one selected period is attempted per call, default 10/hard 20 locator steps.
The anchor and selected key are the only keys derived (one derivation if equal).
SDK connect/read timeouts are 2/3 seconds and total attempts 1. Remaining-time checks
occur before outer SDK stages and existing locator steps; a multi-call step or
in-flight request can still cross that cooperative threshold. Persisted cursors
and transactional locator state allow retries after timeout. No timing bound is
claimed as physical erasure proof.

Results contain only counts, fixed reasons and diagnostic selected-period status.
They always have `complete:false` and `receiptEligible:false`; a selected empty
locator pass says nothing about other periods. The handler still fails unfinished
stream batches so they are not acknowledged as complete. Current/legacy helper
functions remain internal for prior callers/tests; the deployed-handler source
has no fallback to their two-period traversal. The component completion helpers
remain hard-disabled, as do tombstone retirement and unqualified lifecycle paths.

## Infrastructure and rollout prerequisites

Only the campaign deletion bridge artifact changes. Required additional IAM is
pipeline `ConditionCheckItem`, LeadingKeys `PERIOD#*`, ReturnValuesNONE, no registry
write permission. The previous inventory/command/check and transaction mutation
grants remain necessary. No new environment variable or activation flag is added.
Existing empty locator pins, disabled mappings/schedules and completion gates
remain unchanged. This source increment does not upload or deploy an artifact.

Root's metadata-only Dev audit found period 1479 ENABLED/KMS Enabled and period 1478
RETIRED/KMS PendingDeletion. This makes the unverified-retired-period path material;
it does not establish historical erasure, authorize the locator inventory, or
permit skipping 1478. Full retained-period/key/legacy/restore coverage, key and
publication ordering, durable backlog and completion proofs remain open.

## Validation

25 new SDK/Moto cases pass, included in 139 combined campaign coverage, progress,
metadata and producer cases. Six ordinary deletion cases plus nine subtests pass.
Compile and diff checks pass. The full local Python 3.14 ARM64 bridge ZIP
contains 15 distinct Python members, each byte-equal to source and compilable;
size 23,639 bytes, SHA256
`28a138fa47622f8ea40319951d5577827a51c2de94036e429fd6e55be34c1f68`.
No archive was uploaded and no AWS runtime test was performed. Synthetic tests
cover old periods beyond the former two-period window, wrap fairness with busy or
poison predecessors, lost advance acknowledgments, key/command/inventory/tombstone
races, strict cursor schemas, original deadlines, terminal replay, real handler
composition, overlapping owned withdrawal/deletion and different-account isolation.
No live account data, KMS MAC, provider request, key mutation or activation is used.
