# Authoritative period work and retirement candidate

SECUR4ALL-207 remains in progress. This source establishes strict, default-disabled
work accounting and proof-bound key retirement. It does not create production
approval markers, initialize controls, enable research, or approve historical
coverage. The separate withdrawal fixture exercises real producer/cleanup calls
with injected queue delivery; it does not qualify native queue redrive.

Each qualified writer transaction allocates a fixed-width ordinal under an exact
period control CAS and writes both `PERIOD_WORK#n/WORK#ordinal` and a deterministic,
domain-separated `WORK_LOOKUP#digest/RECORD`. The lookup is verified against the
work row's exact target identity. These records are paired discovery metadata,
not anonymous logs: their deadline is the target's original logical deadline.
Shortening is atomic across the target and both records; extension is refused.
Cleanup atomically deletes the exact target, both records and decrements the
count once. A target removed by TTL is reconciled through the remaining work
record. There is no independent work-record TTL or automatic legacy adoption.

`PERIOD_WORK_CONTROL#n/STATE` must already exist under separately qualified
bootstrap. Ordinary writers cannot initialize missing controls. Its monotonic
ordinal, count and pass fields are strict and bounded. Work schema version 1
requires admission registry schema version 2 with work manifest/revision pins;
old registry shapes cannot silently admit modern writes. The exact pipeline and
outbox TableIds are checked against runtime pins and the externally approved
work manifest. A restored table requires a new qualified generation.

The retirement helper requires its own `CAMPAIGN_PERIOD_RETIREMENT_ENABLED=true`
before any SDK call. It requires an exact SEALED registry, zero authoritative
control count, a strongly empty entire work partition, current table identities
and the exact work marker. The intent is durably recorded before disabling the
exact tagged HMAC key. Only DescribeKey, ListResourceTags, DisableKey and
ScheduleKeyDeletion with seven days are used. Missing proof/key, wrong metadata,
changed generation, unsupported shapes or an exhausted SDK budget refuse work.
Ambiguous acknowledgments are reconciled from fresh persisted/provider state;
no EnableKey, CancelKeyDeletion, key creation or automatic compensation exists.
A scheduled deletion is not destruction. A later NotFound is acknowledged only
with the stored scheduled proof and after its recorded deletion date.

The isolated retirement fixture uses two independently provisioned, run-specific
empty tables and one separately described/tagged HMAC key. Its SEALED marker and
control are synthetic assumptions, not evidence that a production period was
erased. Local tests use real SDK/Moto transactions and injected KMS responses.
Root owns any separately reviewed AWS execution and cleanup.

## Integrated lifecycle

All new transient deadlines are capped at the earlier original bound and period
end plus seven days. Repeated submissions preserve the existing contribution and
locator deadline. The scheduled `reconcile_periods` operation selects two periods
per invocation, with at most eight work attempts and sixteen queried records per
period. It advances an exact numeric ordinal before an attempt, preserving poison
records/counts while allowing later canonical ordinals to proceed. Per-call SDK
budget checks stop new calls below six seconds. Missing controls, unknown rows,
changed pins and exhausted budgets never certify completion.

OPEN transitions to CLOSING after period end. Under the qualified writer boundary,
new inputs are then forbidden while bounded publication and account cleanup can
continue. Publication starts after period end and must use still-live input before
the fixed recovery deadline. This resolves the former implementation's conflicting
wait-until-recovery-end rule without extending any deadline. Every included
contributor is read; modern publication captures the period counter before those
reads and folds its exact comparison into the final counter update. All qualified
tombstone/repair writes change that counter, so a concurrent deletion invalidates
publication without truncating a cohort to fit DynamoDB's 100-action limit.
The existing five-page/500-row contribution bound remains fail-closed; an oversized
or unknown candidate is not partially published and remains observable until its
bounded privacy drain. Anonymous publication is not guaranteed for invalid input.

