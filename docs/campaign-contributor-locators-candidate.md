# Campaign contributor locator candidate

This disabled source candidate replaces eventual-index cleanup with authoritative
same-table contributor locators for upgraded writers. It does not approve legacy
coverage, create an inventory marker, deploy or activate processing, or enable
account/withdrawal completion. Both completion helpers remain closed.

## Atomic writes and strong traversal

Each FEATURE and CONTRIBUTION is created in one transaction with its exact
locator, the contributor tombstone absence guard, and the pinned inventory
condition. Existing contribution changes and pending publisher retries require
the exact existing locator. All cluster write paths, including capped dedupe,
check the contributor tombstone transactionally. Existing SUMMARY updates also
require `lifecycleState` absence; a publication freeze cannot be overwritten.

Locator key:

- PK: `CONTRIB#<periodId>#<43-character contributor token>`.
- SK: `LOCATOR#EVENT#<UUIDv4>` or `LOCATOR#CANDIDATE#<UUIDv4>`.
- Exact fields: `recordType=CAMPAIGN_CONTRIBUTOR_LOCATOR`, `schemaVersion=1`,
  environment, periodId, targetKind (FEATURE/CONTRIBUTION), targetPK, targetSK,
  targetExpiresAtEpoch, GSI3PK and GSI3SK, plus PK/SK.
- No account identifier or submitted content is added. GSI3SK is the original
  target expiry; GSI3PK is the existing environment expiry partition.

Locators deliberately have **no `expiresAt` DynamoDB TTL attribute**. They must
not vanish independently before a target or its paired siblings. Expiration is
explicit paired cleanup at the same approved transient deadline, not a new or
longer retention duration. If workers miss that deadline, physical retention is
an overdue failure requiring recovery; absence of TTL is not retention compliance.

FEATURE's DEDUPE/CLUSTERED siblings carry locatorPK, locatorSK and the same
`targetExpiresAtEpoch`. CLUSTERED's own expiry is capped to the existing FEATURE
deadline, so new siblings cannot outlive their locator's declared target window.
Legacy rows with different deadlines/pointers require migration evidence.

Cleanup queries the contributor's base-table partition with ConsistentRead,
Limit and a durable cursor; it never falls back to ContributorPeriodIndex. A
pending target is stored before deletion. Target/locator deletion and pending
recompute state advance atomically, guarded by the exact durable command and
inventory. FEATURE cleanup checks/deletes its owned siblings. Missing targets
are allowed for TTL/retry, but a mismatched replacement or sibling blocks the
whole transaction. CONTRIBUTION cleanup conditionally bumps SUMMARY's version
and refuses a frozen lifecycle state. Recompute also refuses frozen summaries.

The strong walk retains the previous bounded recomputation (25 rows/page) and
operation-adoption semantics. No partial centroid is published. A pass finishes
only after a final strong empty-partition read with no pending repair. Its result
is still `complete:false, coverage:LOCATOR_TRAVERSAL_ONLY`: a covered partition
pass does not certify old periods, backup/replay sources or whole-account erasure.

## Versioned repair fence and deadline

New tombstones use exact `CAMPAIGN_DELETION_TOMBSTONE`, schemaVersion 2:
PK/SK, environment, periodId, createdAtEpoch, deletionDeadlineEpoch, GSI3PK/GSI3SK,
and optional paired locatorCleanupRevision/locatorCleanupState. There is no
independent TTL attribute. The initial deadline is fixed from the original
request time and existing configured transient duration (at most 21 days).
Retries and later operations adopt pending work without extending that deadline.
Overdue work can still resume; it is never converted to success merely by age.

TTL-bearing or otherwise legacy tombstones are rejected, not automatically
converted or treated as covered. A controlled migration must reconstruct all
pending repairs and preserve original deadlines. Pending recomputation therefore
cannot become invisible simply because its contributor tombstone expired.

