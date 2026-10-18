# Selected Dev receipt calculation under the approved lifecycle

The compatible minimum is a **linkable, nonpersistent selected Dev calculation**
over existing approved receipt-local captures. SEC178 policy
`1.0.0-policy.1` already supplies their maximum seven-day linkage ceiling.
No new owner retention decision is needed for this use. A random generation UUID
does not make the selected population or report unlinkable.

`aggregate_selected_captures` is a small entry in the existing reducer. It accepts
only bounded existing capture snapshots, their existing closed selected scope,
a Chicago report date and an explicit observation time. It validates captures
with the existing shared validator, refuses foreign environments, future facts,
expired linkage and conflicting reference metadata, then calls the existing
reducer. No new summary schema, receipt field, account map, locator, table,
permission, configuration, scheduler, endpoint or delivery component is added.

## Exact data, duration and destination

The data is the existing capture: its closed reference metadata and at most four
accepted/terminal/internal-attempt events. Provider facts remain absent unless
actually present in that approved capture; no provider calls, cost, usage,
request-rejection totals or local intake facts are invented. Internal attempts
are not provider cost observations. Empty rejection maps mean no rejection
facts were supplied, not proof of zero actual rejections.

Each input must be younger than its original `expiresAtEpoch`, which the existing
validator limits to acceptance plus seven days and which may be shorter because
of the owning receipt's lifecycle. The existing conservative day-cohort correction
boundary also remains. No read, replay or calculation remints references or
extends either deadline. Exact same-source replay and monotonic settlement
enrichment are deduplicated by the existing reducer; conflicting snapshots abort.

The destination is this calculation's process memory only. No output is written,
queued, logged, delivered or given a ninety-day revision. The result reuses the
existing `selected_dev` / `selected_subset_not_environment_total` descriptor and
unqualified coverage labels. It must be described to an operator as a linkable
selected-test subset, never environment totals or anonymous data. Existing
`AggregateStore` selected read/commit refusal is unchanged and tested.

This function does not impose an output disposal mechanism on arbitrary Python
callers. The returned plain summary has no independent TTL. A future caller must
discard/recompute transient output under every contributing original deadline
and deletion boundary; it must not retain or copy the selected result into the
ninety-day aggregate store, logs, evidence archives or another destination.
No output persistence is qualified or authorized by this source increment.

## Trust, deletion and execution limits

This is calculation, not account selection, source discovery or authentication.
Captures carry no account identifier with which to establish one selected owner.
Trusted future wiring must select current snapshots through the existing receipt
proof, source/inventory, active-account and deletion fences. A supplied historical
capture and `as_of_epoch` are not evidence of a fresh clock, undeleted account or
complete population. There is no atomic multi-receipt snapshot claim.

The existing ingestion/retirement and account deletion components continue owning
receipt-local persistence, expiry and erasure. The helper neither adds a copy nor
changes those components. Local Moto tests exercise actual guarded capture,
settlement/attempt accounting and the existing retirement deadline, verifying
that the calculation adds no source-table write or charge. These are component
tests, not AWS, actor, backup/restore, physical-deletion or runtime evidence.

SEC237 stays In Progress. Real discovery, stable population provenance, current
deletion/clock qualification, execution/scheduling, complete provider cost facts
and target report/delivery acceptance remain open. No runtime or gate changes
are requested. The actor-reference join is already complete and was not repeated.
SEC198's separate owner policy question remains held.

## SEC226 remaining acceptance and next independent source defect

PR149 fixed invalid boolean request versions in the existing legacy History API;
it did not complete SEC226. The legacy source already contains the core routes,
receipt/generation changes and erasure jobs. The following are remaining
end-to-end acceptance and integration criteria, not a claim that every underlying
component is absent:

- The versioned governed mutation/recognition capability and migration contract.
  The current governed contract is GET-only. A flag cannot turn its seven-day
  receipt reader into the legacy ninety-day or destructive authority; there is
  no automatic backfill or new retention approval.
- Complete principal/binding, idempotent operation receipts and concurrent
  delete-one/clear/reset behavior. Clear must exclude content immediately while
  preserving independent badges; reset must exclude pre-reset or delayed work.
- All-copy deletion and expiry, minimal dedup/tombstone lifetimes, bounded purge,
  restore-erasure reconciliation and old-event non-resurrection, with the
  contract/privacy prerequisites finalized rather than silently assumed.
- Durable full-account deletion/write-fence and export inventory integration,
  plus actual target-environment and mobile invalidation acceptance. History-only
  deletion is not full product-account deletion. Physical cases retain ATCR-148.

The next independently confirmed existing-contract defect is in
`HistoryMutationService._transact`: it maps every `TransactionCanceledException`
to `CONFLICT` (409, “History state changed”), including the SDK-documented
`ProvisionedThroughputExceeded` cancellation reason. A synthetic SDK-shaped
source probe confirms this mapping with no service call. A subsequent bounded
fix should distinguish conditional state races from service/unknown cancellation
reasons using the existing error contract; it requires no new product policy.
It is identified, not implemented or attributed to a live incident in this batch.

The companion JSON and independent reviews retain exact validation, source pins
and limitations. No actor/SSO/AWS/service/emulator/provider operation, storage
creation, activation, deployment or main promotion was performed.