At recovery end, DRAINING forbids allocations and permits exact paired deletes.
Earlier due work is erased without waiting for period end when family invariants
allow it. A retained tombstone or repair fence is never discarded merely because
an index is empty. SEALED requires the qualified control count to be zero and a
strongly empty entire work partition, then atomically checks the registry, control,
manifest and resource identities. The full-pass clock means bounded traversal of
the fixed high-water mark; poison can leave that pass visited but unsealed.
`PrivacyDeadlineMissed` reports observed overdue work; silence is not purge proof.
Progress age is zero for an empty period and otherwise uses actual progress or the
validated admission timestamp as its initial baseline.

A separate contiguous retired prefix advances only through exact scheduled-key
retirement proof. Account/withdrawal cleanup and completion consume these proofs
before any MAC derivation, including a retired request-period anchor. The global
work minimum may precede the immutable account-locator minimum: account traversal
starts at the maximum of its locator minimum and the qualified retired prefix.
This does not expand historical account scope or rewrite old locator approval.

## Outbox, aggregate and export compatibility

Modern outbox locator schema 2 stores `logicalExpiresAt` at exactly its former
original deadline and omits independent TTL. Event, locator and paired indexes
are explicitly erased together. Account-outbox receipts follow actual removal;
export validates modern locator shape and exact resource pins, and omits logically
expired metadata. Old unindexed records cannot be silently adopted. The selected
legacy conversation-analysis role remains write-denied; source compatibility is
not authorization to enable that retired producer.

Anonymous aggregates retain their original 400-day deadline. Publication adds a
16-shard `expiryPartition`; reviewer audit rows copy that partition and the same
unchanged deadline. Review refuses logically expired aggregates. The independent
`reconcile_aggregates` event prevents period work from consuming the aggregate
purge budget. It discovers at most two pages of sixteen keys via the intelligence
ExpirationIndex, strongly rereads each exact row and conditionally deletes it.
GSI discovery and a completed index pass are not proof of full physical erasure.

The `AGGREGATE_SWEEP#environment/STATE` cursor contains only an anonymous public
campaign UUID/expiry/index partition, never an account, token, payload or audit
reason. Its logical lifetime is 24 hours within the approved 400-day operational
maximum; completed passes reset the key. Expired continuations discard the key
and advance the numeric shard, preventing early-shard poison from starving all
later shards. Such a pass is marked incomplete and cannot refresh the full-pass
clock. A shard larger than one lifetime can require repeated traversals; this
bounded mechanism makes no universal coverage claim for unlimited poison. Unknown
keys fail closed; unverified/failure/freshness metrics surface obstruction. The
intelligence TableId is independently pinned and checked before reads/deletes.

## Bootstrap and qualification boundary

`shared_campaign_work.bootstrap.actions` is a pure planner, not an approval or
execution tool. An independently reviewed operator must strongly classify the
entire closed/drained resource snapshot, bind its hashes, TableIds, source/runtime
pins and immutable external approval, and perform one conditional transaction.
The planner accepts up to eight exact enabled registries and sixteen completed
retained tombstones. It preserves all original target state/deadlines, creates
absent-only control/work/lookup records and checks every observed target. Unknown
or in-flight repair state refuses migration. Exact legacy six-field enabled
registry metadata can receive reviewed admission fields, starting OPEN or CLOSING
according to its actual period; it is never initialized SEALED or RETIRED. A prior
period still inside its recovery window remains ineligible for draining/retirement.

Global inventory approval must cover all indexed writer and cleaner sources plus
review-audit/export compatibility and current intelligence contents. No runtime
path writes the approval marker. New-period initialization remains the separate
create-only reviewed operator with an independently provisioned/tagged key, now
atomically creating its empty control under the existing work approval. Missing
future period provisioning is an operational failure, not automatic key creation.

The five-table `period_lifecycle.py` fixture composes actual publisher/cluster,
tracked writes, deadline/drain/seal, retired-proof account and withdrawal completion,
and aggregate expiry. It uses synthetic inventories, clocks and commands, captured
queue/metric transport and no native delivery claim. The two-table retirement
fixture separately starts with synthetic sealed proof to qualify actual KMS state
transitions. Neither fixture approves historical or live resource coverage.