Tombstone retirement remains unavailable in source until explicit repair and
period-retirement proof is implemented and qualified. Neither lifecycle's generic
expiry GSI nor a timer may remove this fence. Operator qualification, overdue-work
reconciliation and explicit retirement are activation blockers. This no-TTL
change is source-only and authorizes no live retention change.

## Inventory and migration proof

Runtime pins for publisher, cluster, deletion and lifecycle:

- `CAMPAIGN_LOCATOR_MANIFEST_SHA256`: default empty string.
- `CAMPAIGN_LOCATOR_INVENTORY_REVISION`: default zero.

Missing/invalid pins or marker block upgraded mutation paths. There is no legacy
unlocated-write fallback or self-approval writer.

The marker is PK `INVENTORY#<environment>`, SK `CAMPAIGN_LOCATORS`, with exactly:
recordType CAMPAIGN_LOCATOR_INVENTORY, schemaVersion 1, revision, environment,
coverage VERIFIED_COMPLETE, manifestSha256, approvedAtEpoch, locatorSchemaVersion
1, minimumPeriodId, priorPeriodsErased true, and writers in canonical order
publisher/cluster/deletion_bridge/lifecycle. Revision and manifest must equal the
configured pins. Commands must strictly postdate approvedAtEpoch; older periods
than minimumPeriodId are blocked. Every write transaction rechecks every marker
field. Workers receive read/ConditionCheck permission only on this partition.

The marker is an externally reviewed assertion, not evidence generated by this
source. Before creating it, prove all old writers and queued/stream/restored work
are fenced; backfill exact target locators and sibling pointers transactionally;
reconcile old pending repair; inventory every relevant period/key; verify older
period erasure; and qualify paired cleanup. Material changes advance revision,
manifest and approval epoch and require draining or explicitly requalifying old
commands. Never reset request identity, time or deadline to bypass that fence.
No backfill, marker creation, old-key proof or production inventory was performed.

## Lifecycle integration and remaining acceptance

The shared module exports load_inventory/inventory_condition,
locator_for_target/get_owned_locator, get_locator_by_pointer for expired FEATURE
siblings, locator_put/locator_condition, paired_delete_actions, validate_locator,
owned_page and wire serialization helpers. The lifecycle owner adds its exact
frozen SUMMARY/version guard to paired cleanup transactions and checks absence
of DELETION_RECOMPUTE atomically before freezing. Publication, expiration and
period-key retirement must use these contracts; the older blanket batch-delete
and unqualified key-retirement paths must remain inactive.

Still required before any completion activation: complete legacy migration and
retired-key inventory, period closure and retirement barriers, durable scheduler/
replay and checkpoint recovery, exact logical-deadline enforcement, candidate
metadata/aggregate influence qualification, restoration suppression, every
account component's final proof and disposable-account acceptance. A feature
branch, mocked marker or passing synthetic test does not satisfy these gates.

Infrastructure needs read/transaction checks on INVENTORY#env without mutation,
CONTRIB# locator/tombstone writes, CANDIDATE# lifecycle ConditionCheck for capped
cluster writes, and the exact command guard/paired cleanup permissions already
listed in the prior handoff. Runtime pins remain empty/zero and consumers disabled.

## Validation

Python 3.14, isolated actual boto3 SDK plus Moto (no live AWS/KMS): 37 tests across
`tests/shared_campaign_locators/test_dynamodb.py` and the migrated deletion
progress suite. Cases include atomic producer/locator writes, repeated/capped
submissions, default-disabled/missing/changed inventory, tombstone and publication
races, locator survival after target TTL, exact sibling ownership, no eventual
index query, multi-invocation cleanup/recompute, lost acknowledgments, changed
commands, overlapping operations, legacy tombstone refusal, unchanged overdue
deadline and blocked retirement. Ordinary suites: 15 cluster, 23 publisher and
6 deletion parser/closed-completion tests. Compile and whitespace checks pass.

Source-only ZIP packaging includes shared_campaign_locators for all four campaign
workers; this is not a deployable-dependency, IAM or runtime qualification claim.