No local helper or zero counter establishes story completion. Final source review,
immutable artifacts, effective IAM, separately approved bootstrap, deployed worker
acceptance and monitoring evidence remain distinct. SECUR4ALL-330 owns later
release/UAT evidence; SECUR4ALL-245 owns native restore/reopening qualification.

## Local integrated validation

Before the integrated freeze, the shared-work SDK/Moto suite passed 137 tests.
Affected existing suites passed separately: account-data 68 tests plus 56 subtests
and its isolated six producer-retry cases; export 63; bridge 349 plus nine subtests;
shared locators 78; lifecycle 63; publisher 23 plus 15 subtests; cluster 20 plus
13 subtests; conversation-analysis 119 plus 16 subtests; review six plus five
subtests; existing period initializer 30. Separate processes are needed for the
repository's older unqualified `service` test imports. The SDK environment lacks
jsonschema, so the existing OpenAPI test environment ran conversation-analysis.
The new publication fixture privately scopes its lifecycle service alias so it
also passes in the combined shared-work suite. These are local results; deployed
runtime, cloud IAM and actual key transitions have separately pinned evidence.


## Operator sequence and delivery evidence

The [frozen artifact and AWS qualification record](evidence/campaign-period-lifecycle-2026-09-27/README.md)
separates local SDK/Moto, local archive checks, actual AWS SDK operations and
actual disposable Lambda execution. It preserves the source pin used by each.

1. Close and drain every relevant writer/cleaner/reviewer before the bootstrap
   snapshot. Bind each actual installed artifact and effective role, including
   retired writers whose explicit write deny remains in force. The new analysis
   archive is prepared compatibility only; it need not replace the retired
   installed code to preserve an independently verified no-write boundary.
2. Strongly classify the complete attached pipeline/outbox/intelligence resources
   using the reviewed operator. Bind exact TableIds, all original target clocks,
   enabled registries and the immutable locator approval. Refuse unknown or
   in-flight records. An empty query, a new account or source compatibility alone
   cannot substitute for this inventory review.
3. Review the immutable bootstrap plan/manifest and source proof separately.
   Execute only the exact conditional bootstrap transaction. After an ambiguous
   result, use exact readback rather than retrying a different plan or recreating
   controls. Preserve the earlier global work minimum where it covers an enabled
   period still inside its recovery window; do not manufacture an early seal.
4. Install the reviewed compatible artifacts and resource/IAM pins together while
   work/lifecycle/retirement remain closed. Recheck code hashes, table identities,
   generation, approval revisions, actual role boundaries, review/export source
   compatibility and the two separate schedule targets.
5. Activate only the independently reviewed Dev scope. The period event is
   `{"schemaVersion":1,"environment":"dev","operation":"reconcile_periods"}`;
   the independent aggregate event replaces the operation with
   `reconcile_aggregates`. Retirement has its own gate and may proceed only from
   genuine current SEALED proof after the fixed recovery deadline. Ordinary
   writer admission and research delivery are not implied by cleanup activation.
6. Record actual worker results and emitted heartbeat, failure, unverified,
   overdue, backlog and freshness metrics. A success heartbeat is not a seal or
   erasure receipt. Investigate poison/missing-period/budget failures using fixed
   categories; never clear an authoritative count or discard evidence to silence
   an alarm. Provision future periods through the reviewed create-only operator
   with independently supplied keys before they are needed.
7. Verify fixture cleanup and key state independently. Treat PendingDeletion as
   scheduled removal; actual destruction requires later qualified NotFound
   observation. Capture exact release integration/deployment references and keep
   SECUR4ALL-330 release execution and SECUR4ALL-245 restore work distinct.

This sequence documents required evidence; it does not supply approval, enable a
flag, execute an operator or authorize a general research rollout.
